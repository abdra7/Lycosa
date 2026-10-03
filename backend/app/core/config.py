import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.core.bootstrap import (
    WEAK_DB_PASSWORDS,
    ensure_runtime_secrets,
    is_placeholder,
)
from app.llm.netpolicy import DEFAULT_LOCAL_NETWORKS, parse_networks
from app.schemas.provider import ProviderProfile

logger = logging.getLogger("lycosa.config")


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (and .env locally)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "info"

    # writable state (first-run generated secrets); a volume in Docker
    data_dir: str = "./data"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://lycosa:lycosa@localhost:5432/lycosa"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # fallback for local dev only; must be >= 32 bytes for HS256 (RFC 7518)
    jwt_secret: str = "insecure-dev-only-secret-change-me-in-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    default_admin_email: str = "admin@lycosa.local"
    default_admin_password: str = "change-me"

    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120  # per window, per API key / client IP
    rate_limit_window_seconds: int = 60

    # shared throttle state (ADR-027): empty = in-process buckets (single-worker
    # behavior, no Redis needed); set e.g. redis://redis:6379/0 so rate-limit and
    # failed-login windows are shared across uvicorn workers.
    redis_url: str = ""

    # uvicorn worker processes (ADR-028). >1 requires REDIS_URL — without a
    # shared store the throttles run at N× their limits and WebSocket events
    # stay worker-local, so startup fails fast instead.
    workers: int = 1

    # reverse-proxy support (ADR-028): comma-separated IPs/CIDRs whose
    # X-Forwarded-For header is honored when resolving the client IP that
    # keys the rate limiter and login guard. Empty = header ignored.
    trusted_proxies: str = ""

    # brute-force throttle on /auth/login (ADR-023): after this many failed
    # attempts from one IP within the window, further logins get 429 until the
    # window clears. A successful login resets the counter. 0 disables it.
    auth_max_failed_logins: int = 10
    auth_login_window_seconds: int = 300

    # node liveness (ADR-011): timeout should be ~3x the agent interval
    agent_heartbeat_interval_seconds: int = 5
    heartbeat_timeout_seconds: int = 15
    offline_sweep_interval_seconds: int = 5

    # task dispatch (ADR-012)
    task_dispatch_timeout_seconds: int = 120
    task_max_attempts: int = 3
    # Nonsecret policy. Keys live only in the controller account's OS vault.
    cloud_allowed_node_ids: list[str] = []
    cloud_node_origins: dict[str, str] = {}
    cloud_models: list[str] = []
    routing_vram_safety_margin_mb: int = 512

    # New cloud adapters are opt-in. Exact provider/model policy is admin-owned.
    provider_profiles: dict[str, ProviderProfile] = {}

    # Universal LLM layer (ADR-031). Account credentials are AES-256-GCM
    # encrypted in the database with this key (urlsafe base64 of 32 bytes);
    # empty = generated once under data_dir. "keyring" stores them in the
    # controller's OS vault instead.
    credential_encryption_key: str = ""
    llm_credential_store: str = Field(default="db", pattern=r"^(db|keyring)$")
    # private networks a local runtime endpoint (Ollama, LM Studio, vLLM,
    # custom) may use: deployment accounts and admin-owned accounts…
    llm_local_networks: str = DEFAULT_LOCAL_NETWORKS
    # …and accounts owned by non-admin users (empty = public endpoints only)
    llm_user_endpoint_networks: str = ""
    llm_request_timeout_seconds: int = Field(default=120, ge=1, le=600)
    # wall clock for one call across all retries and fallbacks; stays under
    # the dashboard's task timeout (7 min)
    llm_total_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_models_cache_seconds: int = Field(default=300, ge=0, le=86400)
    llm_pricing_file: str = ""  # empty = config/llm_pricing.yml

    # Isolated local-only inference; unavailable unless deliberately provisioned.
    phantom_enabled: bool = False
    phantom_models: dict[str, str] = {}  # alias -> absolute preinstalled GGUF file
    phantom_image: str = "lycosa-phantom:local"
    phantom_timeout_seconds: int = Field(default=120, ge=1, le=600)
    phantom_memory_mb: int = Field(default=4096, ge=256, le=131072)
    phantom_cpus: float = Field(default=2, ge=0.5, le=32)
    phantom_max_concurrent: int = Field(default=1, ge=1, le=8)

    # knowledge plane (ADR-013)
    embedding_backend: str = "hashing"  # hashing | fastembed
    embedding_dim: int = 384
    # grounding (ADR-019): drop retrieved chunks scoring below this before they
    # reach an LLM. 0.0 keeps every chunk (the /retrieve API default); the
    # task-grounding path in the orchestrator applies it so out-of-scope queries
    # yield no context and trigger the grounded refusal instead of hallucination.
    retrieval_min_score: float = 0.0


