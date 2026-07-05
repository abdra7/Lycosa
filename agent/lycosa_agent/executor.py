"""Local execution API: the controller dispatches tasks here (Sprint 5).

Every request must carry the agent token this agent registered with
(X-Agent-Token) — see ADR-011.
"""

import hmac
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from lycosa_agent.runtimes.base import RuntimeAdapter

AGENT_TOKEN_HEADER = "X-Agent-Token"


class ExecuteRequest(BaseModel):
    model: str = Field(min_length=1)
    prompt: str
    options: dict[str, Any] = {}


class ExecuteResponse(BaseModel):
    status: str  # succeeded | failed
    output: str | None = None
    error: str | None = None


def create_app(adapter: RuntimeAdapter, token: str) -> FastAPI:
    app = FastAPI(title="Lycosa Local Agent", docs_url=None, redoc_url=None)

    async def check_token(
        x_agent_token: Annotated[str | None, Header()] = None,
    ) -> None:
        if x_agent_token is None or not hmac.compare_digest(x_agent_token, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent token"
            )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/models", dependencies=[Depends(check_token)])
    async def models() -> list[str]:
        return await adapter.list_models()

    @app.post("/execute", response_model=ExecuteResponse, dependencies=[Depends(check_token)])
    async def execute(body: ExecuteRequest) -> ExecuteResponse:
        try:
            output = await adapter.generate(body.model, body.prompt, body.options)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the agent
            return ExecuteResponse(status="failed", error=str(exc))
        return ExecuteResponse(status="succeeded", output=output)

    return app
