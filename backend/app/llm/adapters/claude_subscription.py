"""Claude subscription (ADR-032): the call runs on a Lycosa node, in the node
owner's own signed-in Claude Code, through the agent's /claude endpoints.

Lycosa never performs Claude.ai sign-in and never holds a Claude credential:
the connection's credential is the node's agent token, and the agent runs
the unmodified Claude Code binary under the sign-in its owner made with
`claude auth login`.
"""

import time
from collections.abc import AsyncIterator
from typing import Any

from app.llm.adapters.base import LLMAdapter, as_count, collect, health_from_error
from app.llm.errors import (
    AuthenticationError,
    LLMError,
    LLMTimeoutError,
    MalformedResponseError,
    ModelNotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    ResponseTooLargeError,
)
from app.llm.http import Connection, loads_event, request_json, stream_lines
from app.llm.node_runtime import AGENT_TOKEN_HEADER
from app.llm.types import (
    FinishReason,
    HealthStatus,
    LLMRequest,
    LLMResponse,
    ModelInfo,
    Role,
    StreamEvent,
    StreamEventType,
    Usage,
)

# Claude Code's model aliases; "default" lets the plan pick its default model
MODEL_ALIASES = {
    "default": "Plan default",
    "sonnet": "Claude Sonnet (latest)",
    "opus": "Claude Opus (latest)",
    "haiku": "Claude Haiku (latest)",
}
SIGN_IN_HINT = "Claude Code on the node is not signed in; run `claude auth login` on that machine"


class _NodeTimeout(LLMTimeoutError):
    # the node already waited its own timeout; repeating the run would only
    # spend more of the user's plan
    retryable = False


def _agent_error(code: Any, provider: str) -> LLMError:
    match code:
        case "not_signed_in":
            return AuthenticationError(SIGN_IN_HINT, provider=provider)
        case "usage_limit":
            return QuotaExceededError(
                "Your Claude plan's usage limit is reached; it resets later", provider=provider
            )
        case "model_unavailable":
            return ModelNotFoundError(
                "This model is not available on your Claude plan", provider=provider
            )
        case "timeout":
            return _NodeTimeout("Claude Code on the node did not finish in time", provider=provider)
        case "output_too_large":
            return ResponseTooLargeError(provider=provider)
        case "not_installed":
            return LLMError("Claude Code is not installed on the node", provider=provider)
        case _:
            return LLMError("Claude Code failed on the node", provider=provider)


def _from_agent_status(exc: LLMError, provider: str) -> LLMError:
    """The agent's own HTTP refusals, which the generic mapping would read as
    a provider key problem."""
    match exc.status:
        case 401:
            return LLMError(
                "The node did not accept the controller; wait for its next heartbeat",
                provider=provider,
                status=401,
            )
        case 403:
            return PermissionDeniedError(
                "This node's Claude plan serves a different Lycosa user "
                "(LYCOSA_CLAUDE_SUBSCRIPTION_OWNER on the node)",
                provider=provider,
                status=403,
            )
        case 404:
            return LLMError(
                "The Claude subscription runtime is not enabled on the node "
                "(LYCOSA_CLAUDE_SUBSCRIPTION=true)",
                provider=provider,
                status=404,
            )
    return exc


class ClaudeSubscriptionAdapter(LLMAdapter):
    def headers(self, conn: Connection) -> dict[str, str]:
        return {**conn.headers, AGENT_TOKEN_HEADER: conn.credential or ""}

    @staticmethod
    def convert_messages(request: LLMRequest) -> list[dict[str, str]]:
        messages = []
        system = request.system_text()
        if system:
            messages.append({"role": "system", "content": system})
        for message in request.messages:
            if message.role in (Role.USER, Role.ASSISTANT):
                messages.append({"role": message.role.value, "content": message.text()})
        return messages

    async def generate(self, conn: Connection, request: LLMRequest) -> LLMResponse:
        return await collect(
            self.stream(conn, request), provider=self.provider, model=request.model
        )

    async def stream(self, conn: Connection, request: LLMRequest) -> AsyncIterator[StreamEvent]:
        body = {"model": request.model or "default", "messages": self.convert_messages(request)}
        finished = False
        try:
            async for line in stream_lines(
                conn, "POST", "/claude/complete", headers=self.headers(conn), json_body=body
            ):
                if not line.strip():
                    continue
                event = loads_event(line, self.provider)
                if not isinstance(event, dict):
                    raise MalformedResponseError(provider=self.provider)
                kind = event.get("type")
                text = event.get("text")
                if kind == "text" and isinstance(text, str):
                    yield StreamEvent(type=StreamEventType.TEXT, text=text)
                elif kind == "reasoning" and isinstance(text, str):
                    yield StreamEvent(type=StreamEventType.REASONING, text=text)
                elif kind == "usage":
                    yield StreamEvent(
                        type=StreamEventType.USAGE,
                        usage=Usage(
                            prompt_tokens=as_count(event.get("input_tokens")),
                            completion_tokens=as_count(event.get("output_tokens")),
                        ).with_total(),
                    )
                elif kind == "finish":
                    finished = True
                    reason = event.get("reason")
                    yield StreamEvent(
                        type=StreamEventType.FINISH,
                        finish_reason=FinishReason(reason)
                        if reason in ("stop", "length")
                        else FinishReason.OTHER,
                    )
                elif kind == "error":
                    raise _agent_error(event.get("code"), self.provider)
        except LLMError as exc:
            raise _from_agent_status(exc, self.provider) from None
        if not finished:
            raise MalformedResponseError(provider=self.provider)

    async def list_models(self, conn: Connection) -> list[ModelInfo]:
        return [
            ModelInfo(provider=self.provider, id=alias, display_name=name)
            for alias, name in MODEL_ALIASES.items()
        ]

    async def node_status(self, conn: Connection) -> dict[str, Any]:
        """The agent's view: installed, version, signed in, plan, plan usage,
        and whether it serves the Lycosa user named on the connection."""
        try:
            data = await request_json(conn, "GET", "/claude/status", headers=self.headers(conn))
        except LLMError as exc:
            raise _from_agent_status(exc, self.provider) from None
        if not isinstance(data, dict):
            raise MalformedResponseError(provider=self.provider)
        return data

    async def health_check(self, conn: Connection) -> HealthStatus:
        started = time.monotonic()
        try:
            status = await self.node_status(conn)
        except LLMError as exc:
            return health_from_error(exc)
        latency = int((time.monotonic() - started) * 1000)
        if not status.get("enabled"):
            return HealthStatus(
                status="error",
                detail="The Claude subscription runtime is not enabled on the node",
                latency_ms=latency,
            )
        if not status.get("installed"):
            return HealthStatus(
                status="error",
                detail="Claude Code is not installed on the node",
                latency_ms=latency,
            )
        if not status.get("owner_matches"):
            return HealthStatus(
                status="unauthorized",
                detail="This node's Claude plan serves a different Lycosa user",
                latency_ms=latency,
            )
        if not status.get("signed_in"):
            return HealthStatus(status="unauthorized", detail=SIGN_IN_HINT, latency_ms=latency)
        plan = status.get("plan") if isinstance(status.get("plan"), str) else None
        version = status.get("version") if isinstance(status.get("version"), str) else None
        detail = f"Claude Code {version or ''} signed in".replace("  ", " ")
        if plan:
            detail += f" ({plan} plan)"
        return HealthStatus(
            status="ready",
            detail=detail,
            latency_ms=latency,
            models_available=len(MODEL_ALIASES),
        )
