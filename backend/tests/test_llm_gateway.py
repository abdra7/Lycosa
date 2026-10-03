"""Gateway reliability: retries with backoff, timeouts, fallback across route
entries, capability gating, usage/cost records, redaction, streaming fallback
rules and privacy enforcement."""

import asyncio
import logging

import httpx
import pytest
import respx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.metrics import LLM_FALLBACKS
from app.llm import discovery, errors, gateway, routing, usage
from app.llm.accounts import Actor
from app.llm.types import (
    LLMCapabilities,
    LLMRequest,
    Message,
    ModelInfo,
    StreamEventType,
    ToolSpec,
)
from app.models import LLMUsage
from tests.llm_helpers import TEST_KEY, anthropic_message, make_account, openai_completion

OPENAI = "https://api.openai.com/v1/chat/completions"
ANTHROPIC = "https://api.anthropic.com/v1/messages"
PROMPT = "confidential prompt text 42"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(gateway, "sleep", fake_sleep)
    discovery.invalidate()
    return delays


@pytest.fixture
def ctx(users):
    return gateway.CallContext(
        actor=Actor(role="operator", user_id=users["operator"].id), purpose="default"
    )


def request(**extra) -> LLMRequest:
    return LLMRequest(model="placeholder", messages=[Message(role="user", content=PROMPT)], **extra)


async def rows(db_session):
    db_session.expire_all()
    return (
        (await db_session.execute(select(LLMUsage).order_by(LLMUsage.fallback_index)))
        .scalars()
        .all()
    )


# --- retries ---------------------------------------------------------------------


