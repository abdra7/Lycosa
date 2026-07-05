import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class LoginRequest(BaseModel):
    # plain str, not EmailStr: login authenticates an existing identity, and
    # email-validator rejects LAN-style domains like admin@lycosa.local
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds


class PrincipalOut(BaseModel):
    """The authenticated caller — a user (JWT) or a service/node (API key)."""

    type: Literal["user", "api_key"]
    id: uuid.UUID
    role: str
    email: str | None = None  # users only
    name: str | None = None  # api keys only


class AuditLogOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    created_at: datetime
    actor_user_id: uuid.UUID | None
    actor_api_key_id: uuid.UUID | None
    action: str
    resource_type: str | None
    resource_id: str | None
    detail: dict[str, Any] | None
    ip_address: str | None
