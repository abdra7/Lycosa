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
    # exec API contact info (sent by the Local Agent; ADR-011)
    agent_url: str | None = Field(default=None, max_length=255)
    agent_token: str | None = Field(default=None, min_length=16, max_length=128)


class GpuMetrics(BaseModel):
    util_percent: float | None = Field(default=None, ge=0, le=100)
    mem_used_gb: float | None = Field(default=None, ge=0)
    temp_c: float | None = None


class NodeMetrics(BaseModel):
    cpu_percent: float = Field(ge=0, le=100)
    ram_percent: float = Field(ge=0, le=100)
    ram_used_gb: float | None = Field(default=None, ge=0)
    disk_percent: float | None = Field(default=None, ge=0, le=100)
    gpus: list[GpuMetrics] = []
    running_tasks: int = Field(default=0, ge=0)


class HeartbeatRequest(BaseModel):
    metrics: NodeMetrics


class HeartbeatResponse(BaseModel):
    status: str = "ok"
    heartbeat_interval_seconds: int


class NodePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: NodeRole | None = None


class ModelInstallRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)  # Ollama tag, e.g. "llama3.1:8b"


class ModelInstallResponse(BaseModel):
    status: str  # "succeeded"
    models: list[str]  # models now installed on the node


class NodeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    status: NodeStatus
    role: str | None
    recommended_role: str | None
    recommendation_confidence: float | None
    recommendation_rationale: list[str] | None
    cpu_cores: int | None
    ram_gb: float | None
    gpu_count: int | None
    gpu_vram_gb: float | None
    storage_gb: float | None
    os_name: str | None
    hardware_profile: dict[str, Any] | None
    last_heartbeat_at: datetime | None
    metrics: dict[str, Any] | None
    agent_url: str | None  # agent_token is deliberately never exposed
    created_at: datetime
    updated_at: datetime
