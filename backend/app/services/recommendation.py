"""Node-role recommendation engine (ADR-010).

Rule-based and transparent: signals are derived from the hardware profile,
scored against weighted conditions from config/recommendation_rules.yml, and
every role's score is returned alongside the winner and its rationale.

A future ML recommender implements the same `Recommender` protocol and swaps
in behind `get_recommender()` without touching callers.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel

from app.schemas.node import HardwareProfile

_LLM_RUNTIMES = {"ollama", "llama.cpp", "llamacpp", "vllm"}
_VISION_MARKERS = ("vision", "llava", "clip")

_DEFAULT_RULES_PATH = Path(__file__).parents[2] / "config" / "recommendation_rules.yml"


class Recommendation(BaseModel):
    role: str
    confidence: float  # winning score, 0..1
    rationale: list[str]  # human-readable reasons behind the winning role
    scores: dict[str, float]  # every role's score, for transparency


class Recommender(Protocol):
    def recommend(self, profile: HardwareProfile) -> Recommendation: ...


@lru_cache
def _load_rules() -> dict[str, Any]:
    with open(_DEFAULT_RULES_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _tier(value: float, thresholds: dict[str, float], base: str) -> str:
    """Highest tier whose threshold the value meets; `base` if none."""
    tier = base
    for name, threshold in sorted(thresholds.items(), key=lambda kv: kv[1]):
        if value >= threshold:
            tier = name
    return tier


def extract_signals(profile: HardwareProfile) -> dict[str, Any]:
    tiers = _load_rules()["tiers"]
    max_vram = max((gpu.vram_gb for gpu in profile.gpus), default=0.0)
    runtime_names = {r.name.lower() for r in profile.runtimes}
    runtime_models = [m.lower() for r in profile.runtimes for m in r.models]
    return {
        "gpu_tier": _tier(max_vram, tiers["gpu_vram_gb"], base="none"),
        "ram_tier": _tier(profile.ram_gb, tiers["ram_gb"], base="low"),
        "cpu_tier": _tier(profile.cpu_cores, tiers["cpu_cores"], base="low"),
        "storage_tier": _tier(profile.storage_gb, tiers["storage_gb"], base="small"),
        "has_llm_runtime": bool(runtime_names & _LLM_RUNTIMES),
        "has_vision_hint": (
            profile.extra.get("camera") is True
            or any(marker in m for m in runtime_models for marker in _VISION_MARKERS)
        ),
    }


def _matches(condition: dict[str, Any], signals: dict[str, Any]) -> bool:
    value = signals[condition["signal"]]
    if "in" in condition:
        return value in condition["in"]
    return value == condition.get("equals", True)


class RuleBasedRecommender:
    def recommend(self, profile: HardwareProfile) -> Recommendation:
        signals = extract_signals(profile)
        roles: dict[str, list[dict[str, Any]]] = _load_rules()["roles"]

        scores: dict[str, float] = {}
        rationales: dict[str, list[str]] = {}
        for role, conditions in roles.items():
            total_weight = sum(c["weight"] for c in conditions)
            matched = [c for c in conditions if _matches(c, signals)]
            scores[role] = round(sum(c["weight"] for c in matched) / total_weight, 3)
            rationales[role] = [c["reason"] for c in matched]

        # ties resolve in config order (dict preserves YAML order)
        winner = max(scores, key=lambda role: scores[role])
        return Recommendation(
            role=winner,
            confidence=scores[winner],
            rationale=rationales[winner],
            scores=scores,
        )


@lru_cache
def get_recommender() -> Recommender:
    return RuleBasedRecommender()
