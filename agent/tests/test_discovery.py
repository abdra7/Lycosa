"""mDNS advertising (Ticket #103): correct service shape, resilient start."""

import socket

from lycosa_agent import __version__, discovery
from lycosa_agent.discovery import (
    SERVICE_TYPE,
    DiscoveryAdvertiser,
    build_service_info,
    instance_name,
)


def test_instance_name_replaces_dots():
    assert instance_name("my.host.local") == "my-host-local"


def test_instance_name_never_empty():
    assert instance_name("...") == "lycosa-agent"


def test_instance_name_respects_dns_label_limit():
    assert len(instance_name("x" * 100)) == 63


def test_service_info_shape():
    info = build_service_info("gpu-box", "192.168.1.20", 8010)

    assert info.type == SERVICE_TYPE
    assert info.name == f"gpu-box.{SERVICE_TYPE}"
    assert info.port == 8010
    assert socket.inet_ntoa(info.addresses[0]) == "192.168.1.20"
    properties = {k.decode(): v.decode() for k, v in info.properties.items() if v is not None}
    assert properties == {"name": "gpu-box", "version": __version__}


async def test_advertiser_start_failure_does_not_raise(monkeypatch):
    """No multicast (VPN, locked-down network) must not kill the agent."""

    def boom(*args, **kwargs):
        raise OSError("multicast unavailable")

    monkeypatch.setattr(discovery, "AsyncZeroconf", boom)
    advertiser = DiscoveryAdvertiser("gpu-box", "192.168.1.20", 8010)

    await advertiser.start()  # must swallow the failure
    await advertiser.stop()  # and stop must be a no-op, not a crash
