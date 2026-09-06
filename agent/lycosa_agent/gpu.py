"""Optional GPU collectors. Unknown measurements stay unknown, never zero."""

import math


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def collect_gpus() -> list[dict]:
    """Use NVML on supported Windows/Linux hosts; no driver is a normal case."""
    try:
        import pynvml as nvml
    except ImportError:
        return []
    initialized = False
    try:
        nvml.nvmlInit()
        initialized = True
        devices = []
        for index in range(nvml.nvmlDeviceGetCount()):
            try:
                handle = nvml.nvmlDeviceGetHandleByIndex(index)
                name = nvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                gpu = {"index": index, "vendor": "nvidia", "name": name, "available": True}
                try:
                    memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                    gpu.update(
                        memory_used_mb=memory.used / 1024**2,
                        memory_total_mb=memory.total / 1024**2,
                        memory_percent=memory.used / memory.total * 100 if memory.total else None,
                    )
                except Exception:
                    pass
                try:
                    gpu["utilization_percent"] = _number(
                        nvml.nvmlDeviceGetUtilizationRates(handle).gpu
                    )
                except Exception:
                    pass
                try:
                    gpu["temperature_c"] = _number(
                        nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
                    )
                except Exception:
                    pass
                devices.append(gpu)
            except Exception:
                continue
        return devices
    except Exception:
        return []
    finally:
        if initialized:
            try:
                nvml.nvmlShutdown()
            except Exception:
                pass
