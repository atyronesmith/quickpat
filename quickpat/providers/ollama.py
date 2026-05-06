from __future__ import annotations

import json as _json
import os
import time
import urllib.request

from .base import LLMResponse
from ..config import get as cfg


class OllamaProvider:

    def __init__(self, model: str | None = None, base_url: str | None = None):
        self.model = model or cfg("llm.ollama.model", "llama3.1")
        self.base_url = (
            base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or cfg("llm.ollama.base_url", "http://localhost:11434")
        )

    def complete(self, system: str, prompt: str, **kwargs) -> LLMResponse:
        response_schema = kwargs.get("response_schema")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if response_schema:
            payload["format"] = response_schema

        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )

        start = time.perf_counter()
        with urllib.request.urlopen(req) as resp:
            result = _json.loads(resp.read())
        elapsed_ms = (time.perf_counter() - start) * 1000

        content = result["message"]["content"]
        parsed = _json.loads(content) if response_schema else None

        return LLMResponse(
            content=content,
            model=result.get("model", self.model),
            provider="ollama",
            input_tokens=result.get("prompt_eval_count", 0),
            output_tokens=result.get("eval_count", 0),
            total_ms=round(elapsed_ms, 1),
            parsed=parsed,
        )