async def test_transient_errors_retry_with_backoff_then_succeed(_no_real_sleep, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_retries", 2)
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise errors.RateLimitError(retry_after=3.0 if len(calls) == 1 else None)
        return "ok"

    result, attempts = await gateway.with_retries(flaky, deadline=5)
    assert (result, attempts) == ("ok", 3)
    assert _no_real_sleep[0] >= 3.0  # Retry-After honored
    assert 1.0 <= _no_real_sleep[1] <= 1.5  # exponential: 0.5 * 2^1 plus jitter


async def test_permanent_errors_are_not_retried(_no_real_sleep):
    calls = []

    async def denied():
        calls.append(1)
        raise errors.AuthenticationError()

    with pytest.raises(errors.AuthenticationError) as excinfo:
        await gateway.with_retries(denied, deadline=5)
    assert len(calls) == 1 and not _no_real_sleep and excinfo.value.attempts == 1


async def test_retry_limit_and_overall_deadline(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_retries", 1)

    async def hangs():
        await asyncio.sleep(10)

    with pytest.raises(errors.LLMTimeoutError) as excinfo:
        await gateway.with_retries(hangs, deadline=0.01)
    assert excinfo.value.attempts == 2


async def test_overall_budget_stops_retries_and_fallbacks(db_session, ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_total_timeout_seconds", 1)
    first = await make_account(db_session, "openai")
    second = await make_account(db_session, "mistral")
    calls = []

    async def slow_failure(self, conn, req):
        calls.append(conn.provider)
        await asyncio.sleep(1.2)  # consumes the whole budget
        raise errors.ProviderUnavailableError()

    from app.llm.adapters.openai_compat import OpenAICompatibleAdapter

    monkeypatch.setattr(OpenAICompatibleAdapter, "generate", slow_failure)
    with pytest.raises(errors.LLMTimeoutError):
        await gateway.generate(
            db_session, ctx, request(), [routing.Target(first, "a"), routing.Target(second, "b")]
        )
    assert calls == ["openai"]  # no retry, no second provider once time is up


def test_backoff_is_capped():
    assert gateway.backoff_delay(10, None) == gateway.MAX_BACKOFF_SECONDS
    assert gateway.backoff_delay(0, 999) == gateway.MAX_BACKOFF_SECONDS


# --- fallback ----------------------------------------------------------------------


@respx.mock
async def test_fallback_after_exhausted_retries_records_every_attempt(db_session, ctx, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_max_retries", 2)
    primary = await make_account(db_session, "openai")
    backup = await make_account(db_session, "anthropic")
    respx.post(OPENAI).mock(return_value=httpx.Response(503))
    respx.post(ANTHROPIC).mock(return_value=httpx.Response(200, json=anthropic_message()))
    before = LLM_FALLBACKS.labels("openai")._value.get()

    result = await gateway.generate(
        db_session,
        ctx,
        request(),
        [routing.Target(primary, "gpt-x"), routing.Target(backup, "claude-x")],
    )
    assert result.response.content == "claude answer"
    assert (result.fallback_index, result.target.account.id) == (1, backup.id)
    assert result.skipped == [
        {"provider": "openai", "account_id": str(primary.id), "error": "provider_unavailable"}
    ]
    assert respx.calls.call_count == 4  # 3 OpenAI attempts + 1 Anthropic
    assert LLM_FALLBACKS.labels("openai")._value.get() == before + 1

    failed, succeeded = await rows(db_session)
    assert (failed.status, failed.attempts, failed.error_code) == (
        "failed",
        3,
        "provider_unavailable",
    )
    assert (succeeded.status, succeeded.fallback_index, succeeded.provider) == (
        "succeeded",
        1,
        "anthropic",
    )
    assert succeeded.prompt_tokens == 4 and succeeded.total_tokens == 9
    assert succeeded.request_id == failed.request_id == ctx.request_id


@respx.mock
async def test_permanent_failure_falls_back_without_retrying(db_session, ctx):
    primary = await make_account(db_session, "openai")
    backup = await make_account(db_session, "mistral")
    respx.post(OPENAI).mock(return_value=httpx.Response(401))
    respx.post("https://api.mistral.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_completion("mistral says"))
    )
    result = await gateway.generate(
        db_session, ctx, request(), [routing.Target(primary, "a"), routing.Target(backup, "b")]
    )
    assert result.response.content == "mistral says"
    assert respx.routes[0].call_count == 1


@respx.mock
async def test_all_entries_failing_raises_the_last_error(db_session, ctx):
    a = await make_account(db_session, "openai")
    b = await make_account(db_session, "deepseek")
    respx.post(OPENAI).mock(return_value=httpx.Response(401))
    respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(404, json={"error": {"message": "model not found"}})
    )
    with pytest.raises(errors.ModelNotFoundError) as excinfo:
        await gateway.generate(
            db_session, ctx, request(), [routing.Target(a, "x"), routing.Target(b, "y")]
        )
    assert [s["error"] for s in excinfo.value.skipped] == [
        "provider_auth_failed",
        "model_not_found",
    ]
    assert [r.status for r in await rows(db_session)] == ["failed", "failed"]


@respx.mock
async def test_malformed_provider_json_falls_back_instead_of_crashing(db_session, ctx):
    broken = await make_account(db_session, "openai")
    backup = await make_account(db_session, "anthropic")
    respx.post(OPENAI).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": "not-an-object"}]})
    )
    respx.post(ANTHROPIC).mock(return_value=httpx.Response(200, json=anthropic_message()))
    result = await gateway.generate(
        db_session, ctx, request(), [routing.Target(broken, "a"), routing.Target(backup, "b")]
    )
    assert result.fallback_index == 1
    assert result.skipped[0]["error"] == "provider_malformed_response"


@respx.mock
async def test_known_unsupported_capability_skips_to_a_capable_model(db_session, ctx):
    blind = await make_account(db_session, "openrouter")
    able = await make_account(db_session, "openai")
    discovery._cache[blind.id] = (
        float("inf"),
        [
            ModelInfo(
                provider="openrouter",
                id="no-tools",
                capabilities=LLMCapabilities(tool_calling=False),
            )
        ],
    )
    respx.post(OPENAI).mock(return_value=httpx.Response(200, json=openai_completion()))
    result = await gateway.generate(
        db_session,
        ctx,
        request(tools=[ToolSpec(name="t")]),
        [routing.Target(blind, "no-tools"), routing.Target(able, "gpt-x")],
    )
    assert result.fallback_index == 1
    assert result.skipped[0]["error"] == "capability_unsupported"
    assert not any("openrouter" in str(c.request.url) for c in respx.calls)  # never called


# --- secrecy and observability ------------------------------------------------------


@respx.mock
async def test_echoed_keys_are_redacted_and_logs_hold_no_content(db_session, ctx, caplog):
    account = await make_account(db_session, "openai")
    respx.post(OPENAI).mock(
        return_value=httpx.Response(200, json=openai_completion(f"your key is {TEST_KEY}"))
    )
    with caplog.at_level(logging.INFO, logger="lycosa.llm"):
        result = await gateway.generate(db_session, ctx, request(), [routing.Target(account, "m")])
    assert result.response.content == "your key is [REDACTED]"
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "llm request succeeded" in logged
    assert TEST_KEY not in logged and PROMPT not in logged
    (row,) = await rows(db_session)
    assert TEST_KEY not in str(vars(row)) and PROMPT not in str(vars(row))


@respx.mock
async def test_provider_reported_cost_wins_and_unknown_cost_stays_null(
    db_session, ctx, tmp_path, monkeypatch
):
    router = await make_account(db_session, "openrouter")
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_completion(cost=0.0012))
    )
    result = await gateway.generate(db_session, ctx, request(), [routing.Target(router, "v/m")])
    assert (result.estimated_cost, result.cost_source) == (0.0012, "provider")

    plain = await make_account(db_session, "openai")
    respx.post(OPENAI).mock(return_value=httpx.Response(200, json=openai_completion()))
    result = await gateway.generate(db_session, ctx, request(), [routing.Target(plain, "unpriced")])
    assert result.estimated_cost is None and result.cost_source is None

    pricing = tmp_path / "pricing.yml"
    pricing.write_text(
        'currency: EUR\nmodels:\n  "openai:priced": {input_per_mtok: 2.0, output_per_mtok: 10.0}\n'
    )
    monkeypatch.setattr(get_settings(), "llm_pricing_file", str(pricing))
    usage.pricing_table.cache_clear()
    try:
        result = await gateway.generate(
            db_session, ctx, request(), [routing.Target(plain, "priced")]
        )
        assert result.estimated_cost == pytest.approx((3 * 2.0 + 2 * 10.0) / 1_000_000)
        assert result.cost_source == "configured"
        assert (await rows(db_session))[-1].currency == "EUR"
    finally:
        usage.pricing_table.cache_clear()


