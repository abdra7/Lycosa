"""Security audit 2026-10 (ADR-030): the controller installers generate their
env files themselves. .env.example is deliberately untracked, so depending on
it made every fresh-clone install abort and left operators on the
zero-config defaults; Grafana's login goes to its own file."""

from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_needs_no_env_template(name: str) -> None:
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    assert ".env.example" not in text
    assert "ENVIRONMENT=production" in text


@pytest.mark.parametrize("name", ["install.sh", "install.ps1"])
def test_installer_keeps_grafana_settings_out_of_root_env(name: str) -> None:
    text = (SCRIPTS / name).read_text(encoding="utf-8")
    assert ".env.grafana" in text
    root_env = text.split(".env.grafana")[0]
    assert "GF_SECURITY_ADMIN_PASSWORD=$(" not in root_env
