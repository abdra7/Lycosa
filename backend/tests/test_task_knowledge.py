"""Knowledge injection into task dispatch: the agent receives retrieved
context in its prompt but never learns where the knowledge came from."""

import respx
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ADMIN_EMAIL, bearer, login, make_node
from tests.test_knowledge_ingest import create_collection, upload

VENOM_DOC = b"Lycosa venom is mild and causes only minor swelling in humans."


@respx.mock(assert_all_mocked=False)
async def test_knowledge_query_injects_context_into_prompt(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    collection = await create_collection(client, token, name="venom-facts")
    await upload(client, token, collection["id"], "venom.txt", VENOM_DOC)

    await make_node(db_session, "llm-box", role="hybrid")
    route = respx_mock.post("http://llm-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "answered"})
    )

    response = await client.post(
        "/api/v1/tasks",
        json={
            "prompt": "Is a wolf spider bite dangerous?",
            "knowledge_query": "lycosa venom effects on humans",
        },
        headers=bearer(token),
    )
    assert response.status_code == 201
    assert response.json()["status"] == "succeeded"

    dispatched = route.calls.last.request.read().decode()
    assert "Lycosa venom is mild" in dispatched  # retrieved context injected
    assert "Is a wolf spider bite dangerous?" in dispatched  # original task kept
    assert "venom-facts" in dispatched  # source labeled for the model
    assert "qdrant" not in dispatched.lower()  # storage location never leaks


@respx.mock(assert_all_mocked=False)
async def test_retrieval_type_task_auto_uses_prompt_as_query(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    collection = await create_collection(client, token, name="venom-facts")
    await upload(client, token, collection["id"], "venom.txt", VENOM_DOC)

    await make_node(db_session, "kb-box", role="knowledge")
    route = respx_mock.post("http://kb-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "found it"})
    )

    response = await client.post(
        "/api/v1/tasks",
        json={"prompt": "search the docs for lycosa venom", "type": "retrieval"},
        headers=bearer(token),
    )
    assert response.status_code == 201
    dispatched = route.calls.last.request.read().decode()
    assert "Lycosa venom is mild" in dispatched


@respx.mock(assert_all_mocked=False)
async def test_task_without_knowledge_query_is_untouched(
    respx_mock, client: AsyncClient, db_session: AsyncSession, users: dict, qdrant
) -> None:
    token = await login(client, ADMIN_EMAIL)
    await make_node(db_session, "plain-box", role="hybrid")
    route = respx_mock.post("http://plain-box:8010/execute").mock(
        return_value=Response(200, json={"status": "succeeded", "output": "hi"})
    )

    await client.post("/api/v1/tasks", json={"prompt": "just say hello"}, headers=bearer(token))
    dispatched = route.calls.last.request.read().decode()
    assert "retrieved context" not in dispatched
