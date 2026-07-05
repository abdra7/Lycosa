from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (and .env locally)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "info"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://lycosa:change-me@localhost:5432/lycosa"
    qdrant_url: str = "http://localhost:6333"

    # fallback for local dev only; must be >= 32 bytes for HS256 (RFC 7518)
    jwt_secret: str = "insecure-dev-only-secret-change-me-in-env"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    default_admin_email: str = "admin@lycosa.local"
    default_admin_password: str = "change-me"

    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120  # per window, per API key / client IP
    rate_limit_window_seconds: int = 60

    # node liveness (ADR-011): timeout should be ~3x the agent interval
    agent_heartbeat_interval_seconds: int = 15
    heartbeat_timeout_seconds: int = 45
    offline_sweep_interval_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
