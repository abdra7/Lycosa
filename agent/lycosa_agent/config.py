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
    # announce this agent over mDNS so the dashboard's LAN scan can find it
    discovery_enabled: bool = True

    ollama_url: str = "http://localhost:11434"
    cloud_execution_enabled: bool = False
    # zero-config model setup: on first run with an empty Ollama, pull the
    # best-fit model for this hardware automatically (LYCOSA_AUTO_PULL_MODEL=false to opt out)
    auto_pull_model: bool = True

    # Claude subscription runtime (ADR-032): runs this machine's own Claude
    # Code, signed in by its owner with `claude auth login`. Off by default;
    # serves only the Lycosa user named in claude_subscription_owner.
    claude_subscription: bool = False
    claude_subscription_owner: str = ""
    claude_binary: str = "claude"
    claude_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    claude_max_concurrency: int = Field(default=1, ge=1, le=4)
