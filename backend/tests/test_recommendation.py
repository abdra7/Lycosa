"""Engine unit tests: the SDD example table plus one profile per role."""

from app.schemas.node import HardwareProfile
from app.services.recommendation import get_recommender


def profile(**overrides) -> HardwareProfile:
    base = {
        "cpu_model": "Test CPU",
        "cpu_cores": 8,
        "ram_gb": 16,
        "gpus": [],
        "storage_gb": 500,
        "os": {"name": "Linux"},
        "runtimes": [],
    }
    base.update(overrides)
    return HardwareProfile.model_validate(base)


def recommend(**overrides):
    return get_recommender().recommend(profile(**overrides))


# --- The SDD example table ---


def test_sdd_ryzen9_rtx5090_64gb_is_hybrid() -> None:
    rec = recommend(
        cpu_model="AMD Ryzen 9 7950X",
        cpu_cores=32,
        ram_gb=64,
        gpus=[{"model": "NVIDIA RTX 5090", "vram_gb": 32}],
        storage_gb=2000,
        runtimes=[{"name": "ollama"}],
    )
    assert rec.role == "hybrid", rec.scores


def test_sdd_i5_8gb_no_gpu_is_knowledge_or_tool() -> None:
    rec = recommend(cpu_model="Intel Core i5", cpu_cores=4, ram_gb=8, gpus=[], storage_gb=256)
    assert rec.role in ("knowledge", "tool"), rec.scores


def test_sdd_rtx4090_128gb_is_ai_compute() -> None:
    rec = recommend(
        cpu_model="AMD Threadripper",
        cpu_cores=24,
        ram_gb=128,
        gpus=[{"model": "NVIDIA RTX 4090", "vram_gb": 24}],
        storage_gb=2000,
        runtimes=[{"name": "ollama"}],
    )
    assert rec.role == "ai_compute", rec.scores


# --- Every role is reachable ---


def test_ai_compute_reachable() -> None:
    rec = recommend(cpu_cores=24, ram_gb=128, gpus=[{"model": "A6000", "vram_gb": 48}])
    assert rec.role == "ai_compute"


def test_hybrid_reachable() -> None:
    rec = recommend(cpu_cores=16, ram_gb=64, gpus=[{"model": "RTX 4070", "vram_gb": 12}])
    assert rec.role == "hybrid"


def test_knowledge_reachable() -> None:
    rec = recommend(cpu_cores=4, ram_gb=24, gpus=[], storage_gb=2000)
    assert rec.role == "knowledge"


def test_tool_reachable() -> None:
    rec = recommend(cpu_cores=4, ram_gb=8, gpus=[], storage_gb=256)
    assert rec.role == "tool"


def test_vision_reachable() -> None:
    rec = recommend(
        cpu_cores=8,
        ram_gb=32,
        gpus=[{"model": "RTX 3060", "vram_gb": 12}],
        runtimes=[{"name": "ollama", "models": ["llava:13b"]}],
    )
    assert rec.role == "vision"


def test_vision_reachable_via_camera_extra() -> None:
    rec = recommend(
        cpu_cores=8, ram_gb=16, gpus=[{"model": "Jetson", "vram_gb": 8}], extra={"camera": True}
    )
    assert rec.role == "vision"


def test_storage_reachable() -> None:
    rec = recommend(cpu_cores=4, ram_gb=16, gpus=[], storage_gb=8000)
    assert rec.role == "storage"


# --- Output shape ---


def test_recommendation_is_transparent() -> None:
    rec = recommend(cpu_cores=24, ram_gb=128, gpus=[{"model": "RTX 4090", "vram_gb": 24}])
    assert 0 <= rec.confidence <= 1
    assert rec.rationale, "winning role must come with human-readable reasons"
    assert set(rec.scores) == {"ai_compute", "hybrid", "vision", "knowledge", "storage", "tool"}
    assert rec.scores[rec.role] == rec.confidence
