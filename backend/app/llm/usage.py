"""Usage records, cost estimates and LLM metrics/logs (ADR-031).

Recorded per provider attempt: provider, account, model, tokens, latency,
outcome, fallback position and cost. Never recorded: prompts, outputs, keys,
headers.
"""

import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.metrics import LLM_LATENCY, LLM_REQUESTS, LLM_TOKENS
from app.llm.types import Usage
from app.models.llm import LLMUsage

logger = logging.getLogger("lycosa.llm")

_DEFAULT_PRICING = Path(__file__).parents[2] / "config" / "llm_pricing.yml"


@lru_cache
def pricing_table() -> dict[str, Any]:
    path = Path(get_settings().llm_pricing_file or _DEFAULT_PRICING)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        logger.warning("LLM pricing file unreadable; cost estimates disabled")
        return {"currency": None, "models": {}}
    models = {}
    for ref, entry in (data.get("models") or {}).items():
        try:
            rates = (float(entry["input_per_mtok"]), float(entry["output_per_mtok"]))
        except (KeyError, TypeError, ValueError):
            continue
        if all(math.isfinite(r) and r >= 0 for r in rates):
            models[str(ref)] = rates
    currency = data.get("currency")
    return {"currency": currency if isinstance(currency, str) else "USD", "models": models}


def estimate_cost(
    provider: str, model: str, usage: Usage
) -> tuple[float | None, str | None, str | None]:
    """(cost, source, currency). A provider-reported cost wins; otherwise the
    configured per-token price; otherwise unknown (None), never invented."""
    if usage.reported_cost is not None:
        return usage.reported_cost, "provider", "USD"
    table = pricing_table()
    rates = table["models"].get(f"{provider}:{model}")
    if rates is None or usage.prompt_tokens is None or usage.completion_tokens is None:
        return None, None, None
    cost = (usage.prompt_tokens * rates[0] + usage.completion_tokens * rates[1]) / 1_000_000
    return round(cost, 8), "configured", table["currency"]


async def record(
    db: AsyncSession,
    *,
    request_id: str,
    user_id: Any,
    api_key_id: Any,
    account_id: Any,
    provider: str,
    model: str,
    purpose: str | None,
    task_id: Any,
    workflow_run_id: Any,
    status: str,
    error_code: str | None,
    attempts: int,
    fallback_index: int,
    stream: bool,
    latency_ms: int,
    usage: Usage | None,
) -> tuple[float | None, str | None]:
    usage = usage or Usage()
    cost, source, currency = (
        estimate_cost(provider, model, usage) if status == "succeeded" else (None, None, None)
    )
    db.add(
        LLMUsage(
            request_id=request_id,
            user_id=user_id,
            api_key_id=api_key_id,
            account_id=account_id,
            provider=provider,
            model=model[:200],
            purpose=purpose,
            task_id=task_id,
            workflow_run_id=workflow_run_id,
            status=status,
            error_code=error_code,
            attempts=attempts,
            fallback_index=fallback_index,
            stream=stream,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            latency_ms=latency_ms,
            estimated_cost=cost,
            cost_source=source,
            currency=currency,
        )
    )
    await db.flush()

    LLM_REQUESTS.labels(provider, status).inc()
    LLM_LATENCY.labels(provider).observe(latency_ms / 1000)
    if usage.prompt_tokens:
        LLM_TOKENS.labels(provider, "prompt").inc(usage.prompt_tokens)
    if usage.completion_tokens:
        LLM_TOKENS.labels(provider, "completion").inc(usage.completion_tokens)
    logger.info(
        "llm request %s",
        status,
        extra={
            "llm_request_id": request_id,
            "provider": provider,
            "account_id": str(account_id) if account_id else None,
            "model": model,
            "purpose": purpose,
            "llm_task_id": str(task_id) if task_id else None,
            "status": status,
            "error_code": error_code,
            "attempts": attempts,
            "fallback_index": fallback_index,
            "stream": stream,
            "latency_ms": latency_ms,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
        },
    )
    return cost, source
