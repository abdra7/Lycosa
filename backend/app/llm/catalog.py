"""Provider registry: one spec + adapter per supported provider.

Endpoints, auth methods and API-level capabilities were checked against each
provider's official documentation on 2026-10-02 (links in `docs_url`). A
capability is True only where the API documents it for the provider as a
whole; per-model support comes from model discovery. Model names are never
hard-coded here.
"""

from app.llm.adapters.anthropic import AnthropicAdapter
from app.llm.adapters.base import LLMAdapter
from app.llm.adapters.gemini import GeminiAdapter
from app.llm.adapters.ollama import OllamaAdapter
from app.llm.adapters.openai_compat import (
    DeepSeekAdapter,
    LMStudioAdapter,
    MistralAdapter,
    OpenAIAdapter,
    OpenAICompatibleAdapter,
    OpenRouterAdapter,
    QwenAdapter,
    VLLMAdapter,
    XAIAdapter,
)
from app.llm.spec import AuthMethod, BaseUrlMode, ProviderKind, ProviderSpec
from app.llm.types import LLMCapabilities

KEY = (AuthMethod.API_KEY,)
LOCAL_AUTH = (AuthMethod.NONE, AuthMethod.API_KEY)
# Lycosa adapters do not implement embeddings (the knowledge plane has its own
# embedder), so no provider claims that capability at the API level.
_NO_EMBED = {"embeddings": False}

SUBSCRIPTION_NOTE = (
    "{product} is a consumer subscription and does not include API access. "
    "Create an API key in {console}; API usage is billed separately."
)

