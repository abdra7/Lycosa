"""Client-IP resolution with trusted-proxy X-Forwarded-For support (ADR-028).

The rate limiter (ADR-020) and login guard (ADR-023) key on client IP. Behind
a reverse proxy the direct peer is the proxy, so every caller would share one
bucket — but honoring X-Forwarded-For unconditionally re-opens the F-2 bypass
(the header is client-controlled). Resolution rule:

- TRUSTED_PROXIES unset (default): the direct peer IP, header ignored.
- Peer not in TRUSTED_PROXIES: the direct peer IP, header ignored.
- Peer trusted: the RIGHTMOST X-Forwarded-For entry that is not itself a
  trusted proxy. A conforming proxy appends the real client last, so
  attacker-prepended entries are never reached; trusted intermediate hops in
  a proxy chain are skipped.
"""

import logging
from ipaddress import ip_address, ip_network

from starlette.requests import Request

from app.core.config import get_settings

logger = logging.getLogger("lycosa.clientip")


def _trusted_networks(raw: str):
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ip_network(part, strict=False))
        except ValueError:
            logger.warning("ignoring invalid TRUSTED_PROXIES entry: %r", part)
    return networks


def _is_trusted(ip: str, networks) -> bool:
    try:
        address = ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip(request: Request) -> str | None:
    peer = request.client.host if request.client else None
    raw = get_settings().trusted_proxies
    if not raw or peer is None:
        return peer
    networks = _trusted_networks(raw)
    if not _is_trusted(peer, networks):
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, networks):
            return hop
    # every hop was our own infrastructure (or the header was absent)
    return hops[0] if hops else peer
