"""LAN presence: advertise this agent over mDNS/DNS-SD (Ticket #103).

The desktop dashboard (a native app on the operator's LAN) browses for
`_lycosa-agent._tcp.local.` to surface devices that run an agent but are not
yet registered. The controller cannot do this itself: it lives in a
bridge-network Docker container that LAN multicast never reaches.

Advertising is best-effort — a machine without working multicast (locked-down
Wi-Fi, some VPNs, blocked UDP 5353) still registers and executes tasks
normally; it just won't show up in the dashboard's LAN scan.
"""

import logging
import socket

from zeroconf import IPVersion, ServiceInfo
from zeroconf.asyncio import AsyncZeroconf

from lycosa_agent import __version__

logger = logging.getLogger("lycosa.agent.discovery")

SERVICE_TYPE = "_lycosa-agent._tcp.local."
_MAX_LABEL_LENGTH = 63  # DNS label limit


def instance_name(node_name: str) -> str:
    """DNS-SD instance label: dots would be parsed as label separators."""
    cleaned = node_name.replace(".", "-").strip("-") or "lycosa-agent"
    return cleaned[:_MAX_LABEL_LENGTH]


def build_service_info(node_name: str, ip: str, port: int) -> ServiceInfo:
    instance = instance_name(node_name)
    return ServiceInfo(
        SERVICE_TYPE,
        f"{instance}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties={"name": node_name, "version": __version__},
        server=f"{instance}.local.",
    )


class DiscoveryAdvertiser:
    """Registers/unregisters the mDNS service; failures never crash the agent."""

    def __init__(self, node_name: str, ip: str, port: int) -> None:
        self._info = build_service_info(node_name, ip, port)
        self._zeroconf: AsyncZeroconf | None = None

    async def start(self) -> None:
        try:
            self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
            await self._zeroconf.async_register_service(self._info)
            logger.info("advertising %s over mDNS", self._info.name)
        except Exception as exc:  # noqa: BLE001 — discovery is best-effort
            logger.warning("mDNS advertising unavailable, continuing without it: %s", exc)
            self._zeroconf = None

    async def stop(self) -> None:
        if self._zeroconf is None:
            return
        try:
            await self._zeroconf.async_unregister_service(self._info)
            await self._zeroconf.async_close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("mDNS shutdown error: %s", exc)
        self._zeroconf = None
