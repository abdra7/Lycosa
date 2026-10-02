"""Agent exec-API base URL validation (ADR-030).

A node reports its own agent_url, and the controller POSTs task prompts and
the node's token to it. The value must therefore be a bare http(s) origin:
userinfo, a path, a query or a fragment could retarget the route the
controller appends (`/execute`, `/models/pull`) onto another service.
"""

from urllib.parse import urlsplit


class InvalidAgentUrl(ValueError):
    pass


def normalize_agent_url(value: str) -> str:
    """Return `scheme://host[:port]` for a valid agent base URL, else raise."""
    try:
        parts = urlsplit(value.strip())
        port = parts.port  # raises on a non-numeric or out-of-range port
    except ValueError as exc:
        raise InvalidAgentUrl("agent_url has an invalid host or port") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidAgentUrl("agent_url must be an http(s) URL with a host")
    if parts.username is not None or parts.password is not None:
        raise InvalidAgentUrl("agent_url must not carry credentials")
    if parts.path not in ("", "/") or "?" in value or "#" in value:
        raise InvalidAgentUrl("agent_url must be a bare origin (no path, query or fragment)")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def is_valid_agent_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        normalize_agent_url(value)
    except InvalidAgentUrl:
        return False
    return True
