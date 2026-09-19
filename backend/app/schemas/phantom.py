"""A separate contract: never accepted by the persistent task/workflow path."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PhantomRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    prompt: str = Field(min_length=1, max_length=64000, repr=False)
    model: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    provider: Literal["phantom_local"] = "phantom_local"
    max_tokens: int = Field(default=512, ge=1, le=4096)
    temperature: float = Field(default=0.2, ge=0, le=1)


class PhantomResponse(BaseModel):
    output: str
    persisted: Literal[False] = False
    cleanup: Literal["confirmed"] = "confirmed"
    privacy_boundary: str = "application-no-content-retention; not forensic zero-trace"
