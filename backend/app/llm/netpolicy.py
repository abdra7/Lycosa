"""Outbound endpoint policy for provider calls (SSRF defense).

Two layers:
1. `validate_base_url` checks the configured URL's shape (scheme, no userinfo,
   query or fragment) when an account is created or updated.
2. `GuardedBackend` re-checks every address at socket-connect time. The
   hostname is resolved once, every resolved address must satisfy the
   account's `AddressPolicy`, and the socket connects to that vetted address.
   A DNS answer that changes after validation (rebinding) is therefore caught.
   TLS still verifies the certificate against the original hostname.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpcore

from app.llm.errors import EndpointNotAllowedError, ProviderUnavailableError

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

# Never reachable from a provider call, whatever the account or policy:
# cloud metadata services, link-local, multicast, unspecified/broadcast.
ALWAYS_BLOCKED: tuple[IPNetwork, ...] = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "169.254.0.0/16",  # link-local, incl. 169.254.169.254 metadata
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "192.0.0.192/32",  # Oracle Cloud metadata
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "::/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",  # AWS IPv6 metadata
    )
)

DEFAULT_LOCAL_NETWORKS = (
    "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7,100.64.0.0/10"
)


def parse_networks(value: str | Iterable[str]) -> tuple[IPNetwork, ...]:
    items = value.split(",") if isinstance(value, str) else list(value)
    return tuple(ipaddress.ip_network(i.strip(), strict=False) for i in items if i.strip())


def _normalize(address: str) -> IPAddress:
    ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


@dataclass(frozen=True)
class AddressPolicy:
    """Which resolved addresses a connection may use.

    - public_ok: globally routable addresses are allowed
    - networks: non-global addresses are allowed only inside these networks
    """

    public_ok: bool = True
    networks: tuple[IPNetwork, ...] = ()
    label: str = "public"

    def permits(self, address: str) -> bool:
        try:
            ip = _normalize(address)
        except ValueError:
            return False
        if any(ip in net for net in ALWAYS_BLOCKED if ip.version == net.version):
            return False
        if ip.is_global:
            return self.public_ok
        return any(ip in net for net in self.networks if ip.version == net.version)


PUBLIC_ONLY = AddressPolicy(public_ok=True, networks=(), label="public")


def local_policy(networks: tuple[IPNetwork, ...]) -> AddressPolicy:
    """Local runtimes: public addresses plus the allowed private networks."""
    return AddressPolicy(public_ok=True, networks=networks, label="local")


def private_only_policy(networks: tuple[IPNetwork, ...]) -> AddressPolicy:
    """Privacy-required requests: the prompt must not leave the allowed
    private networks, even if DNS later points the host elsewhere."""
    return AddressPolicy(public_ok=False, networks=networks, label="private")


def validate_base_url(url: str, *, allow_http: bool) -> str:
    """Shape check for a configured endpoint; returns it without a trailing slash."""
    if not isinstance(url, str) or len(url) > 255 or any(ord(c) < 33 for c in url):
        raise EndpointNotAllowedError("Endpoint URL is invalid")
    parts = urlsplit(url)
    schemes = {"https", "http"} if allow_http else {"https"}
    if parts.scheme not in schemes:
        raise EndpointNotAllowedError(
            "Endpoint must use HTTPS" if not allow_http else "Endpoint must use HTTP or HTTPS"
        )
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise EndpointNotAllowedError(
            "Endpoint must be a plain URL without credentials, query or fragment"
        )
    try:
        parts.port  # noqa: B018 — raises on an out-of-range port
    except ValueError:
        raise EndpointNotAllowedError("Endpoint port is invalid") from None
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def is_literal_private(url: str) -> bool:
    """True when the URL's host is an IP literal or name that is local by
    construction (loopback name). Used for display/locality hints only; the
    connect-time guard is what enforces policy."""
    host = urlsplit(url).hostname or ""
    if host in {"localhost", "host.docker.internal"}:
        return True
    try:
        return not _normalize(host).is_global
    except ValueError:
        return False


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def resolve_checked(
    host: str, port: int, policy: AddressPolicy, resolver: Resolver | None = None
) -> list[str]:
    try:
        addresses = [str(_normalize(host))]
    except ValueError:
        try:
            addresses = await (resolver or system_resolver)(host, port)
        except OSError:
            raise ProviderUnavailableError("Provider host could not be resolved") from None
    if not addresses or not all(policy.permits(a) for a in addresses):
        raise EndpointNotAllowedError()
    return addresses


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that only opens sockets to vetted addresses."""

    def __init__(self, policy: AddressPolicy, resolver: Resolver | None = None) -> None:
        self._policy = policy
        self._resolver = resolver
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 — httpcore's backend signature
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await resolve_checked(host, port, self._policy, self._resolver)
        last_error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address, port, timeout, local_address, socket_options
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, *args, **kwargs) -> httpcore.AsyncNetworkStream:
        raise EndpointNotAllowedError()

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)
