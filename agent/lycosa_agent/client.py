"""HTTP client for the controller's API (register + heartbeat)."""

from typing import Any

import httpx

API_KEY_HEADER = "X-API-Key"


class ControllerClient:
    def __init__(self, controller_url: str, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=controller_url.rstrip("/"),
            headers={API_KEY_HEADER: api_key},
            timeout=15,
        )

    async def register(
        self,
        name: str,
        hardware_profile: dict[str, Any],
        agent_url: str | None = None,
        agent_token: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name, "hardware_profile": hardware_profile}
        if agent_url is not None:
            payload["agent_url"] = agent_url
        if agent_token is not None:
            payload["agent_token"] = agent_token
        response = await self._client.post("/api/v1/nodes/register", json=payload)
        response.raise_for_status()
        return response.json()

    async def heartbeat(self, metrics: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("/api/v1/nodes/heartbeat", json={"metrics": metrics})
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._client.aclose()