def test_shipped_pricing_table_is_empty():
    usage.pricing_table.cache_clear()
    assert usage.pricing_table()["models"] == {}


# --- streaming ----------------------------------------------------------------------


def sse(*chunks):
    return "".join(f"data: {c}\n\n" for c in chunks).encode()


@respx.mock
async def test_stream_falls_back_only_before_the_first_event(db_session, ctx):
    primary = await make_account(db_session, "openai")
    backup = await make_account(db_session, "xai")
    respx.post(OPENAI).mock(return_value=httpx.Response(401))
    respx.post("https://api.x.ai/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            content=sse(
                '{"choices":[{"index":0,"delta":{"content":"hi"}}]}',
                '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
                "[DONE]",
            ),
        )
    )
    events = [
        e
        async for e in gateway.stream(
            db_session, ctx, request(), [routing.Target(primary, "a"), routing.Target(backup, "b")]
        )
    ]
    assert events[0].type == StreamEventType.START
    assert events[0].metadata["provider"] == "xai" and events[0].metadata["fallback_index"] == 1
    assert [e.text for e in events if e.type == StreamEventType.TEXT] == ["hi"]
    assert [r.status for r in await rows(db_session)] == ["failed", "succeeded"]


@respx.mock
async def test_mid_stream_failure_is_final_not_silently_switched(db_session, ctx):
    primary = await make_account(db_session, "openai")
    backup = await make_account(db_session, "xai")
    respx.post(OPENAI).mock(
        return_value=httpx.Response(
            200,
            content=sse(
                '{"choices":[{"index":0,"delta":{"content":"partial"}}]}',
                '{"error":{"message":"overloaded","type":"server_error"}}',
            ),
        )
    )
    backup_route = respx.post("https://api.x.ai/v1/chat/completions")
    received = []
    with pytest.raises(errors.ProviderUnavailableError):
        async for event in gateway.stream(
            db_session, ctx, request(), [routing.Target(primary, "a"), routing.Target(backup, "b")]
        ):
            received.append(event)
    assert any(e.text == "partial" for e in received)
    assert backup_route.call_count == 0


# --- privacy ----------------------------------------------------------------------


async def test_private_requests_never_reach_a_cloud_provider(db_session, users):
    cloud = await make_account(db_session, "openai")
    private_ctx = gateway.CallContext(
        actor=Actor(role="operator", user_id=users["operator"].id), requires_privacy=True
    )
    with pytest.raises(errors.EndpointNotAllowedError):
        await gateway.generate(db_session, private_ctx, request(), [routing.Target(cloud, "m")])


async def test_private_requests_cannot_be_rebound_to_a_public_address(
    db_session, users, monkeypatch
):
    local = await make_account(
        db_session, "ollama", base_url="http://llm.lan:11434", key=None, is_local=True
    )

    async def public_answer(host, port):
        return ["93.184.216.34"]  # DNS now points outside the LAN

    import app.llm.netpolicy as netpolicy

    monkeypatch.setattr(netpolicy, "system_resolver", public_answer)
    private_ctx = gateway.CallContext(
        actor=Actor(role="operator", user_id=users["operator"].id), requires_privacy=True
    )
    with pytest.raises(errors.EndpointNotAllowedError):
        await gateway.generate(db_session, private_ctx, request(), [routing.Target(local, "m")])
