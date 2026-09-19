"""Opt-in normalized text adapters through LiteLLM; never used by Phantom.

No Router, fallback, tools, callbacks or arbitrary per-request provider kwargs.
Deployment-owned profiles are the allowlist for provider/model and credential flow.
"""

import asyncio
import logging
import math

from app.core.config import get_settings
from app.core.logging import provider_request_var
from app.schemas.task import TaskCreate
from app.services.provider_secrets import SecretStoreUnavailable, provider_key


def _safe_sdk_log(record):
    return not provider_request_var.get()


async def _completion(**kwargs):
    import litellm

    # No telemetry/callback configuration is accepted from users or profiles.
    litellm.turn_off_message_logging = True
    for name in ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router"):
        for handler in logging.getLogger(name).handlers:
            handler.addFilter(_safe_sdk_log)
    litellm.telemetry = False
    litellm.set_verbose = False
    litellm.suppress_debug_info = True
    return await litellm.acompletion(**kwargs)


async def complete(body: TaskCreate, prompt: str) -> dict:
    if body.requires_privacy:
        return {"error": "Privacy requires local execution; external providers are denied"}
    settings = get_settings()
    profile = settings.provider_profiles.get(body.provider)
    if profile is None or body.model not in profile.models:
        return {"error": "Provider/model is not enabled by the controller administrator"}
    credential = None
    try:
        if profile.auth == "api_key":
            credential = provider_key(body.provider)
            if not credential:
                return {"error": "Configure the provider credential in Admin > Providers"}
        kwargs = {
            "model": body.model,
            "custom_llm_provider": profile.sdk_provider,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": body.max_tokens,
            "temperature": body.temperature,
            "stream": False,
            "num_retries": 0,
            "caching": False,
            "timeout": settings.task_dispatch_timeout_seconds,
            "drop_params": False,
        }
        if credential is not None:
            kwargs["api_key"] = credential
        if profile.api_base:
            kwargs["api_base"] = profile.api_base
        if profile.api_version:
            kwargs["api_version"] = profile.api_version
        if profile.region:
            if profile.sdk_provider == "bedrock":
                kwargs["aws_region_name"] = profile.region
            elif profile.sdk_provider == "vertex_ai":
                kwargs["vertex_location"] = profile.region
        if profile.project and profile.sdk_provider == "vertex_ai":
            kwargs["vertex_project"] = profile.project
        # Some SDKs put API keys in URLs; HTTP client INFO logs must not emit
        # those URLs. This request-local flag also follows child SDK tasks.
        log_token = provider_request_var.set(True)
        try:
            async with asyncio.timeout(settings.task_dispatch_timeout_seconds):
                response = await _completion(**kwargs)
        finally:
            provider_request_var.reset(log_token)
        data = response.model_dump() if hasattr(response, "model_dump") else response
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            return {"error": "Provider did not finish a plain-text answer; output not retained"}
        text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            return {"error": "Provider returned no text"}
        usage = {
            k: v
            for k, v in (data.get("usage") or {}).items()
            if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and v >= 0
        }
        return {
            "output": text.replace(credential, "[REDACTED]") if credential else text,
            "model": body.model,
            "node": None,
            "usage": usage,
            "routing": {
                "provider": body.provider,
                "execution_mode": "cloud",
                "executor": "controller",
                "reasons": ["explicit allowlisted provider/model"],
            },
        }
    except ImportError:
        return {"error": "Install the backend providers extra to enable this adapter"}
    except SecretStoreUnavailable:
        return {"error": "Provider credential store unavailable"}
    except Exception:
        # SDK errors may include prompts, request bodies, headers or credentials.
        return {"error": "Provider request failed; check configured model, access and limits"}
    finally:
        credential = None
