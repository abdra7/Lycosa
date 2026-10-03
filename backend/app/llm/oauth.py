"""OpenRouter OAuth PKCE (https://openrouter.ai/docs/use-cases/oauth-pkce).

The only provider sign-in Lycosa implements: it is documented, and it yields
an ordinary API key the user can revoke at OpenRouter. The code verifier
never leaves the controller: it travels inside a sealed, user-bound,
10-minute "flow" token, so no server-side state is needed and every worker
can complete any flow.
"""

import base64
import hashlib
import json
import secrets
import time
import uuid
from urllib.parse import urlencode, urlsplit

from app.llm.errors import LLMError, MalformedResponseError
from app.llm.http import Connection, request_json
from app.llm.netpolicy import PUBLIC_ONLY
from app.llm.vault import get_cipher

AUTHORIZE_URL = "https://openrouter.ai/auth"
EXCHANGE_URL = "https://openrouter.ai/api/v1/auth/keys"
FLOW_TTL_SECONDS = 600
_PURPOSE = "lycosa:oauth:openrouter:v1"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class OAuthFlowError(Exception):
    pass


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def validate_callback(url: str) -> str:
    """Only a loopback HTTP listener on the user's own machine may receive the code."""
    parts = urlsplit(url)
    if (
        parts.scheme != "http"
        or parts.hostname not in _LOOPBACK
        or not parts.port
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise OAuthFlowError("callback_url must be http://127.0.0.1:<port>/<path> on this device")
    return url


def start(user_id: uuid.UUID, callback_url: str | None) -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43..128
    flow = get_cipher().seal(
        json.dumps(
            {"v": verifier, "u": str(user_id), "exp": int(time.time()) + FLOW_TTL_SECONDS}
        ).encode(),
        _PURPOSE,
    )
    params = {"code_challenge": _challenge(verifier), "code_challenge_method": "S256"}
    if callback_url:
        params = {"callback_url": validate_callback(callback_url), **params}
    return f"{AUTHORIZE_URL}?{urlencode(params)}", flow


def _open_flow(flow: str, user_id: uuid.UUID) -> str:
    try:
        data = json.loads(get_cipher().unseal(flow, _PURPOSE))
    except ValueError:
        raise OAuthFlowError("Sign-in flow is invalid; start again") from None
    if data.get("u") != str(user_id):
        raise OAuthFlowError("Sign-in flow belongs to another user")
    if int(data.get("exp", 0)) < time.time():
        raise OAuthFlowError("Sign-in flow expired; start again")
    return data["v"]


async def exchange(flow: str, code: str, user_id: uuid.UUID) -> str:
    """Trade the authorization code for a user-controlled OpenRouter API key."""
    verifier = _open_flow(flow, user_id)
    conn = Connection(
        provider="openrouter", base_url="https://openrouter.ai/api/v1", policy=PUBLIC_ONLY
    )
    try:
        data = await request_json(
            conn,
            "POST",
            EXCHANGE_URL,
            headers={"Content-Type": "application/json"},
            json_body={"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
            max_bytes=64 * 1024,
        )
    except LLMError as exc:
        raise OAuthFlowError("OpenRouter did not accept the sign-in code") from exc
    key = data.get("key") if isinstance(data, dict) else None
    if not isinstance(key, str) or not key:
        raise OAuthFlowError("OpenRouter did not return a key") from MalformedResponseError()
    return key
