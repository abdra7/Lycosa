#!/usr/bin/env python3
"""Stage the standalone runtime that the Lycosa desktop app bundles (ADR-037).

Downloads the pinned Python, PostgreSQL and Qdrant builds for this machine,
checks every archive against the sha256 pinned in runtime/manifest.json,
unpacks them, installs the backend's and the agent's Python dependencies into
the bundled Python (exact versions from runtime/constraints.txt, wheels only),
and copies the backend and agent sources. The result,
desktop-tauri/src-tauri/resources/runtime/ (git-ignored), is what
tauri.conf.json ships as the app's `runtime/` resource directory.

Staging is native only: the bundled Python must run here to install wheels
for its own platform, so each OS stages its own runtime (CI does it on each
release runner).

    python desktop-tauri/scripts/stage_runtime.py [--out DIR] [--cache DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESKTOP = HERE.parent
REPO = DESKTOP.parent
MANIFEST = DESKTOP / "runtime" / "manifest.json"
CONSTRAINTS = DESKTOP / "runtime" / "constraints.txt"
DEFAULT_OUT = DESKTOP / "src-tauri" / "resources" / "runtime"
DEFAULT_CACHE = DESKTOP / ".runtime-cache"

# backend files the runtime needs; tests, local data and caches stay out
BACKEND_TREES = ("app", "alembic", "config")
BACKEND_FILES = ("alembic.ini", "scripts/seed_admin.py")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")

# stdlib parts nothing in Lycosa imports; dropping them saves ~30 MB
PYTHON_PRUNE = ("test", "idlelib", "tkinter", "turtledemo", "ensurepip", "lib2to3")
# build-time-only parts of the Python install (headers, import and static
# libraries, launcher scripts)
PYTHON_ROOT_PRUNE = ("include", "libs", "Scripts", "share", "lib/pkgconfig")
# PostgreSQL parts the supervisor never runs: headers, docs, translations,
# the StackBuilder GUI, the regression and test tools
POSTGRES_PRUNE = ("include", "doc", "symbols", "pgAdmin 4", "StackBuilder", "share/locale")
POSTGRES_BIN_PRUNE = (
    "stackbuilder*",
    "wx*.dll",
    "*regress*",
    "isolationtester*",
    "libpq_pipeline*",
    "libpq_testclient*",
    "test_cloexec*",
    "testplug*",
    "ecpg*",
    "pgbench*",
)


def log(message: str) -> None:
    print(f"[stage-runtime] {message}", flush=True)


def host_target() -> str:
    system, machine = platform.system(), platform.machine().lower()
    if system == "Windows" and machine in ("amd64", "x86_64"):
        return "x86_64-pc-windows-msvc"
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "aarch64-apple-darwin"
    if system == "Darwin" and machine == "x86_64":
        return "x86_64-apple-darwin"
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "x86_64-unknown-linux-gnu"
    raise SystemExit(f"unsupported host for the standalone runtime: {system} {machine}")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, sha256: str, cache: Path) -> Path:
    """Fetch `url` into the cache unless a verified copy is already there."""
    cache.mkdir(parents=True, exist_ok=True)
    name = urllib.request.url2pathname(url.rsplit("/", 1)[-1]).replace("+", "_")
    target = cache / name
    if target.is_file() and sha256_of(target) == sha256:
        log(f"cached {name}")
        return target
    log(f"downloading {url}")
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "lycosa-stage-runtime"})
    with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    actual = sha256_of(partial)
    if actual != sha256:
        partial.unlink()
        raise SystemExit(f"sha256 mismatch for {name}: expected {sha256}, got {actual}")
    partial.replace(target)
    return target


def extract(archive: Path, dest: Path) -> None:
    """Unpack an archive, refusing members that would land outside `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    if archive.name.endswith(".zip"):
        root = dest.resolve()
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                resolved = (dest / member).resolve()
                if root not in resolved.parents and resolved != root:
                    raise SystemExit(f"{archive.name}: unsafe member path {member!r}")
            zf.extractall(dest)
        return
    with tarfile.open(archive) as tf:
        # the "data" filter rejects absolute paths, "..", links that leave
        # the destination and device files (PEP 706)
        tf.extractall(dest, filter="data")


def single_child(directory: Path) -> Path:
    children = [p for p in directory.iterdir() if not p.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return directory


def python_exe(python_root: Path) -> Path:
    if os.name == "nt":
        return python_root / "python.exe"
    return python_root / "bin" / "python3"


def stdlib_dir(python_root: Path) -> Path:
    if os.name == "nt":
        return python_root / "Lib"
    return next((python_root / "lib").glob("python3.*"))


def project_dependencies(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return list(data["project"]["dependencies"])


def stage_python(spec: dict, cache: Path, work: Path, out: Path) -> Path:
    archive = download(spec["url"], spec["sha256"], cache)
    extract(archive, work / "python")
    root = work / "python" / "python"
    if not root.is_dir():
        root = single_child(work / "python")
    shutil.move(str(root), out / "python")
    python_root = out / "python"
    stdlib = stdlib_dir(python_root)
    for name in PYTHON_PRUNE:
        shutil.rmtree(stdlib / name, ignore_errors=True)
    for config in stdlib.glob("config-3*"):  # static libpython + Makefile
        shutil.rmtree(config, ignore_errors=True)
    for tcl in ("tcl", "lib/tcl8.6", "lib/tk8.6", "lib/tcl8"):
        shutil.rmtree(python_root / tcl, ignore_errors=True)
    for itcl in (python_root / "lib").glob("itcl*"):
        shutil.rmtree(itcl, ignore_errors=True)
    return python_root


def prune_python(python_root: Path) -> None:
    """After the dependency install: drop pip and the build-only parts."""
    for name in PYTHON_ROOT_PRUNE:
        shutil.rmtree(python_root / name, ignore_errors=True)
    site = stdlib_dir(python_root) / "site-packages"
    for pip in site.glob("pip*"):
        shutil.rmtree(pip, ignore_errors=True)
    if os.name != "nt":  # keep python3 / python3.12; drop pip, idle, pydoc launchers
        for script in (python_root / "bin").iterdir():
            if not script.name.startswith("python"):
                script.unlink()


def install_dependencies(python_root: Path) -> None:
    requirements = project_dependencies(REPO / "backend" / "pyproject.toml")
    requirements += project_dependencies(REPO / "agent" / "pyproject.toml")
    command = [
        str(python_exe(python_root)),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--no-compile",
        "--only-binary=:all:",
        "--constraint",
        str(CONSTRAINTS),
        *sorted(set(requirements)),
    ]
    log("installing backend + agent dependencies (wheels only, pinned)")
    subprocess.run(command, check=True, env=clean_env())


def clean_env() -> dict[str, str]:
    """The host's Python settings must not leak into the bundled one."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "PIP_"))}
    env["PYTHONNOUSERSITE"] = "1"
    env["PIP_NO_INPUT"] = "1"
    return env


def stage_postgres(spec: dict, cache: Path, work: Path, out: Path) -> Path:
    archive = download(spec["url"], spec["sha256"], cache)
    extract(archive, work / "postgres")
    root = single_child(work / "postgres")
    if (root / "pgsql").is_dir():  # EDB-style layout
        root = root / "pgsql"
    shutil.move(str(root), out / "postgres")
    postgres = out / "postgres"
    for name in POSTGRES_PRUNE:
        shutil.rmtree(postgres / name, ignore_errors=True)
    for doc in ("share/doc", "share/man"):
        shutil.rmtree(postgres / doc, ignore_errors=True)
    for pattern in POSTGRES_BIN_PRUNE:
        for path in (postgres / "bin").glob(pattern):
            path.unlink()
    # Windows debug symbols and import libraries
    for path in [*postgres.rglob("*.pdb"), *postgres.rglob("*.lib")]:
        path.unlink()
    for binary in ("postgres", "initdb", "pg_ctl", "pg_isready", "psql", "pg_dump"):
        exe = postgres / "bin" / (binary + (".exe" if os.name == "nt" else ""))
        if not exe.is_file():
            raise SystemExit(f"PostgreSQL archive has no {exe.relative_to(out)}")
    return postgres


def stage_qdrant(spec: dict, cache: Path, work: Path, out: Path) -> Path:
    archive = download(spec["url"], spec["sha256"], cache)
    extract(archive, work / "qdrant")
    name = "qdrant.exe" if os.name == "nt" else "qdrant"
    found = next((work / "qdrant").rglob(name), None)
    if found is None:
        raise SystemExit(f"Qdrant archive has no {name}")
    (out / "qdrant").mkdir()
    target = out / "qdrant" / name
    shutil.move(str(found), target)
    if os.name != "nt":
        target.chmod(0o755)
    return target


def stage_sources(out: Path) -> None:
    backend = out / "backend"
    for tree in BACKEND_TREES:
        shutil.copytree(REPO / "backend" / tree, backend / tree, ignore=IGNORED)
    for file in BACKEND_FILES:
        (backend / file).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "backend" / file, backend / file)
    shutil.copytree(
        REPO / "agent" / "lycosa_agent", out / "agent" / "lycosa_agent", ignore=IGNORED
    )


def compile_bytecode(python_root: Path, out: Path) -> None:
    """Precompile with unchecked hashes: installers rewrite file mtimes and
    the install dir is often read-only, so mtime-checked .pyc would be
    recompiled in memory on every start."""
    exe = str(python_exe(python_root))
    # the stdlib (and the site-packages inside it) may hold a few files
    # that never compile (templates, py2 fixtures): best effort there
    for directory, strict in (
        (stdlib_dir(python_root), False),
        (out / "backend", True),
        (out / "agent", True),
    ):
        result = subprocess.run(
            [exe, "-m", "compileall", "-q", "-j", "0", "--invalidation-mode",
             "unchecked-hash", str(directory)],
            env=clean_env(),
            check=False,
        )
        if result.returncode != 0 and strict:
            raise SystemExit(f"compileall failed for {directory}")


def backend_version() -> str:
    data = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def write_backend_metadata(python_root: Path) -> None:
    """The backend runs from its source tree (its config files are found
    relative to it), so no wheel is installed; a bare dist-info lets
    `importlib.metadata.version("lycosa-backend")` (app/version.py, shown by
    /healthz) report the real version instead of 0.0.0+dev."""
    version = backend_version()
    dist = stdlib_dir(python_root) / "site-packages" / f"lycosa_backend-{version}.dist-info"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: lycosa-backend\nVersion: {version}\n", encoding="utf-8"
    )
    (dist / "INSTALLER").write_text("lycosa-stage-runtime\n", encoding="utf-8")
    (dist / "RECORD").write_text("", encoding="utf-8")


def smoke_check(python_root: Path, out: Path) -> None:
    """Import what the supervisor will run, from the staged tree only."""
    env = clean_env()
    env["PYTHONPATH"] = os.pathsep.join([str(out / "backend"), str(out / "agent")])
    code = (
        "import asyncpg, alembic, uvicorn, fastapi, qdrant_client, cryptography, "
        "pwdlib, psutil, zeroconf; import lycosa_agent.main; "
        "from app.version import APP_VERSION; assert APP_VERSION != '0.0.0+dev', APP_VERSION; "
        "print('imports ok, backend', APP_VERSION)"
    )
    subprocess.run([str(python_exe(python_root)), "-c", code], check=True, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args()

    if sys.version_info < (3, 11, 4):
        raise SystemExit("stage_runtime.py needs Python 3.11.4+ (tarfile extraction filters)")
    target = host_target()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    previous = [p for p in out.iterdir() if p.name != ".gitkeep"]
    if previous:
        log(f"removing the previous runtime in {out}")
    for path in previous:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    with tempfile.TemporaryDirectory(prefix="lycosa-runtime-") as tmp:
        work = Path(tmp)
        python_root = stage_python(manifest["python"]["targets"][target], args.cache, work, out)
        stage_postgres(manifest["postgres"]["targets"][target], args.cache, work, out)
        stage_qdrant(manifest["qdrant"]["targets"][target], args.cache, work, out)
    stage_sources(out)
    install_dependencies(python_root)
    prune_python(python_root)
    write_backend_metadata(python_root)
    compile_bytecode(python_root, out)
    smoke_check(python_root, out)

    info = {
        "target": target,
        "python": manifest["python"]["version"],
        "postgres": manifest["postgres"]["version"],
        "qdrant": manifest["qdrant"]["version"],
        "backend": backend_version(),
        "staged_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (out / "runtime.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    log(f"staged {target} runtime at {out} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
