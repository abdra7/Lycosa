"""Provider-neutral, bounded chat execution contract for Sprint 12."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=200000)


class RuntimeRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)
    temperature: float = Field(default=0.2, ge=0, le=1)
    max_tokens: int = Field(default=4096, ge=1, le=16384)
    stream: Literal[False] = False
    tools: list = Field(default_factory=list, max_length=0)
    agent_loop: Literal[False] = False


class RuntimeResponse(BaseModel):
    output: str
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


class ProviderError(Exception):
    """Only fixed, public messages: no headers, requests, or response bodies."""
