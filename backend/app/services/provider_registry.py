"""Provider metadata is policy; no provider payload translation belongs here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    execution_mode: str
    privacy: str
    tools: bool = False
    streaming: bool = False
    vision: bool = False
    structured_output: bool = False
    context_window: int | None = None
    cost_metadata: dict | None = None


PROVIDERS = {
    "ollama": Provider("ollama", "local", "local"),
    "anthropic": Provider("anthropic", "cloud", "external"),
}
