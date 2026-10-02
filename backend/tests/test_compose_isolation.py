"""Security audit 2026-10 (ADR-030): Grafana is published on the LAN, so it
must neither reach the internal data services nor receive the operator's
root .env (controller admin password, JWT secret, DB credentials)."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "infra" / "docker-compose.yml"
INTERNAL = ("postgres", "qdrant", "redis", "api")


def _services() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


def _env_files(service: dict) -> list[str]:
    entries = service.get("env_file") or []
    entries = [entries] if isinstance(entries, str | dict) else entries
    return [e if isinstance(e, str) else e["path"] for e in entries]


def test_grafana_shares_no_network_with_internal_services() -> None:
    services = _services()
    grafana = set(services["grafana"]["networks"])
    for name in INTERNAL:
        shared = grafana & set(services[name]["networks"])
        assert not shared, f"grafana shares {shared} with {name}"


def test_prometheus_still_bridges_grafana_and_the_api() -> None:
    services = _services()
    prometheus = set(services["prometheus"]["networks"])
    assert prometheus & set(services["grafana"]["networks"])
    assert prometheus & set(services["api"]["networks"])


def test_grafana_does_not_receive_the_root_env() -> None:
    files = _env_files(_services()["grafana"])
    assert "../.env" not in files
    assert all(Path(f).name.startswith(".env.grafana") for f in files)
