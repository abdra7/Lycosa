"""Runtime adapter contract. Ollama first; llama.cpp / HF slot in behind
the same interface without touching the executor."""

from typing import Any, Protocol, runtime_checkable

from lycosa_agent.runtimes.chat import RuntimeRequest, RuntimeResponse


@runtime_checkable
class RuntimeAdapter(Protocol):
    name: str

    async def complete(
        self, request: RuntimeRequest, credential: str | None = None
    ) -> RuntimeResponse: ...


@runtime_checkable
class LocalRuntimeAdapter(RuntimeAdapter, Protocol):
    async def list_models(self) -> list[str]: ...

    async def pull_model(self, model: str) -> None: ...

    async def generate(
        self, model: str, prompt: str, options: dict[str, Any] | None = None
    ) -> str: ...
