"""Zero-config model setup: a fresh agent pulls the best-fit model for its
hardware automatically — no operator action, no inbound firewall ports."""

from typing import Any

from lycosa_agent.config import AgentSettings
from lycosa_agent.main import _auto_configure_model

NODE = {"id": "n1"}

RECOMMENDATIONS = [
    {
        "model": "llama3.1:8b",
        "use_case": "general",
        "recommended": True,
        "runnable": True,
        "reason": "fits in GPU VRAM",
    },
    {
        "model": "qwen2.5-coder:7b",
        "use_case": "coding",
        "recommended": True,
        "runnable": True,
        "reason": "best coding pick",
    },
    {
        "model": "llama3.2:1b",
        "use_case": "general",
        "recommended": False,
        "runnable": True,
        "reason": "tiny",
    },
    {
        "model": "llama3.1:70b",
        "use_case": "general",
        "recommended": False,
        "runnable": False,
        "reason": "too big",
    },
]


class FakeControllerClient:
    def __init__(self, recommendations: list[dict[str, Any]] | None = None) -> None:
        self.recommendations = RECOMMENDATIONS if recommendations is None else recommendations
        self.register_calls: list[dict[str, Any]] = []

    async def llm_recommendations(self, node_id: str) -> list[dict[str, Any]]:
        return self.recommendations

    async def register(self, name, profile, agent_url=None, agent_token=None) -> dict[str, Any]:
        self.register_calls.append({"name": name, "profile": profile})
        return NODE


class FakeOllama:
    def __init__(self, models: list[str] | None = None, reachable: bool = True) -> None:
        self.models = models or []
        self.reachable = reachable
        self.pulled: list[str] = []

    async def list_models(self) -> list[str]:
        if not self.reachable:
            raise ConnectionError("connection refused")
        return self.models

    async def pull_model(self, model: str) -> None:
        self.pulled.append(model)
        self.models.append(model)


def settings(**overrides) -> AgentSettings:
    return AgentSettings(api_key="k", node_name="box", **overrides)


async def test_fresh_node_pulls_best_general_model_and_reregisters() -> None:
    client = FakeControllerClient()
    ollama = FakeOllama()

    pulled = await _auto_configure_model(
        client, ollama, NODE, settings(), profile_factory=lambda: {"fake": True}
    )

    assert pulled == "llama3.1:8b"  # the recommended *general* pick, not coding
    assert ollama.pulled == ["llama3.1:8b"]
    assert len(client.register_calls) == 1  # inventory refreshed on the controller


async def test_existing_models_are_left_alone() -> None:
    client = FakeControllerClient()
    ollama = FakeOllama(models=["mistral:7b"])

    pulled = await _auto_configure_model(client, ollama, NODE, settings())

    assert pulled is None
    assert ollama.pulled == []
    assert client.register_calls == []


async def test_opt_out_via_setting() -> None:
    client = FakeControllerClient()
    ollama = FakeOllama()

    pulled = await _auto_configure_model(client, ollama, NODE, settings(auto_pull_model=False))

    assert pulled is None
    assert ollama.pulled == []


async def test_ollama_unreachable_warns_but_does_not_crash() -> None:
    client = FakeControllerClient()
    ollama = FakeOllama(reachable=False)

    pulled = await _auto_configure_model(client, ollama, NODE, settings())

    assert pulled is None
    assert ollama.pulled == []


async def test_falls_back_to_first_runnable_when_no_general_pick() -> None:
    recommendations = [
        {
            "model": "llava:7b",
            "use_case": "vision",
            "recommended": True,
            "runnable": False,
            "reason": "too big",
        },
        {
            "model": "llama3.2:1b",
            "use_case": "general",
            "recommended": False,
            "runnable": True,
            "reason": "tiny",
        },
    ]
    client = FakeControllerClient(recommendations)
    ollama = FakeOllama()

    pulled = await _auto_configure_model(
        client, ollama, NODE, settings(), profile_factory=lambda: {"fake": True}
    )

    assert pulled == "llama3.2:1b"


async def test_nothing_runnable_skips_quietly() -> None:
    client = FakeControllerClient(
        [
            {
                "model": "llama3.1:70b",
                "use_case": "general",
                "recommended": False,
                "runnable": False,
                "reason": "too big",
            }
        ]
    )
    ollama = FakeOllama()

    pulled = await _auto_configure_model(client, ollama, NODE, settings())

    assert pulled is None
    assert ollama.pulled == []
