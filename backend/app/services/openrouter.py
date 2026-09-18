"""Controller-owned OpenRouter execution: fixed origin and free-model boundary."""

import httpx

from app.core.config import get_settings
from app.schemas.task import TaskCreate
from app.services.provider_secrets import SecretStoreUnavailable, provider_key

MODEL = "nvidia/nemotron-3.5-lightning:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


async def complete(body: TaskCreate, prompt: str) -> dict:
    if body.requires_privacy:
        return {"error": "Privacy requires local execution; OpenRouter is external"}
    if body.model != MODEL:
        return {"error": f"This OpenRouter integration only allows {MODEL}"}
    credential = None
    try:
        credential = provider_key("openrouter")
        if not credential:
            return {"error": "Add an OpenRouter API key in Admin > Providers"}
        async with httpx.AsyncClient(
            timeout=get_settings().task_dispatch_timeout_seconds, follow_redirects=False
        ) as client:
            response = await client.post(
                ENDPOINT,
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": body.max_tokens,
                    "temperature": body.temperature,
                    "stream": False,
                    "provider": {"max_price": {"prompt": 0, "completion": 0}},
                },
            )
        if not response.is_success:
            return {
                "error": f"OpenRouter request failed (HTTP {response.status_code}); "
                "check key, quota or model availability"
            }
        data = response.json()
        if data["choices"][0].get("finish_reason") == "length":
            return {"error": "OpenRouter output was truncated; retry with a larger output budget"}
        output = data["choices"][0]["message"]["content"]
        if not isinstance(output, str) or not output.strip():
            return {"error": "OpenRouter returned no text; retry with a larger output budget"}
        # Persist only allowlisted numeric usage, never raw provider responses.
        usage = {
            k: v
            for k, v in data.get("usage", {}).items()
            if k in {"prompt_tokens", "completion_tokens", "total_tokens", "cost"}
            and isinstance(v, (int, float))
            and not isinstance(v, bool)
        }
        return {
            "output": output.replace(credential, "[REDACTED]"),
            "model": MODEL,
            "node": None,
            "usage": usage,
            "routing": {
                "provider": "openrouter",
                "execution_mode": "cloud",
                "executor": "controller",
                "reasons": [
                    "explicit provider selection",
                    "fixed HTTPS origin",
                    "exact free model and zero-price provider cap",
                ],
            },
        }
    except SecretStoreUnavailable:
        return {
            "error": "Provider vault unavailable; configure the OS vault "
            "or explicitly use a single-worker session key"
        }
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return {
            "error": "OpenRouter request failed; no provider response or credential was retained"
        }
    finally:
        credential = None
