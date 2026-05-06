from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_ms: float = 0.0
    parsed: dict | None = None


@runtime_checkable
class Provider(Protocol):
    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse: ...
