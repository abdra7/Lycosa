"""All ORM models. Importing this package registers every table on Base.metadata
(required for Alembic autogenerate and metadata.create_all in tests)."""

from app.models.audit import AuditLog
from app.models.knowledge import (
    Document,
    DocumentStatus,
    EmbeddingJob,
    JobStatus,
    KnowledgeCollection,
    RetrievalRequest,
)
from app.models.node import Agent, AgentCapability, Node, NodeStatus
from app.models.task import ExecutionStatus, Task, TaskExecution, TaskStatus, TaskType
from app.models.user import ApiKey, Role, Session, User

__all__ = [
    "Agent",
    "AgentCapability",
    "ApiKey",
    "AuditLog",
    "Document",
    "DocumentStatus",
    "EmbeddingJob",
    "ExecutionStatus",
    "JobStatus",
    "KnowledgeCollection",
    "Node",
    "NodeStatus",
    "RetrievalRequest",
    "Role",
    "Session",
    "Task",
    "TaskExecution",
    "TaskStatus",
    "TaskType",
    "User",
]
