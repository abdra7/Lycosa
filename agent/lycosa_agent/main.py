"""Agent entrypoint: register with the controller, heartbeat forever, and
serve the local execution API."""

import argparse
import asyncio
import logging
import secrets
import socket

import httpx
import uvicorn

from lycosa_agent.client import ControllerClient
from lycosa_agent.config import AgentSettings
from lycosa_agent.discovery import DiscoveryAdvertiser
from lycosa_agent.executor import create_app
from lycosa_agent.hwprofile import collect_profile
from lycosa_agent.metrics import collect_metrics
from lycosa_agent.runtimes.ollama import OllamaAdapter

logger = logging.getLogger("lycosa.agent")

_REGISTER_BACKOFF_SECONDS = [2, 5, 10, 30, 60]


def _local_ip() -> str:
    """Best-effort LAN IP (no traffic is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


async def _register_with_retry(
    client: ControllerClient, settings: AgentSettings, agent_url: str, agent_token: str
) -> dict:
    profile = collect_profile(settings.ollama_url)
    for attempt, backoff in enumerate([0, *_REGISTER_BACKOFF_SECONDS]):
        if backoff:
            await asyncio.sleep(backoff)
        try:
            node = await client.register(
                settings.node_name, profile, agent_url=agent_url, agent_token=agent_token
            )
            logger.info(
                "registered as node %s (recommended role: %s)",
                node["id"],
                node.get("recommended_role"),
            )
            return node
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (401, 403, 422):
                raise  # config problem; retrying won't help
            logger.warning("registration attempt %d failed: %s", attempt + 1, exc)
        except httpx.HTTPError as exc:
            logger.warning("controller unreachable (attempt %d): %s", attempt + 1, exc)
    raise RuntimeError("could not register with controller; giving up")


async def _auto_configure_model(
    client: ControllerClient,
    adapter,
    node: dict,
    settings: AgentSettings,
    *,
    agent_url: str | None = None,
    agent_token: str | None = None,
    profile_factory=None,
) -> str | None:
    """Zero-config model setup: a fresh node (empty Ollama) asks the controller
    which model best fits its hardware, pulls it, and re-registers so the
    inventory updates — no operator action, no inbound ports. Returns the
    pulled model tag, or None when nothing was done."""
    if not settings.auto_pull_model:
        return None
    try:
        installed = await adapter.list_models()
    except Exception as exc:  # noqa: BLE001 — Ollama missing is a normal state
        logger.warning(
            "cannot reach Ollama at %s (%s) — install it from https://ollama.com "
            "and restart the agent to enable local models",
            settings.ollama_url,
            exc,
        )
        return None
    if installed:
        logger.info("models already installed (%s); skipping auto-setup", ", ".join(installed))
        return None
    try:
        recommendations = await client.llm_recommendations(node["id"])
    except httpx.HTTPError as exc:
        logger.warning("could not fetch model recommendations: %s", exc)
        return None
    best = next(
        (r for r in recommendations if r.get("recommended") and r.get("use_case") == "general"),
        None,
    ) or next((r for r in recommendations if r.get("runnable")), None)
    if best is None:
        logger.info("no catalog model fits this hardware; skipping auto-setup")
        return None
    logger.info(
        "auto-setup: pulling %s (%s) — the download may take several minutes",
        best["model"],
        best["reason"],
    )
    try:
        await adapter.pull_model(best["model"])
    except Exception as exc:  # noqa: BLE001 — report, keep the agent alive
        logger.warning("model pull failed: %s", exc)
        return None
    logger.info("auto-setup complete: %s is installed and ready", best["model"])
    try:  # re-register so the controller's inventory/scheduler see the model now
        profile = (profile_factory or (lambda: collect_profile(settings.ollama_url)))()
        await client.register(
            settings.node_name, profile, agent_url=agent_url, agent_token=agent_token
        )
    except httpx.HTTPError as exc:
        logger.warning("re-register after model install failed: %s", exc)
    return best["model"]


async def _heartbeat_loop(client: ControllerClient, interval: int) -> None:
    while True:
        try:
            response = await client.heartbeat(collect_metrics())
            interval = int(response.get("heartbeat_interval_seconds", interval))
        except httpx.HTTPError as exc:
            logger.warning("heartbeat failed: %s", exc)
        await asyncio.sleep(interval)


async def run(settings: AgentSettings) -> None:
    if not settings.api_key:
        raise SystemExit("LYCOSA_API_KEY is required (mint one via the controller admin API)")

    agent_token = secrets.token_urlsafe(32)
    agent_url = settings.advertise_url or f"http://{_local_ip()}:{settings.exec_port}"

    client = ControllerClient(settings.controller_url, settings.api_key)
    node = await _register_with_retry(client, settings, agent_url, agent_token)

    adapter = OllamaAdapter(settings.ollama_url)
    exec_app = create_app(adapter, token=agent_token)
    server = uvicorn.Server(
        uvicorn.Config(
            exec_app, host=settings.exec_host, port=settings.exec_port, log_level="warning"
        )
    )

    advertiser: DiscoveryAdvertiser | None = None
    if settings.discovery_enabled:
        advertiser = DiscoveryAdvertiser(settings.node_name, _local_ip(), settings.exec_port)
        await advertiser.start()

    logger.info(
        "exec API on %s, heartbeating every %ss", agent_url, settings.heartbeat_interval_seconds
    )
    try:
        await asyncio.gather(
            server.serve(),
            _heartbeat_loop(client, settings.heartbeat_interval_seconds),
            _auto_configure_model(
                client,
                adapter,
                node,
                settings,
                agent_url=agent_url,
                agent_token=agent_token,
            ),
        )
    finally:
        if advertiser is not None:
            await advertiser.stop()
        await client.aclose()


def cli() -> None:
    parser = argparse.ArgumentParser(prog="lycosa-agent", description="Lycosa Local Agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="register with the controller and start serving")
    run_parser.add_argument("--controller-url", help="overrides LYCOSA_CONTROLLER_URL")
    run_parser.add_argument("--api-key", help="overrides LYCOSA_API_KEY")
    run_parser.add_argument("--name", help="overrides LYCOSA_NODE_NAME")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    overrides = {
        key: value
        for key, value in {
            "controller_url": args.controller_url,
            "api_key": args.api_key,
            "node_name": args.name,
        }.items()
        if value is not None
    }
    asyncio.run(run(AgentSettings(**overrides)))


if __name__ == "__main__":
    cli()
