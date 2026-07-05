from app.models.task import TaskType
from app.services.classifier import classify, preferred_roles


def test_explicit_type_wins_over_keywords() -> None:
    assert classify("fix this python bug", TaskType.VISION) == TaskType.VISION


def test_coding_keywords() -> None:
    assert classify("Refactor this function to remove the bug") == TaskType.CODING


def test_retrieval_keywords() -> None:
    assert classify("Search the Flutter documentation for state management") == TaskType.RETRIEVAL


def test_vision_keywords() -> None:
    assert classify("Describe what is in this image") == TaskType.VISION


def test_tool_keywords() -> None:
    assert classify("Execute command to list the directory") == TaskType.TOOL


def test_default_is_general() -> None:
    assert classify("Tell me a story about a spider") == TaskType.GENERAL


def test_every_type_has_role_preferences() -> None:
    for task_type in TaskType:
        roles = preferred_roles(task_type)
        assert roles, task_type
