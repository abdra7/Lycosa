"""Per-node LLM recommendations: hardware profile in, ranked model list out.

Rule-based and transparent like the role recommender (ADR-010): the catalog in
config/llm_catalog.yml declares what each model needs; this module decides
what a given node can run (GPU first, CPU fallback), why, and which runnable
model per use case is the best pick.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

_CATALOG_PATH = Path(__file__).parents[2] / "config" / "llm_catalog.yml"


class ModelRecommendation(BaseModel):
    model: str
    params_b: float
    use_case: str  # general | coding | vision
    runnable: bool
    runs_on: str | None  # "gpu" | "cpu" | None when not runnable
    recommended: bool  # best runnable model of its use case
    installed: bool
    reason: str


@lru_cache
def _load_catalog() -> list[dict[str, Any]]:
    with open(_CATALOG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["models"]


def recommend_models(
    *,
    ram_gb: float | None,
    gpu_vram_gb: float | None,
    installed_models: set[str] | None = None,
) -> list[ModelRecommendation]:
    """Rank the catalog for a node. `gpu_vram_gb` is the largest single card's
    VRAM (None/0 = CPU-only); `installed_models` are Ollama tags already on
    the node so the UI can show install state."""
    ram = ram_gb or 0.0
    vram = gpu_vram_gb or 0.0
    installed = installed_models or set()

    results: list[ModelRecommendation] = []
    for entry in _load_catalog():
        min_vram = float(entry["min_vram_gb"])
        min_ram = float(entry["min_ram_gb"])
        if vram >= min_vram:
            runs_on = "gpu"
            reason = f"fits in GPU VRAM ({vram:g} GB ≥ {min_vram:g} GB needed)"
        elif ram >= min_ram:
            runs_on = "cpu"
            reason = (
                f"runs CPU-only in RAM ({ram:g} GB ≥ {min_ram:g} GB needed) — "
                "slower than GPU inference"
            )
        else:
            runs_on = None
            reason = (
                f"needs {min_vram:g} GB GPU VRAM or {min_ram:g} GB RAM — "
                f"this node has {vram:g} GB VRAM / {ram:g} GB RAM"
            )
        results.append(
            ModelRecommendation(
                model=entry["name"],
                params_b=float(entry["params_b"]),
                use_case=entry["use_case"],
                runnable=runs_on is not None,
                runs_on=runs_on,
                recommended=False,  # decided below
                installed=entry["name"] in installed,
                reason=f"{reason}. {entry['note']}",
            )
        )

    # best runnable model per use case = the largest one; GPU beats CPU on ties
    best_by_use_case: dict[str, ModelRecommendation] = {}
    for rec in results:
        if not rec.runnable:
            continue
        current = best_by_use_case.get(rec.use_case)
        if (
            current is None
            or rec.params_b > current.params_b
            or (
                rec.params_b == current.params_b
                and rec.runs_on == "gpu"
                and current.runs_on == "cpu"
            )
        ):
            best_by_use_case[rec.use_case] = rec
    for rec in best_by_use_case.values():
        rec.recommended = True

    # recommended picks first, then runnable big→small, then the rest big→small
    results.sort(key=lambda r: (not r.recommended, not r.runnable, -r.params_b))
    return results
