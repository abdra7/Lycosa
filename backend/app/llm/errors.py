"""Normalized LLM errors.

Every error carries a fixed public message. Provider response bodies are read
only to classify the failure and are never copied into a message, a log line
or a persisted row: they can echo prompts, headers or credentials.
"""

import email.utils
import time
from datetime import UTC
from typing import Any

MAX_RETRY_AFTER_SECONDS = 60.0


class LLMError(Exception):
    code = "llm_error"
    message = "LLM request failed"
    retryable = False
    http_status = 502  # upstream failures are never reported as the caller's 401/403

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.public_message = message or self.message
        self.provider = provider
        self.status = status
        self.retry_after = retry_after
        super().__init__(self.public_message)


class AuthenticationError(LLMError):
    code = "provider_auth_failed"
    message = "The provider rejected the credential; check or replace the API key"


class PermissionDeniedError(LLMError):
    code = "provider_permission_denied"
    message = "The credential is not permitted to use this model or endpoint"


class QuotaExceededError(LLMError):
    code = "provider_quota_exceeded"
    message = "The provider account has no remaining quota or balance"


class RateLimitError(LLMError):
    code = "provider_rate_limited"
    message = "The provider rate-limited the request"
    retryable = True
    http_status = 429


class LLMTimeoutError(LLMError):
    code = "provider_timeout"
    message = "The provider did not respond in time"
    retryable = True
    http_status = 504


class ProviderUnavailableError(LLMError):
    code = "provider_unavailable"
    message = "The provider is unavailable or overloaded"
    retryable = True
    http_status = 503


class InvalidRequestError(LLMError):
    code = "provider_invalid_request"
    message = "The provider rejected the request parameters"
    http_status = 422


class ModelNotFoundError(LLMError):
    code = "model_not_found"
    message = "The model does not exist or is not available to this account"
    http_status = 422


class ContextLengthError(LLMError):
    code = "context_length_exceeded"
    message = "The request exceeds the model's context window"
    http_status = 422


class ToolCallingError(LLMError):
    code = "tool_call_invalid"
    message = "The provider returned a malformed tool call"


class UnsupportedCapabilityError(LLMError):
    code = "capability_unsupported"
    message = "The selected provider or model does not support a requested feature"
    http_status = 422


class EndpointNotAllowedError(LLMError):
    code = "endpoint_not_allowed"
    message = "The provider endpoint is not permitted by the controller network policy"
    http_status = 422


class MalformedResponseError(LLMError):
    code = "provider_malformed_response"
    message = "The provider returned a response Lycosa could not interpret"


class ResponseTooLargeError(LLMError):
    code = "provider_response_too_large"
    message = "The provider response exceeded Lycosa's size limit"


class CredentialUnavailableError(LLMError):
    code = "credential_unavailable"
    message = "The account credential is missing or cannot be decrypted"
    http_status = 409


class NoRouteError(LLMError):
    code = "no_llm_route"
    message = "No usable provider account/model is configured for this request"
    http_status = 409


_CONTEXT_HINTS = (
    "context length",
    "context_length",
    "context window",
    "maximum context",
    "prompt is too long",
    "too many tokens",
    "exceeds the maximum number of tokens",
    "input is too long",
)
_MODEL_HINTS = (
    "model_not_found",
    "model not found",
    "does not exist",
    "unknown model",
    "no such model",
)
_QUOTA_HINTS = (
    "insufficient_quota",
    "insufficient balance",
    "exceeded your current quota",
    "credits",
)


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After as delta-seconds or an HTTP date, clamped to a sane bound."""
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = when.timestamp() - time.time()
    if seconds != seconds:  # NaN
        return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS))


def _error_signals(body: Any) -> tuple[str, str]:
    """(code/type, message) lowered, from the common error envelopes, for
    classification only."""
    if isinstance(body, list) and body:
        body = body[0]
    if not isinstance(body, dict):
        return "", ""
    err = body.get("error", body)
    if isinstance(err, str):
        return "", err.lower()[:2000]
    if not isinstance(err, dict):
        return "", ""
    code = " ".join(str(err.get(k) or "") for k in ("code", "type", "status")).lower()
    message = str(err.get("message") or "").lower()[:2000]
    return code, message


def from_http(
    status: int, body: Any, *, provider: str, headers: dict[str, str] | None = None
) -> LLMError:
    """Classify an HTTP error response into a normalized error."""
    code, message = _error_signals(body)
    signals = f"{code} {message}"
    retry_after = parse_retry_after((headers or {}).get("retry-after"))
    kw: dict[str, Any] = {"provider": provider, "status": status}

    if any(h in signals for h in _CONTEXT_HINTS):
        return ContextLengthError(**kw)
    if status == 401:
        return AuthenticationError(**kw)
    if status == 402 or (status in (403, 429) and any(h in signals for h in _QUOTA_HINTS)):
        return QuotaExceededError(**kw)
    if status == 403:
        return PermissionDeniedError(**kw)
    if status == 404 or any(h in signals for h in _MODEL_HINTS):
        return ModelNotFoundError(**kw)
    if status == 408:
        return LLMTimeoutError(**kw)
    if status == 429:
        return RateLimitError(retry_after=retry_after, **kw)
    if status in (400, 409, 413, 415, 422):
        return InvalidRequestError(**kw)
    if status >= 500:  # includes Anthropic's 529 overloaded
        return ProviderUnavailableError(retry_after=retry_after, **kw)
    return LLMError(**kw)


def from_stream_payload(payload: Any, *, provider: str) -> LLMError:
    """Classify an error delivered inside a 200 stream (SSE/NDJSON)."""
    code, message = _error_signals(payload)
    signals = f"{code} {message}"
    if "overloaded" in signals or "unavailable" in signals or "internal" in signals:
        return ProviderUnavailableError(provider=provider)
    if "rate_limit" in signals or "rate limit" in signals:
        return RateLimitError(provider=provider)
    if any(h in signals for h in _CONTEXT_HINTS):
        return ContextLengthError(provider=provider)
    if any(h in signals for h in _MODEL_HINTS) or "not found" in signals:
        return ModelNotFoundError(provider=provider)
    if "auth" in signals or "api key" in signals:
        return AuthenticationError(provider=provider)
    return LLMError(provider=provider)