def apply_runtime_secrets(settings: Settings) -> None:
    """Zero-config bootstrap (ADR-022): replace placeholder JWT/admin secrets
    with generated values persisted under the data dir."""
    need_jwt = is_placeholder(settings.jwt_secret)
    need_admin = is_placeholder(settings.default_admin_password)
    if not (need_jwt or need_admin):
        return
    data = ensure_runtime_secrets(settings.data_dir, need_jwt=need_jwt, need_admin=need_admin)
    if need_jwt:
        settings.jwt_secret = data["jwt_secret"]
    if need_admin:
        settings.default_admin_password = data["admin_password"]


def enforce_production_secrets(settings: Settings) -> None:
    """Fail fast on default/placeholder secrets when ENVIRONMENT=production (issue #7).

    JWT/admin secrets are auto-generated by apply_runtime_secrets, so in the
    normal startup path only the DB password can still be weak here; the auth
    checks remain as defense-in-depth.
    """
    if settings.environment.strip().lower() != "production":
        return
    problems = []
    try:
        db_password = make_url(settings.database_url).password or ""
    except Exception:
        db_password = ""
    if db_password in WEAK_DB_PASSWORDS:
        problems.append("DATABASE_URL/POSTGRES_PASSWORD uses a default password")
    if is_placeholder(settings.jwt_secret):
        problems.append("JWT_SECRET is a placeholder")
    if is_placeholder(settings.default_admin_password):
        problems.append("DEFAULT_ADMIN_PASSWORD is a placeholder")
    if problems:
        raise RuntimeError(
            "refusing to start with ENVIRONMENT=production: "
            + "; ".join(problems)
            + " — set strong values in .env (see .env.example)"
        )


def enforce_multiworker_prereqs(settings: Settings) -> None:
    """Fail fast when WORKERS>1 without REDIS_URL (ADR-028): every worker
    would keep its own throttle buckets (N× the configured limits — a security
    regression for the login guard) and events would only reach WebSocket
    clients on the worker that published them."""
    if settings.workers > 1 and not settings.redis_url:
        raise RuntimeError(
            f"refusing to start with WORKERS={settings.workers}: multi-worker "
            "needs shared state — set REDIS_URL (see .env.example and the "
            "'redis' compose profile) or run WORKERS=1"
        )


def enforce_llm_settings(settings: Settings) -> None:
    """Fail fast on a malformed credential key or network list (ADR-031):
    discovering it at first use would strand every stored credential."""
    from app.llm.vault import decode_key

    if settings.credential_encryption_key:
        try:
            decode_key(settings.credential_encryption_key)
        except ValueError:
            raise RuntimeError(
                "CREDENTIAL_ENCRYPTION_KEY must be urlsafe base64 of exactly 32 bytes"
            ) from None
    for name in ("llm_local_networks", "llm_user_endpoint_networks"):
        try:
            parse_networks(getattr(settings, name))
        except ValueError:
            raise RuntimeError(f"{name.upper()} must be comma-separated IP networks") from None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    apply_runtime_secrets(settings)
    enforce_production_secrets(settings)
    enforce_multiworker_prereqs(settings)
    enforce_llm_settings(settings)
    return settings
