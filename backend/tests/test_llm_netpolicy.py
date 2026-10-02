"""SSRF defenses for provider endpoints: URL shape, connect-time address
checks (incl. DNS rebinding), no redirects, no proxies, size caps."""

import httpcore
import httpx
import pytest
import respx

from app.llm import errors
from app.llm.http import Connection, build_client, request_json, stream_lines
from app.llm.netpolicy import (
    PUBLIC_ONLY,
    GuardedBackend,
    local_policy,
    parse_networks,
    private_only_policy,
    resolve_checked,
    validate_base_url,
)

LAN = parse_networks("127.0.0.0/8,10.0.0.0/8,192.168.0.0/16,::1/128")


@pytest.mark.parametrize(
    "address",
    [
        "169.254.169.254",  # cloud metadata
        "100.100.100.200",  # alibaba metadata
        "fd00:ec2::254",  # aws ipv6 metadata
        "::ffff:169.254.169.254",  # ipv4-mapped metadata
        "0.0.0.0",
        "224.0.0.1",
        "fe80::1",
        "not-an-ip",
    ],
)
def test_always_blocked_addresses(address):
    for policy in (PUBLIC_ONLY, local_policy(LAN), private_only_policy(LAN)):
        assert not policy.permits(address)


def test_public_only_policy_refuses_private_and_loopback():
    assert PUBLIC_ONLY.permits("8.8.8.8")
    for address in ("127.0.0.1", "10.0.0.5", "192.168.1.2", "::1", "172.17.0.1"):
        assert not PUBLIC_ONLY.permits(address)


def test_local_policy_allows_only_listed_private_networks():
    policy = local_policy(LAN)
    assert policy.permits("192.168.1.20") and policy.permits("127.0.0.1")
    assert policy.permits("8.8.8.8")  # a public host is not an internal-network risk
    assert not policy.permits("172.17.0.1")  # private, but not in the allowlist


def test_private_only_policy_refuses_public_addresses():
    policy = private_only_policy(LAN)
    assert policy.permits("10.1.2.3")
    assert not policy.permits("8.8.8.8")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "file:///etc/passwd",
        "https://user:pw@host",
        "https://host/v1?x=1",
        "https://host/v1#frag",
        "https://host:99999",
        "https:///nohost",
        "https://host/ v1",
    ],
)
def test_base_url_shape_is_validated(url):
    with pytest.raises(errors.EndpointNotAllowedError):
        validate_base_url(url, allow_http=True)


def test_base_url_scheme_rules_and_normalization():
    with pytest.raises(errors.EndpointNotAllowedError):
        validate_base_url("http://api.example.com/v1", allow_http=False)
    assert validate_base_url("http://192.168.1.5:11434/", allow_http=True) == (
        "http://192.168.1.5:11434"
    )
    assert validate_base_url("https://api.example.com/v1/", allow_http=False) == (
        "https://api.example.com/v1"
    )


def _resolver(mapping):
    async def resolve(host, port):
        return mapping[host]

    return resolve


async def test_resolution_must_be_entirely_permitted():
    # one bad address in the answer poisons the whole answer
    resolver = _resolver({"mixed.example": ["8.8.8.8", "10.0.0.1"]})
    with pytest.raises(errors.EndpointNotAllowedError):
        await resolve_checked("mixed.example", 443, PUBLIC_ONLY, resolver)
    resolver = _resolver({"ok.example": ["8.8.8.8"]})
    assert await resolve_checked("ok.example", 443, PUBLIC_ONLY, resolver) == ["8.8.8.8"]


async def test_ip_literal_hosts_are_checked_without_dns():
    async def never(host, port):
        raise AssertionError("must not resolve a literal")

    with pytest.raises(errors.EndpointNotAllowedError):
        await resolve_checked("169.254.169.254", 80, local_policy(LAN), never)
    assert await resolve_checked("192.168.1.9", 80, local_policy(LAN), never) == ["192.168.1.9"]


