"""Live health metrics in the controller's heartbeat schema."""

import shutil
import subprocess

import psutil

from lycosa_agent.gpu import _number, collect_gpus

_GB = 1024**3


def _gpu_metrics() -> list[dict]:
    collected = collect_gpus()
    if collected:
        return collected
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
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
        if len(parts) != 6:
            continue
        try:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "vendor": "nvidia",
                    "name": parts[1],
                    "available": True,
                    "utilization_percent": _number(parts[2]),
                    "memory_used_mb": float(parts[3]),
                    "memory_total_mb": float(parts[4]),
                    "memory_percent": float(parts[3]) / float(parts[4]) * 100,
                    "temperature_c": _number(parts[5]),
                }
            )
        except (ValueError, ZeroDivisionError):
            continue
    return gpus


def collect_metrics(running_tasks: int = 0) -> dict:
    memory = psutil.virtual_memory()
    disk = shutil.disk_usage("/")
    gpus = _gpu_metrics()
    # Keep the original fields for existing controllers and dashboard clients.
    for gpu in gpus:
        gpu["util_percent"] = gpu.get("utilization_percent")
        gpu["mem_used_gb"] = (
            gpu["memory_used_mb"] / 1024 if gpu.get("memory_used_mb") is not None else None
        )
        gpu["temp_c"] = gpu.get("temperature_c")
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": memory.percent,
        "ram_used_gb": round(memory.used / _GB, 2),
        "ram_available_mb": round(memory.available / 1024**2, 2),
        "disk_percent": round(disk.used / disk.total * 100, 1),
        "gpus": gpus,
        "gpu_unavailable_reason": None if gpus else "No supported GPU telemetry detected",
        "running_tasks": running_tasks,
    }
