from __future__ import annotations

import json as _json
import os
import time

from .base import LLMResponse
from ..config import get as cfg


class DeepInfraProvider:

    def __init__(self, model: str | None = None, api_key: str | None = None):
        import openai
        self.model = model or cfg("llm.deepinfra.model", "Qwen/Qwen2.5-72B-Instruct")
        key = api_key or os.environ.get("DEEPINFRA_API_KEY")
        if not key:
            raise ValueError(
                "DeepInfra API key required. Set DEEPINFRA_API_KEY env var "
                "or pass api_key parameter."
            )
        self._client = openai.OpenAI(
            api_key=key,
            base_url="https://api.deepinfra.com/v1/openai",
        )

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        response_schema = kwargs.get("response_schema")
        req = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if response_schema:
            req["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                },
            }

        start = time.perf_counter()
        response = self._client.chat.completions.create(**req)
        elapsed_ms = (time.perf_counter() - start) * 1000

        content = response.choices[0].message.content
        usage = response.usage
        parsed = _json.loads(content) if response_schema else None

        return LLMResponse(
            content=content,
            model=response.model or self.model,
            provider="deepinfra",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_ms=round(elapsed_ms, 1),
            parsed=parsed,
        )
