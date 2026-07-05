import httpx
import pytest
import respx
from httpx import Response

from lycosa_agent.client import API_KEY_HEADER, ControllerClient

CONTROLLER = "http://controller:8000"


@respx.mock
async def test_register_sends_profile_and_key() -> None:
    route = respx.post(f"{CONTROLLER}/api/v1/nodes/register").mock(
        return_value=Response(201, json={"id": "abc", "recommended_role": "tool"})
    )
    client = ControllerClient(CONTROLLER, api_key="lyc_test_key")
    node = await client.register(
        "box", {"cpu_model": "x"}, agent_url="http://1.2.3.4:8010", agent_token="t" * 32
    )
    await client.aclose()

    assert node["id"] == "abc"
    request = route.calls.last.request
    assert request.headers[API_KEY_HEADER] == "lyc_test_key"
    body = request.read().decode()
    assert '"agent_url":"http://1.2.3.4:8010"' in body.replace(" ", "")


@respx.mock
async def test_heartbeat_posts_metrics() -> None:
    route = respx.post(f"{CONTROLLER}/api/v1/nodes/heartbeat").mock(
        return_value=Response(200, json={"status": "ok", "heartbeat_interval_seconds": 15})
    )
    client = ControllerClient(CONTROLLER, api_key="k")
    response = await client.heartbeat({"cpu_percent": 10, "ram_percent": 40})
    await client.aclose()

    assert response["heartbeat_interval_seconds"] == 15
    assert route.called


@respx.mock
async def test_heartbeat_raises_on_error_status() -> None:
    respx.post(f"{CONTROLLER}/api/v1/nodes/heartbeat").mock(return_value=Response(409))
    client = ControllerClient(CONTROLLER, api_key="k")
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await client.heartbeat({"cpu_percent": 1, "ram_percent": 1})
    finally:
        await client.aclose()
