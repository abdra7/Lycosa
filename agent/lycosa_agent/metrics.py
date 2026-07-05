"""Live health metrics in the controller's heartbeat schema."""

import shutil
import subprocess

import psutil

_GB = 1024**3


def _gpu_metrics() -> list[dict]:
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            gpus.append(
                {
                    "util_percent": float(parts[0]),
                    "mem_used_gb": round(float(parts[1]) / 1024, 2),
                    "temp_c": float(parts[2]),
                }
            )
        except ValueError:
            continue
    return gpus


def collect_metrics(running_tasks: int = 0) -> dict:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / _GB, 2),
        "disk_percent": round(disk.used / disk.total * 100, 1),
        "gpus": _gpu_metrics(),
        "running_tasks": running_tasks,
    }
