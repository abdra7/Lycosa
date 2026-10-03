"""Runs an orchestrator task through the universal LLM layer.

The orchestrator stays provider-agnostic: it hands over the (grounded)
prompt and gets back the same outcome dict shape as the legacy routes.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import gateway, routing
from app.llm.accounts import AccountError, Actor, require_account
from app.llm.errors import LLMError
from app.llm.types import FinishReason, LLMRequest, Message
from app.models.task import TaskType
from app.models.user import ROLE_OPERATOR
from app.schemas.task import TaskCreate


async def complete_task(
    db: AsyncSession,
    body: TaskCreate,
    prompt: str,
    *,
    task_id: uuid.UUID,
    task_type: TaskType,
    user_id: uuid.UUID | None,
    api_key_id: uuid.UUID | None,
    workflow_run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    # account use depends on ownership only; the role is not consulted
    actor = Actor(role=ROLE_OPERATOR, user_id=user_id, api_key_id=api_key_id)
    # "auto": route by task type (coding -> coding route, vision -> vision
    # route, else default); resolution falls back to the default route
    purpose = routing.purpose_for_task(task_type) if body.route == "auto" else body.route
    try:
        if body.llm_account_id is not None:
            account = await require_account(db, actor, body.llm_account_id, "use")
            if body.requires_privacy and not routing.is_local_target(account):
                return {"error": "Privacy requires a local model; this account is not local"}
            targets = [routing.Target(account, body.model or "")]
        else:
            targets = await routing.resolve(
                db, actor, purpose or "default", requires_privacy=body.requires_privacy
            )
    except AccountError as exc:
        return {"error": exc.message}
    except LLMError as exc:
        return {"error": exc.public_message}

    request = LLMRequest(
        model=targets[0].model,
        messages=[Message(role="user", content=prompt)],
        max_tokens=body.max_tokens,
        # only an explicitly chosen temperature is sent; several newer models
        # reject anything but their own default
        temperature=body.temperature if "temperature" in body.model_fields_set else None,
    )
    ctx = gateway.CallContext(
        actor=actor,
        purpose=purpose,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        requires_privacy=body.requires_privacy,
    )
    try:
        result = await gateway.generate(db, ctx, request, targets)
    except LLMError as exc:
        tried = len(getattr(exc, "skipped", []) or [1])
        entries = "entry" if tried == 1 else "entries"
        return {"error": f"{exc.public_message} ({tried} route {entries} tried)"}

    response = result.response
    if response.finish_reason == FinishReason.LENGTH:
        return {"error": "Output was truncated at max_tokens; raise max_tokens and resubmit"}
    if response.finish_reason == FinishReason.CONTENT_FILTER:
        return {"error": "The provider withheld the answer (content filter)"}
    if not response.content.strip():
        return {"error": "The provider returned no text"}
    account = result.target.account
    local = routing.is_local_target(account)
    return {
        "output": response.content,
        "model": f"{account.provider}:{result.target.model}",
        "node": None,
        "usage": response.usage.model_dump(exclude_none=True, exclude={"reported_cost"}),
        "estimated_cost": result.estimated_cost,
        "cost_source": result.cost_source,
        "routing": {
            "provider": account.provider,
            "account_id": str(account.id),
            "account_label": account.label,
            "model": result.target.model,
            "execution_mode": "local" if local else "cloud",
            "executor": "controller",
            "purpose": purpose,
            "fallback_index": result.fallback_index,
            "attempts": result.attempts,
            "skipped": result.skipped,
            "reasons": [
                "explicit account selection"
                if body.llm_account_id
                else f"routing policy '{purpose}'",
                "local runtime" if local else "external provider",
            ],
        },
    }
