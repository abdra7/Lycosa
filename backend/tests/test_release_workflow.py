"""Security audit 2026-10 (ADR-030): the release workflow publishes installers
and the GHCR image, so its build inputs must be pinned and verified and its
write token scoped to the jobs that publish."""

import re
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SHA_PINNED = re.compile(r"@[0-9a-f]{40}$")


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _steps(workflow: dict):
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            yield job_name, step


def test_write_permissions_are_scoped_per_job() -> None:
    release = _workflow("release.yml")
    assert release["permissions"] == {"contents": "read"}
    for name, job in release["jobs"].items():
        perms = job.get("permissions", {})
        writes = {scope for scope, level in perms.items() if level == "write"}
        expected = {"release": {"contents"}, "backend-image": {"packages"}}.get(name, set())
        assert writes == expected, (name, perms)


def test_actions_are_pinned_to_commit_shas() -> None:
    for job, step in _steps(_workflow("release.yml")):
        if "uses" in step:
            assert SHA_PINNED.search(step["uses"]), (job, step["uses"])


def test_checkouts_do_not_persist_the_token() -> None:
    for job, step in _steps(_workflow("release.yml")):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step.get("with", {}).get("persist-credentials") is False, job


def test_downloaded_build_tools_are_versioned_and_verified() -> None:
    for job, step in _steps(_workflow("release.yml")):
        script = step.get("run", "")
        assert "/releases/download/continuous/" not in script, job
        if "curl" in script or "wget" in script:
            assert "sha256sum -c" in script, (job, step.get("name"))
            verified = script.index("sha256sum -c")
            assert verified < script.index("chmod +x appimagetool"), job
            assert verified < script.index("./appimagetool"), job


def test_run_scripts_take_no_expressions() -> None:
    """Values reach shell steps through env, never by expression expansion."""
    for job, step in _steps(_workflow("release.yml")):
        assert "${{" not in step.get("run", ""), (job, step.get("name"))


def test_ci_token_is_read_only() -> None:
    assert _workflow("ci.yml")["permissions"] == {"contents": "read"}
