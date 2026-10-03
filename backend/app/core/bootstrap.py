"""First-run secret bootstrap (ADR-022).

A fresh clone must run with zero configuration: when JWT_SECRET or the
default admin password are unset or left at a known placeholder, generate
strong values once and persist them under the data dir, so restarts — and
the seed script vs the API process — agree on the same secrets.
"""

import base64
import json
import logging
import os
import secrets
import time
from pathlib import Path

logger = logging.getLogger("lycosa.bootstrap")

RUNTIME_SECRETS_FILENAME = "runtime-secrets.json"
CREDENTIAL_KEY_FILENAME = "credential-encryption.key"

# every placeholder shipped in .env.example, config.py defaults, or docs
PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "change-me",
        "change-me-to-a-long-random-value",
        "insecure-dev-only-secret-change-me-in-env",
    }
)

# committed compose defaults for the localhost-bound postgres — fine on a dev
# box, never production-worthy (see infra/compose-defaults.env)
WEAK_DB_PASSWORDS = PLACEHOLDER_SECRETS | {"lycosa", "postgres"}


def is_placeholder(value: str) -> bool:
    return value.strip() in PLACEHOLDER_SECRETS


def load_runtime_secrets(data_dir: str | Path) -> dict:
    path = Path(data_dir) / RUNTIME_SECRETS_FILENAME
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("could not read %s; its secrets will be regenerated", path)
    return {}


def ensure_runtime_secrets(data_dir: str | Path, *, need_jwt: bool, need_admin: bool) -> dict:
    """Return persisted first-run secrets, generating the requested ones if missing."""
    data = load_runtime_secrets(data_dir)
    changed = False
    if need_jwt and not data.get("jwt_secret"):
        data["jwt_secret"] = secrets.token_hex(32)  # 64 chars, >= 32 bytes for HS256
        changed = True
    if need_admin and not data.get("admin_password"):
        data["admin_password"] = secrets.token_urlsafe(12)
        data["admin_password_generated"] = True
        changed = True
    if changed:
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / RUNTIME_SECRETS_FILENAME
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # best-effort; not meaningful on Windows
            pass
        logger.info("generated first-run secret(s), persisted to %s", path)
    return data


def _valid_key(value: str) -> bool:
    try:
        return len(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))) == 32
    except ValueError:
        return False


def ensure_credential_key(data_dir: str | Path) -> str:
    """The LLM credential encryption key (ADR-031), generated on first use.

    Created with O_EXCL so that concurrent uvicorn workers converge on the
    first writer's key instead of each persisting their own; a worker that
    loses the race waits for the winner's content. Losing this file makes the
    stored credentials undecryptable: back up the data directory with the
    database.
    """
    path = Path(data_dir) / CREDENTIAL_KEY_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        for _ in range(100):
            value = path.read_text(encoding="ascii").strip()
            if _valid_key(value):
                return value
            time.sleep(0.05)  # another worker is still writing it
        raise RuntimeError(f"{path} does not hold a valid credential key") from None
    value = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(value + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    logger.info("generated the LLM credential encryption key at %s", path)
    return value
