import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.user import ALL_ROLES


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default="node", pattern=f"^({'|'.join(ALL_ROLES)})$")
    expires_at: datetime | None = None


class ApiKeyOut(BaseModel):
    """Listing shape: prefix only, never the key or its hash."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    key_prefix: str
    node_id: uuid.UUID | None
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyCreatedOut(ApiKeyOut):
    """Creation response: the only time the full key is ever returned."""

    api_key: str
    role: str
