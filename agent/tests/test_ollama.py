import respx
from httpx import Response

from lycosa_agent.runtimes.ollama import OllamaAdapter

OLLAMA = "http://ollama:11434"


@respx.mock
async def test_list_models() -> None:
    respx.get(f"{OLLAMA}/api/tags").mock(
        return_value=Response(200, json={"models": [{"name": "llama3:8b"}, {"name": "phi3:mini"}]})
    )
    adapter = OllamaAdapter(OLLAMA)
    assert await adapter.list_models() == ["llama3:8b", "phi3:mini"]
    await adapter.aclose()


@respx.mock
async def test_generate_returns_response_text() -> None:
    route = respx.post(f"{OLLAMA}/api/generate").mock(
        return_value=Response(200, json={"response": "hello from the model"})
    )
    adapter = OllamaAdapter(OLLAMA)
    output = await adapter.generate("llama3:8b", "say hello", {"temperature": 0})
    await adapter.aclose()

    assert output == "hello from the model"
    body = route.calls.last.request.read().decode()
    assert '"stream": false' in body or '"stream":false' in body.replace(" ", "")


@respx.mock
async def test_pull_model_posts_name() -> None:
    route = respx.post(f"{OLLAMA}/api/pull").mock(return_value=Response(200, json={}))
    adapter = OllamaAdapter(OLLAMA)
    await adapter.pull_model("mistral:7b")
    await adapter.aclose()
    assert route.called
