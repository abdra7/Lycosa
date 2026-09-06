"""Execution adapter registration; the executor only uses the common contract."""

from lycosa_agent.runtimes.anthropic import AnthropicAdapter


def provider_registry(local_adapter) -> dict:
    return {"ollama": local_adapter, "anthropic": AnthropicAdapter()}
