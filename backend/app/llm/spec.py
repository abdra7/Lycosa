"""Static provider descriptions: endpoints, auth methods, API-level capabilities."""

import enum
import re
from dataclasses import dataclass, field

from app.llm.types import LLMCapabilities


class ProviderKind(enum.StrEnum):
    CLOUD = "cloud"
    AGGREGATOR = "aggregator"
    LOCAL = "local"
    COMPATIBLE = "compatible"  # any OpenAI-compatible endpoint the user points at
    # runs on a Lycosa node under the node owner's own sign-in (ADR-032)
    NODE = "node"


class AuthMethod(enum.StrEnum):
    API_KEY = "api_key"
    OAUTH_PKCE = "oauth_pkce"  # official OAuth flow that yields an API key
    NONE = "none"  # local runtimes without authentication
    # the node's own sign-in (e.g. Claude Code's); Lycosa holds no credential
    NODE_SIGN_IN = "node_sign_in"


class BaseUrlMode(enum.StrEnum):
    FIXED = "fixed"  # the provider's official endpoint only
    OFFICIAL_CHOICE = "official_choice"  # one of the documented regional endpoints
    USER = "user"  # operator-supplied URL, subject to the network policy


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    kind: ProviderKind
    default_base_url: str
    base_url_mode: BaseUrlMode
    auth_methods: tuple[AuthMethod, ...]
    capabilities: LLMCapabilities
    docs_url: str
    official_base_urls: tuple[str, ...] = ()  # documented choices, for display
    official_base_url_patterns: tuple[str, ...] = ()  # regexes for OFFICIAL_CHOICE
    subscription_note: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def credential_required(self) -> bool:
        return not {AuthMethod.NONE, AuthMethod.NODE_SIGN_IN} & set(self.auth_methods)

    @property
    def node_bound(self) -> bool:
        """Runs on a Lycosa node; accounts are personal and bound to one node."""
        return self.kind == ProviderKind.NODE

    @property
    def allow_http(self) -> bool:
        """Plain HTTP only for runtimes that typically live on the LAN."""
        return self.kind in (ProviderKind.LOCAL, ProviderKind.COMPATIBLE, ProviderKind.NODE)

    def accepts_official_base_url(self, url: str) -> bool:
        return any(re.fullmatch(p, url) for p in self.official_base_url_patterns)
