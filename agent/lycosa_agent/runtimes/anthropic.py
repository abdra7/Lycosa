"""Anthropic translation; credentials are scoped to one call and never cached."""

import httpx

from lycosa_agent.runtimes.chat import ProviderError, RuntimeRequest, RuntimeResponse


class AnthropicAdapter:
    name = "anthropic"

    async def complete(
        self, request: RuntimeRequest, credential: str | None = None
    ) -> RuntimeResponse:
        if not credential:
            raise ProviderError("Provider credential unavailable")
        messages = [m.model_dump() for m in request.messages if m.role != "system"]
        system = "\n\n".join(m.content for m in request.messages if m.role == "system")
        payload = {
            "model": request.model.removeprefix("anthropic/"),
            "messages": messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system:
            payload["system"] = system
        result = None
        # The HTTP client (including its request headers) is never retained.
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": credential, "anthropic-version": "2023-06-01"},
                    json=payload,
                )
                if response.is_success:
                    data = response.json()
                    result = RuntimeResponse(
                        output="".join(
                            b["text"] for b in data["content"] if b["type"] == "text"
                        ).replace(credential, "[REDACTED]"),
                        provider=self.name,
                        model=request.model,
                        usage={
                            k: int(v)
                            for k, v in data.get("usage", {}).items()
                            if k in ("input_tokens", "output_tokens")
                        },
                    )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            pass
        finally:
            credential = ""
        if result is None:
            raise ProviderError("Cloud provider request failed")
        return result