async def test_guarded_backend_connects_to_the_vetted_address_only(monkeypatch):
    """DNS rebinding: the backend never re-resolves; it dials the IP it vetted."""
    dialed = []

    class FakeInner:
        async def connect_tcp(self, host, port, *args):
            dialed.append(host)
            return "stream"

    backend = GuardedBackend(PUBLIC_ONLY, _resolver({"api.example": ["93.184.216.34"]}))
    backend._inner = FakeInner()
    assert await backend.connect_tcp("api.example", 443) == "stream"
    assert dialed == ["93.184.216.34"]

    rebound = GuardedBackend(PUBLIC_ONLY, _resolver({"api.example": ["127.0.0.1"]}))
    rebound._inner = FakeInner()
    with pytest.raises(errors.EndpointNotAllowedError):
        await rebound.connect_tcp("api.example", 443)
    assert dialed == ["93.184.216.34"]  # never dialed the rebound address


async def test_guarded_backend_refuses_unix_sockets():
    with pytest.raises(errors.EndpointNotAllowedError):
        await GuardedBackend(PUBLIC_ONLY).connect_unix_socket("/var/run/docker.sock")


def test_client_uses_the_guarded_pool_without_redirects_or_proxies():
    client = build_client(Connection(provider="p", base_url="https://api.example"))
    pool = client._transport._pool
    assert isinstance(pool, httpcore.AsyncConnectionPool)
    assert isinstance(pool._network_backend, GuardedBackend)
    assert client.follow_redirects is False
    assert client._trust_env is False


async def test_real_connection_to_metadata_address_is_refused_before_any_socket():
    conn = Connection(provider="p", base_url="http://169.254.169.254", policy=local_policy(LAN))
    with pytest.raises(errors.EndpointNotAllowedError):
        await request_json(conn, "GET", "/latest/meta-data/")


@respx.mock
async def test_redirects_are_not_followed():
    respx.get("https://api.example/models").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    )
    conn = Connection(provider="p", base_url="https://api.example")
    with pytest.raises(errors.LLMError) as excinfo:
        await request_json(conn, "GET", "/models")
    assert "redirect" in excinfo.value.public_message


@respx.mock
async def test_response_size_is_capped():
    respx.get("https://api.example/models").mock(
        return_value=httpx.Response(200, content=b"x" * 2048)
    )
    conn = Connection(provider="p", base_url="https://api.example")
    with pytest.raises(errors.ResponseTooLargeError):
        await request_json(conn, "GET", "/models", max_bytes=1024)


@respx.mock
async def test_error_bodies_classify_but_never_surface():
    respx.get("https://api.example/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "key sk-SECRET invalid"}})
    )
    conn = Connection(provider="p", base_url="https://api.example", credential="sk-SECRET")
    with pytest.raises(errors.AuthenticationError) as excinfo:
        await request_json(conn, "GET", "/models")
    assert "sk-SECRET" not in str(excinfo.value)


@respx.mock
async def test_transport_failures_are_normalized():
    respx.get("https://api.example/a").mock(side_effect=httpx.ConnectError("boom"))
    respx.get("https://api.example/b").mock(side_effect=httpx.ReadTimeout("slow"))
    conn = Connection(provider="p", base_url="https://api.example")
    with pytest.raises(errors.ProviderUnavailableError):
        await request_json(conn, "GET", "/a")
    with pytest.raises(errors.LLMTimeoutError):
        await request_json(conn, "GET", "/b")


@respx.mock
async def test_stream_lines_caps_a_line_without_newline(monkeypatch):
    from app.llm import http

    monkeypatch.setattr(http, "MAX_LINE_BYTES", 64)
    respx.post("https://api.example/s").mock(return_value=httpx.Response(200, content=b"a" * 500))
    conn = Connection(provider="p", base_url="https://api.example")
    with pytest.raises(errors.ResponseTooLargeError):
        async for _ in stream_lines(conn, "POST", "/s"):
            pass


def test_connection_repr_hides_the_credential():
    conn = Connection(provider="p", base_url="https://x", credential="sk-SECRET")
    assert "sk-SECRET" not in repr(conn)
