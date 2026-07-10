"""Ollama runtime adapter over its local HTTP API."""

from typing import Any

import httpx


class OllamaAdapter:
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 300) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def list_models(self) -> list[str]:
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        return [m["name"] for m in response.json().get("models", [])]

    async def pull_model(self, model: str) -> None:
        # weights are multi-GB downloads; don't inherit the short default timeout
        response = await self._client.post(
            "/api/pull", json={"name": model, "stream": False}, timeout=3600
        )
        response.raise_for_status()

    async def generate(self, model: str, prompt: str, options: dict[str, Any] | None = None) -> str:
        response = await self._client.post(
            "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": options or {}},
        )
        response.raise_for_status()
        return response.json()["response"]

    async def aclose(self) -> None:
        await self._client.aclose()
