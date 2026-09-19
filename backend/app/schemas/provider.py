"""Deployment-owned provider configuration; no request-controlled endpoints/keys."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sdk_provider: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=60)
    models: list[str] = Field(min_length=1, max_length=200)
    # Only explicit workload identity may use the SDK's host credential chain.
    auth: str = Field(default="api_key", pattern=r"^(api_key|workload_identity)$")
    api_base: str | None = None
    api_version: str | None = None
    region: str | None = None
    project: str | None = None

    @model_validator(mode="after")
    def validate_policy(self):
        if self.api_base:
            url = urlsplit(self.api_base)
            if (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError("Provider endpoint must be credential-free HTTPS")
        if self.auth == "workload_identity" and self.sdk_provider not in {"bedrock", "vertex_ai"}:
            raise ValueError("Workload identity is supported for Bedrock and Vertex AI only")
        if any(not m.strip() or len(m) > 250 or "://" in m for m in self.models):
            raise ValueError("Invalid model allowlist")
        return self