_SPECS: list[tuple[ProviderSpec, type[LLMAdapter]]] = [
    (
        ProviderSpec(
            id="openai",
            display_name="OpenAI",
            kind=ProviderKind.CLOUD,
            default_base_url="https://api.openai.com/v1",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                parallel_tools=True,
                structured_output=True,
                json_mode=True,
                vision=None,
                reasoning=None,
                **_NO_EMBED,
            ),
            docs_url="https://developers.openai.com/api/reference/resources/chat",
            subscription_note=SUBSCRIPTION_NOTE.format(
                product="ChatGPT Plus/Pro", console="the OpenAI platform dashboard"
            ),
        ),
        OpenAIAdapter,
    ),
    (
        ProviderSpec(
            id="anthropic",
            display_name="Anthropic (Claude)",
            kind=ProviderKind.CLOUD,
            default_base_url="https://api.anthropic.com/v1",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                parallel_tools=True,
                structured_output=None,
                json_mode=False,  # JSON-schema output only, no free-form JSON mode
                vision=None,
                reasoning=None,
                **_NO_EMBED,
            ),
            docs_url="https://platform.claude.com/docs/en/api/messages",
            subscription_note=(
                "Claude Pro/Max are consumer subscriptions. Anthropic does not permit "
                "their sign-in to be used by third-party tools, and they do not include "
                "API access. Create an API key in the Claude Console."
            ),
            notes=("Models newer than Opus 4.6 accept only the default temperature.",),
        ),
        AnthropicAdapter,
    ),
    (
        ProviderSpec(
            id="gemini",
            display_name="Google Gemini",
            kind=ProviderKind.CLOUD,
            default_base_url="https://generativelanguage.googleapis.com/v1beta",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                parallel_tools=True,
                structured_output=True,
                json_mode=True,
                vision=None,
                reasoning=None,
                **_NO_EMBED,
            ),
            docs_url="https://ai.google.dev/api/generate-content",
            subscription_note=SUBSCRIPTION_NOTE.format(
                product="A Gemini app subscription", console="Google AI Studio"
            ),
            notes=(
                "Gemini API OAuth requires a deployment-owned Google Cloud OAuth client and "
                "is not offered here; use an API key.",
            ),
        ),
        GeminiAdapter,
    ),
    (
        ProviderSpec(
            id="deepseek",
            display_name="DeepSeek",
            kind=ProviderKind.CLOUD,
            default_base_url="https://api.deepseek.com",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                json_mode=True,
                reasoning=True,
                **_NO_EMBED,
            ),
            docs_url="https://api-docs.deepseek.com/",
        ),
        DeepSeekAdapter,
    ),
    (
        ProviderSpec(
            id="qwen",
            display_name="Qwen (Alibaba Cloud Model Studio)",
            kind=ProviderKind.CLOUD,
            default_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            base_url_mode=BaseUrlMode.OFFICIAL_CHOICE,
            auth_methods=KEY,
            capabilities=LLMCapabilities(chat=True, streaming=True, tool_calling=True, **_NO_EMBED),
            docs_url="https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope",
            official_base_urls=(
                "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1",
            ),
            official_base_url_patterns=(
                r"https://dashscope(-intl|-us)?\.aliyuncs\.com/compatible-mode/v1",
                r"https://[a-z0-9-]{1,64}\.[a-z0-9-]{1,32}\.maas\.aliyuncs\.com/compatible-mode/v1",
            ),
            notes=(
                "Pick the endpoint of the region your API key belongs to; the legacy "
                "dashscope domains stop receiving new features after 2026-09-30.",
            ),
        ),
        QwenAdapter,
    ),
    (
        ProviderSpec(
            id="xai",
            display_name="xAI (Grok)",
            kind=ProviderKind.CLOUD,
            default_base_url="https://api.x.ai/v1",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                parallel_tools=True,
                structured_output=True,
                reasoning=True,
                **_NO_EMBED,
            ),
            docs_url="https://docs.x.ai/developers/rest-api-reference/inference/chat-completions",
            notes=(
                "Uses xAI's Chat Completions endpoint, which xAI marks as legacy but "
                "supported; new xAI features ship on its Responses API first.",
            ),
        ),
        XAIAdapter,
    ),
    (
        ProviderSpec(
            id="mistral",
            display_name="Mistral AI",
            kind=ProviderKind.CLOUD,
            default_base_url="https://api.mistral.ai/v1",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=KEY,
            capabilities=LLMCapabilities(
                chat=True,
                streaming=True,
                tool_calling=True,
                parallel_tools=True,
                structured_output=True,
                json_mode=True,
                **_NO_EMBED,
            ),
            docs_url="https://docs.mistral.ai/api/",
        ),
        MistralAdapter,
    ),
    (
        ProviderSpec(
            id="openrouter",
            display_name="OpenRouter",
            kind=ProviderKind.AGGREGATOR,
            default_base_url="https://openrouter.ai/api/v1",
            base_url_mode=BaseUrlMode.FIXED,
            auth_methods=(AuthMethod.API_KEY, AuthMethod.OAUTH_PKCE),
            capabilities=LLMCapabilities(chat=True, streaming=True, **_NO_EMBED),
            docs_url="https://openrouter.ai/docs/api/reference/overview",
            notes=(
                "Per-model tool, JSON, reasoning and vision support comes from OpenRouter's "
                "model list. Sign in with OpenRouter creates a key you can revoke there.",
            ),
        ),
        OpenRouterAdapter,
    ),
    (
        ProviderSpec(
            id="ollama",
            display_name="Ollama",
            kind=ProviderKind.LOCAL,
            default_base_url="http://localhost:11434",
            base_url_mode=BaseUrlMode.USER,
            auth_methods=LOCAL_AUTH,
            capabilities=LLMCapabilities(
                chat=True, streaming=True, structured_output=True, json_mode=True, **_NO_EMBED
            ),
            docs_url="https://docs.ollama.com/api",
            notes=("Per-model tool, vision and thinking support comes from /api/show.",),
        ),
        OllamaAdapter,
    ),
    (
        ProviderSpec(
            id="lmstudio",
            display_name="LM Studio",
            kind=ProviderKind.LOCAL,
            default_base_url="http://localhost:1234/v1",
            base_url_mode=BaseUrlMode.USER,
            auth_methods=LOCAL_AUTH,
            capabilities=LLMCapabilities(
                chat=True, streaming=True, tool_calling=True, structured_output=True, **_NO_EMBED
            ),
            docs_url="https://lmstudio.ai/docs/developer/openai-compat",
            notes=("API tokens are optional and enabled in LM Studio's server settings.",),
        ),
        LMStudioAdapter,
    ),
    (
        ProviderSpec(
            id="vllm",
            display_name="vLLM",
            kind=ProviderKind.LOCAL,
            default_base_url="http://localhost:8000/v1",
            base_url_mode=BaseUrlMode.USER,
            auth_methods=LOCAL_AUTH,
            capabilities=LLMCapabilities(
                chat=True, streaming=True, structured_output=True, **_NO_EMBED
            ),
            docs_url="https://docs.vllm.ai/en/latest/serving/online_serving/",
            notes=(
                "Tool calling requires the server to run with --enable-auto-tool-choice "
                "and a --tool-call-parser.",
            ),
        ),
        VLLMAdapter,
    ),
    (
        ProviderSpec(
            id="openai_compatible",
            display_name="OpenAI-compatible endpoint",
            kind=ProviderKind.COMPATIBLE,
            default_base_url="",
            base_url_mode=BaseUrlMode.USER,
            auth_methods=LOCAL_AUTH,
            capabilities=LLMCapabilities(chat=True, streaming=True, **_NO_EMBED),
            docs_url="https://developers.openai.com/api/reference/resources/chat",
            notes=("Any server implementing /chat/completions; capabilities are unknown.",),
        ),
        OpenAICompatibleAdapter,
    ),
]

_REGISTRY: dict[str, LLMAdapter] = {spec.id: cls(spec) for spec, cls in _SPECS}


def get(provider: str) -> LLMAdapter:
    """The adapter for a provider id; KeyError for unknown providers."""
    return _REGISTRY[provider]


def specs() -> list[ProviderSpec]:
    return [adapter.spec for adapter in _REGISTRY.values()]


def spec(provider: str) -> ProviderSpec:
    return _REGISTRY[provider].spec


def known(provider: str) -> bool:
    return provider in _REGISTRY
