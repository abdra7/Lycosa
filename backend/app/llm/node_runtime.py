"""Node-run providers (ADR-032): calls that execute on a Lycosa node under
the node owner's own sign-in, such as the Claude subscription runtime.

The controller holds no provider credential for these. It reaches the node
agent with the node's agent token, as task dispatch does, and names the
Lycosa user the call is for. The agent serves only the user its machine's
owner configured, so a binding to someone else's node cannot use their plan.
"""

from typing import Any

from app.core.agenturl import InvalidAgentUrl, normalize_agent_url
from app.core.config import get_settings
from app.llm.errors import NodeUnavailableError
from app.llm.http import Connection
from app.llm.netpolicy import AddressPolicy, local_policy, parse_networks, private_only_policy
from app.models.node import Node

AGENT_TOKEN_HEADER = "X-Agent-Token"
OWNER_HEADER = "X-Lycosa-Owner"
CLAUDE_CAPABILITY = "claude_subscription"


def capabilities_of(node: Node) -> dict[str, Any]:
    """The agent_capabilities the node reported with its hardware profile."""
    profile = node.hardware_profile if isinstance(node.hardware_profile, dict) else {}
    extra = profile.get("extra")
    caps = extra.get("agent_capabilities") if isinstance(extra, dict) else None
    return caps if isinstance(caps, dict) else {}


def offers_provider(node: Node, provider: str) -> bool:
    """A node-run provider is offered when the agent reports a capability of
    the same name (e.g. claude_subscription)."""
    return capabilities_of(node).get(provider) is True


def offers_claude(node: Node) -> bool:
    return offers_provider(node, CLAUDE_CAPABILITY)


def node_policy(*, private_only: bool = False) -> AddressPolicy:
    """Agents are registered by nodes holding a node key, not typed in by a
    user, so they get the administrator's local networks."""
    networks = parse_networks(get_settings().llm_local_networks)
    return private_only_policy(networks) if private_only else local_policy(networks)


def node_connection(
    node: Node | None, *, provider: str, owner_email: str, timeout: float
) -> Connection:
    if node is None or not node.agent_url or not node.agent_token:
        raise NodeUnavailableError(provider=provider)
    try:
        base_url = normalize_agent_url(node.agent_url)
    except InvalidAgentUrl:
        raise NodeUnavailableError(provider=provider) from None
    return Connection(
        provider=provider,
        base_url=base_url,
        credential=node.agent_token,
        policy=node_policy(),
        timeout=timeout,
        headers={OWNER_HEADER: owner_email},
    )
