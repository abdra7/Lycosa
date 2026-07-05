import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.schemas.workflow import WorkflowDefinition
from tests.conftest import ADMIN_EMAIL, bearer, login


def definition(*steps: dict) -> dict:
    return {"steps": list(steps)}


PLAN = {"id": "plan", "kind": "task", "prompt": "Plan: {{input}}"}


def test_valid_definition_accepted() -> None:
    WorkflowDefinition.model_validate(
        definition(
            PLAN,
            {"id": "ctx", "kind": "retrieve", "query": "{{input}}"},
            {"id": "gate", "kind": "approval", "message": "check"},
            {
                "id": "code",
                "kind": "task",
                "prompt": "Do {{steps.plan.output}} with {{steps.ctx.output}}",
                "when": {"step": "plan", "contains": "PLAN"},
            },
        )
    )


def test_duplicate_step_ids_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate step id"):
        WorkflowDefinition.model_validate(definition(PLAN, dict(PLAN)))


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        WorkflowDefinition.model_validate(
            definition({"id": "x", "kind": "teleport", "prompt": "p"})
        )


def test_when_referencing_later_step_rejected() -> None:
    with pytest.raises(ValidationError, match="undefined or later"):
        WorkflowDefinition.model_validate(
            definition(
                {
                    "id": "first",
                    "kind": "task",
                    "prompt": "p",
                    "when": {"step": "second", "contains": "x"},
                },
                {"id": "second", "kind": "task", "prompt": "p"},
            )
        )


def test_template_referencing_unknown_step_rejected() -> None:
    with pytest.raises(ValidationError, match="undefined or later"):
        WorkflowDefinition.model_validate(
            definition({"id": "a", "kind": "task", "prompt": "{{steps.ghost.output}}"})
        )


def test_when_requires_exactly_one_matcher() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        WorkflowDefinition.model_validate(
            definition(
                PLAN,
                {"id": "b", "kind": "task", "prompt": "p", "when": {"step": "plan"}},
            )
        )


def test_parallel_substep_ids_join_the_namespace() -> None:
    with pytest.raises(ValidationError, match="duplicate step id"):
        WorkflowDefinition.model_validate(
            definition(
                PLAN,
                {
                    "id": "fan",
                    "kind": "parallel",
                    "steps": [{"id": "plan", "kind": "task", "prompt": "p"}],
                },
            )
        )


async def test_api_rejects_invalid_definition(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    response = await client.post(
        "/api/v1/workflows",
        json={
            "name": "bad-wf",
            "definition": definition({"id": "a", "kind": "task", "prompt": "{{steps.b.output}}"}),
        },
        headers=bearer(token),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_api_duplicate_name_is_409(client: AsyncClient, users: dict) -> None:
    token = await login(client, ADMIN_EMAIL)
    body = {"name": "dup-wf", "definition": definition(PLAN)}
    assert (
        await client.post("/api/v1/workflows", json=body, headers=bearer(token))
    ).status_code == 201
    assert (
        await client.post("/api/v1/workflows", json=body, headers=bearer(token))
    ).status_code == 409
