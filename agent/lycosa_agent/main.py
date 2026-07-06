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
    await _register_with_retry(client, settings, agent_url, agent_token)

    exec_app = create_app(OllamaAdapter(settings.ollama_url), token=agent_token)
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
