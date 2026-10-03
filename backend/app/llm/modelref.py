"""`provider:model` identifiers.

Only the first colon separates the provider, so local tags such as
`ollama:llama3.2:1b` keep their own colons.
"""

import re
from dataclasses import dataclass

PROVIDER_ID_PATTERN = r"^[a-z][a-z0-9_]{0,39}$"
_PROVIDER_RE = re.compile(PROVIDER_ID_PATTERN)
_MODEL_RE = re.compile(r"^[^\s\x00-\x1f\x7f]{1,200}$")


@dataclass(frozen=True)
class ModelRef:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def validate_model_id(model: str) -> str:
    if not _MODEL_RE.fullmatch(model) or "://" in model:
        raise ValueError("invalid model identifier")
    return model


def parse_model_ref(value: str) -> ModelRef:
    provider, sep, model = value.partition(":")
    if not sep or not _PROVIDER_RE.fullmatch(provider):
        raise ValueError("model reference must look like 'provider:model'")
    return ModelRef(provider, validate_model_id(model))
