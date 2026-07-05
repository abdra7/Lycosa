import platform

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Agent configuration. Env vars use the LYCOSA_ prefix
    (e.g. LYCOSA_CONTROLLER_URL, LYCOSA_API_KEY)."""

    model_config = SettingsConfigDict(
        env_prefix="LYCOSA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    controller_url: str = "http://localhost:8000"
    api_key: str = ""
    node_name: str = Field(default_factory=platform.node)
    heartbeat_interval_seconds: int = 15

    # local execution API
    exec_host: str = "0.0.0.0"
    exec_port: int = 8010
    # URL the controller should use to reach this agent; autodetected if unset
    advertise_url: str | None = None

    ollama_url: str = "http://localhost:11434"
