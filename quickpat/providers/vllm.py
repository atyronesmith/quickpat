from __future__ import annotations

import json as _json
import os
import time

from .base import LLMResponse
from ..config import get as cfg


class VLLMProvider:

    def __init__(self, model: str | None = None, base_url: str | None = None):
        import openai
        self.model = model or cfg("llm.vllm.model", "default")
        self.base_url = (
            base_url
            or os.environ.get("VLLM_BASE_URL")
            or cfg("llm.vllm.base_url", "http://localhost:8000")
        )
        self._client = openai.OpenAI(
            api_key="unused", base_url=f"{self.base_url}/v1",
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
            req["extra_body"] = {"guided_json": response_schema}

        start = time.perf_counter()
        response = self._client.chat.completions.create(**req)
        elapsed_ms = (time.perf_counter() - start) * 1000

        content = response.choices[0].message.content
        usage = response.usage
        parsed = _json.loads(content) if response_schema else None

        return LLMResponse(
            content=content,
            model=response.model or self.model,
            provider="vllm",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_ms=round(elapsed_ms, 1),
            parsed=parsed,
        )
