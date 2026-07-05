import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.node import NodeRole, NodeStatus


class GpuInfo(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    vram_gb: float = Field(gt=0)
    count: int = Field(default=1, ge=1)


class OsInfo(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    version: str | None = Field(default=None, max_length=50)
    arch: str | None = Field(default=None, max_length=20)


class RuntimeInfo(BaseModel):
    name: str = Field(min_length=1, max_length=50)  # "ollama", "llama.cpp", ...
    version: str | None = Field(default=None, max_length=50)
    models: list[str] = []


class HardwareProfile(BaseModel):
    cpu_model: str = Field(min_length=1, max_length=200)
    cpu_cores: int = Field(ge=1, description="Logical cores")
    ram_gb: float = Field(gt=0)
    gpus: list[GpuInfo] = []  # empty list = CPU-only node
    storage_gb: float = Field(gt=0)
    storage_type: str | None = Field(default=None, max_length=20)  # nvme / ssd / hdd
    os: OsInfo
    runtimes: list[RuntimeInfo] = []
    extra: dict[str, Any] = {}  # forward-compat escape hatch


class NodeRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    hardware_profile: HardwareProfile


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: NodeRole | None = None


class NodeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    status: NodeStatus
    role: str | None
    cpu_cores: int | None
    ram_gb: float | None
    gpu_count: int | None
    gpu_vram_gb: float | None
    storage_gb: float | None
    os_name: str | None
    hardware_profile: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
