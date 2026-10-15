"""Local execution API: the controller dispatches tasks here (Sprint 5).

Every request must carry the agent token this agent registered with
(X-Agent-Token) — see ADR-011.
"""

import asyncio
import hmac
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from lycosa_agent.knowledge import GroundedRequest, GroundedResponse, answer, capabilities
from lycosa_agent.runtimes.base import LocalRuntimeAdapter
from lycosa_agent.runtimes.chat import ChatMessage, RuntimeRequest
from lycosa_agent.runtimes.claude_code import ClaudeCodeRuntime
from lycosa_agent.runtimes.registry import provider_registry

AGENT_TOKEN_HEADER = "X-Agent-Token"
# the Lycosa user a Claude subscription call is made for (ADR-032)
OWNER_HEADER = "X-Lycosa-Owner"


class ExecuteRequest(BaseModel):
    model: str = Field(min_length=1)
    prompt: str = ""
    messages: list[ChatMessage] | None = None
    provider: Literal["ollama", "anthropic"] = "ollama"
    max_tokens: int = Field(default=4096, ge=1, le=16384)
    temperature: float = Field(default=0.2, ge=0, le=1)
    stream: Literal[False] = False
    tools: list = Field(default_factory=list, max_length=0)
    agent_loop: Literal[False] = False
    options: dict[str, Any] = {}


class ExecuteResponse(BaseModel):
    status: str  # succeeded | failed
    output: str | None = None
    error: str | None = None


class ClaudeCompleteRequest(BaseModel):
    # an alias (sonnet, opus, haiku, default) or a full model id; never a flag
    model: str = Field(default="default", pattern=r"^[a-z0-9][a-z0-9.\-]{0,63}$")
    messages: list[ChatMessage] = Field(min_length=1, max_length=200)


def owner_matches(configured: str, presented: str | None) -> bool:
    if not configured or not presented:
        return False
    return hmac.compare_digest(
        presented.strip().casefold().encode(), configured.strip().casefold().encode()
    )


class PullRequest(BaseModel):
    model: str = Field(min_length=1)


class PullResponse(BaseModel):
    status: str  # succeeded | failed
    models: list[str] = []  # inventory after the pull, on success
    error: str | None = None


def create_app(
    adapter: LocalRuntimeAdapter,
    token: str,
    *,
    cloud_enabled: bool = False,
    claude: ClaudeCodeRuntime | None = None,
    claude_owner: str = "",
) -> FastAPI:
    app = FastAPI(title="Lycosa Local Agent", docs_url=None, redoc_url=None)
    app.state.running_tasks = 0
    registry = provider_registry(adapter)
    rag_slots = asyncio.Semaphore(2)

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

    @app.get("/capabilities", dependencies=[Depends(check_token)])
    async def agent_capabilities() -> dict:
        return capabilities()

    @app.post("/rag/answer", response_model=GroundedResponse, dependencies=[Depends(check_token)])
    async def grounded_answer(body: GroundedRequest) -> GroundedResponse:
        if rag_slots.locked():
            raise HTTPException(429, "Grounded-answer capacity busy; retry later")
        async with rag_slots:
            app.state.running_tasks += 1
            try:
                return await answer(adapter, body)
            except TimeoutError:
                raise HTTPException(504, "Local grounded answer timed out") from None
            except Exception:
                raise HTTPException(
                    502, "Local grounded answer failed; check model/runtime"
                ) from None
            finally:
                app.state.running_tasks -= 1

    @app.get("/claude/status", dependencies=[Depends(check_token)])
    async def claude_status(
        x_lycosa_owner: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Claude subscription runtime state; never the Claude account's
        identity. owner_matches says whether the presented Lycosa user is the
        one this machine serves."""
        if claude is None:
            return {"enabled": False, "owner_matches": False}
        return {
            **await claude.status(),
            "owner_matches": owner_matches(claude_owner, x_lycosa_owner),
        }

    @app.post("/claude/complete", dependencies=[Depends(check_token)])
    async def claude_complete(
        body: ClaudeCompleteRequest,
        x_lycosa_owner: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        """Run one call on this machine's Claude Code; NDJSON events."""
        if claude is None:
            raise HTTPException(404, "The Claude subscription runtime is not enabled on this node")
        if not owner_matches(claude_owner, x_lycosa_owner):
            raise HTTPException(403, "This node's Claude subscription serves a different user")

        async def events() -> AsyncIterator[bytes]:
            app.state.running_tasks += 1
            try:
                async for event in claude.stream(body.messages, body.model):
                    yield (json.dumps(event) + "\n").encode()
            finally:
                app.state.running_tasks -= 1

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/models/pull", response_model=PullResponse, dependencies=[Depends(check_token)])
    async def pull_model(body: PullRequest) -> PullResponse:
        """Download a model into the local runtime (controller-initiated)."""
        try:
            await adapter.pull_model(body.model)
            return PullResponse(status="succeeded", models=await adapter.list_models())
        except Exception as exc:  # noqa: BLE001 — report, don't crash the agent
            return PullResponse(status="failed", error=str(exc))

    @app.post("/execute", response_model=ExecuteResponse, dependencies=[Depends(check_token)])
    async def execute(
        body: ExecuteRequest,
        x_provider_credential: Annotated[str | None, Header()] = None,
    ) -> ExecuteResponse:
        if body.provider != "ollama" and (not cloud_enabled or not x_provider_credential):
            raise HTTPException(403, "Cloud execution is not authorized")
        app.state.running_tasks += 1
        try:
            if body.messages is not None or body.provider != "ollama":
                request = RuntimeRequest(
                    model=body.model,
                    messages=body.messages or [ChatMessage(role="user", content=body.prompt)],
                    max_tokens=body.max_tokens,
                    temperature=body.temperature,
                )
                result = await registry[body.provider].complete(request, x_provider_credential)
                output = result.output
            else:
                output = await adapter.generate(body.model, body.prompt, body.options)
        except Exception as exc:  # noqa: BLE001 — report, don't crash the agent
            return ExecuteResponse(
                status="failed",
                error=("Cloud provider request failed" if body.provider != "ollama" else str(exc)),
            )
        finally:
            app.state.running_tasks -= 1
            x_provider_credential = None
        return ExecuteResponse(status="succeeded", output=output)

    return app
