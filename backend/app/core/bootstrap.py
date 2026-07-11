"""First-run secret bootstrap (ADR-022).

A fresh clone must run with zero configuration: when JWT_SECRET or the
default admin password are unset or left at a known placeholder, generate
strong values once and persist them under the data dir, so restarts — and
the seed script vs the API process — agree on the same secrets.
"""

import json
import logging
import secrets
from pathlib import Path

logger = logging.getLogger("lycosa.bootstrap")

RUNTIME_SECRETS_FILENAME = "runtime-secrets.json"

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
