"""Task classification: what kind of work is this, and which node roles fit.

v1 is transparent keyword matching; an LLM-based classifier can replace
`classify` behind the same signature later (ADR-012).
"""

from app.models.task import TaskType

# checked in order; first type with a keyword hit wins
_KEYWORDS: list[tuple[TaskType, tuple[str, ...]]] = [
    (
        TaskType.CODING,
        ("code", "function", "bug", "refactor", "python", "compile", "unit test", "script"),
    ),
    (
        TaskType.RETRIEVAL,
        ("retrieve", "search", "documentation", "docs", "look up", "find information"),
    ),
    (
        TaskType.VISION,
        ("image", "photo", "picture", "detect object", "camera", "screenshot", "ocr"),
    ),
    (TaskType.TOOL, ("run tool", "execute command", "shell", "webhook")),
]

# preference order per type: first role is the best fit; used by the scheduler
ROLE_PREFERENCES: dict[TaskType, list[str]] = {
    TaskType.CODING: ["ai_compute", "hybrid"],
    TaskType.RETRIEVAL: ["knowledge", "hybrid"],
    TaskType.TOOL: ["tool", "hybrid"],
    TaskType.VISION: ["vision", "hybrid"],
    TaskType.GENERAL: ["hybrid", "ai_compute", "knowledge", "tool"],
}


def classify(prompt: str, requested: TaskType | None = None) -> TaskType:
    """Explicit type wins; otherwise first keyword match; otherwise general."""
    if requested is not None:
        return requested
    lowered = prompt.lower()
    for task_type, keywords in _KEYWORDS:
        if any(keyword in lowered for keyword in keywords):
            return task_type
    return TaskType.GENERAL


def preferred_roles(task_type: TaskType) -> list[str]:
    return ROLE_PREFERENCES[task_type]
