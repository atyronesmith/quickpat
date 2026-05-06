from __future__ import annotations

import os
import time

from .base import LLMResponse
from ..config import get as cfg


class AnthropicProvider:

    def __init__(self, model: str | None = None, api_key: str | None = None):
        import anthropic
        self.model = model or cfg("llm.anthropic.model", "claude-sonnet-4-20250514")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        response_schema = kwargs.get("response_schema")
        req = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        if response_schema:
            req["tools"] = [{
                "name": "structured_response",
                "description": "Provide a structured response",
                "input_schema": response_schema,
            }]
            req["tool_choice"] = {
                "type": "tool", "name": "structured_response",
            }

        start = time.perf_counter()
        response = self._client.messages.create(**req)
        elapsed_ms = (time.perf_counter() - start) * 1000

        parsed = None
        content = ""
        if response_schema:
            for block in response.content:
                if block.type == "tool_use":
                    parsed = block.input
                    break
            parsed = parsed or {}
            content = str(parsed)
        else:
            content = response.content[0].text

        usage = response.usage

        return LLMResponse(
            content=content,
            model=response.model or self.model,
            provider="anthropic",
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
            total_ms=round(elapsed_ms, 1),
            parsed=parsed,
        )
