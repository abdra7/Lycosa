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
    adapter: str = "native"


PROVIDERS = {
    "ollama": Provider("ollama", "local", "local"),
    "anthropic": Provider("anthropic", "cloud", "external"),
    "openrouter": Provider("openrouter", "cloud", "external"),
}

# Built-in names are discoverable; dispatch still requires a configured profile.
SDK_PROVIDERS = (
    "openai",
    "gemini",
    "azure",
    "bedrock",
    "vertex_ai",
    "groq",
    "mistral",
    "cohere",
    "deepseek",
    "xai",
    "together_ai",
    "fireworks_ai",
    "perplexity",
    "cerebras",
    "sambanova",
    "nvidia_nim",
    "huggingface",
    "replicate",
    "ai21",
    "databricks",
    "watsonx",
)
PROVIDERS.update(
    {name: Provider(name, "cloud", "external", adapter="litellm") for name in SDK_PROVIDERS}
)


def registry() -> dict[str, Provider]:
    import re

    from app.core.config import get_settings

    result = dict(PROVIDERS)
    for name in get_settings().provider_profiles:
        if name in {"ollama", "anthropic", "openrouter", "phantom_local"}:
            raise ValueError("Native provider policies cannot be overridden")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", name):
            raise ValueError("Invalid provider alias")
        result[name] = Provider(name, "cloud", "external", adapter="litellm")
    return result


def validate_provider(value: str) -> str:
    if value not in registry():
        raise ValueError("Unknown provider")
    return value
