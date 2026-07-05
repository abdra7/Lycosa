"""Hardware profile collection: what this machine is, in the controller's schema."""

import platform
import shutil
import subprocess

import httpx
import psutil

_GB = 1024**3


def _cpu_model() -> str:
    model = platform.processor()
    if not model and platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
    return model or platform.machine() or "unknown"


def _detect_gpus() -> list[dict]:
    """NVIDIA via nvidia-smi; other vendors report none (extend later)."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        name, _, mem_mb = line.partition(",")
        try:
            gpus.append({"model": name.strip(), "vram_gb": round(float(mem_mb) / 1024, 1)})
        except ValueError:
            continue
    return gpus


def _detect_runtimes(ollama_url: str) -> list[dict]:
    runtimes = []
    try:
        response = httpx.get(f"{ollama_url}/api/tags", timeout=3)
        response.raise_for_status()
        models = [m["name"] for m in response.json().get("models", [])]
        version = None
        try:
            version = httpx.get(f"{ollama_url}/api/version", timeout=3).json().get("version")
        except (httpx.HTTPError, ValueError):
            pass
        runtimes.append({"name": "ollama", "version": version, "models": models})
    except (httpx.HTTPError, ValueError):
        if shutil.which("ollama"):  # installed but not running
            runtimes.append({"name": "ollama", "version": None, "models": []})
    return runtimes


def collect_profile(ollama_url: str = "http://localhost:11434") -> dict:
    """Snapshot this machine as a controller-schema hardware profile."""
    disk = shutil.disk_usage("/")
    return {
        "cpu_model": _cpu_model(),
        "cpu_cores": psutil.cpu_count(logical=True) or 1,
        "ram_gb": round(psutil.virtual_memory().total / _GB, 1),
        "gpus": _detect_gpus(),
        "storage_gb": round(disk.total / _GB, 1),
        "os": {
            "name": platform.system(),
            "version": platform.release(),
            "arch": platform.machine(),
        },
        "runtimes": _detect_runtimes(ollama_url),
    }
