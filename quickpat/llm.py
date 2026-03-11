"""LLM adapter factories for quickpat.

Each factory returns a callable: (system, user, response_schema=None) -> str | dict
When response_schema is provided, adapters return parsed dicts; otherwise plain text.
"""

from typing import Callable

from .config import get as cfg

LLMCallable = Callable


def make_openai_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using OpenAI's API."""
    import json as _json
    import openai
    model = model or cfg("llm.openai.model", "gpt-4o-mini")
    api_key = api_key or cfg("llm.openai.api_key") or None
    client = openai.OpenAI(api_key=api_key)

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": response_schema,
                },
            }
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_anthropic_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using Anthropic's API."""
    import anthropic
    model = model or cfg("llm.anthropic.model", "claude-sonnet-4-20250514")
    api_key = api_key or cfg("llm.anthropic.api_key") or None
    client = anthropic.Anthropic(api_key=api_key)

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if response_schema:
            kwargs["tools"] = [{
                "name": "structured_response",
                "description": "Provide a structured response",
                "input_schema": response_schema,
            }]
            kwargs["tool_choice"] = {
                "type": "tool", "name": "structured_response",
            }
        response = client.messages.create(**kwargs)
        if response_schema:
            for block in response.content:
                if block.type == "tool_use":
                    return block.input
            return {}
        return response.content[0].text
    return call


def make_ollama_llm(model: str = None, base_url: str = None):
    """Create an LLM callable using a local Ollama instance."""
    import json as _json
    import urllib.request
    model = model or cfg("llm.ollama.model", "llama3.1")
    base_url = base_url or cfg("llm.ollama.base_url", "http://localhost:11434")

    def call(system: str, user: str, response_schema: dict = None):
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        }
        if response_schema:
            payload["format"] = response_schema
        data = _json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            result = _json.loads(resp.read())
        content = result["message"]["content"]
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_vllm_llm(model: str = None, base_url: str = None):
    """Create an LLM callable using vLLM's OpenAI-compatible API."""
    import json as _json
    import openai
    model = model or cfg("llm.vllm.model", "default")
    base_url = base_url or cfg("llm.vllm.base_url", "http://localhost:8000")
    client = openai.OpenAI(api_key="unused", base_url=f"{base_url}/v1")

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["extra_body"] = {"guided_json": response_schema}
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_deepinfra_llm(model: str = None, api_key: str = None):
    """Create an LLM callable using DeepInfra's OpenAI-compatible API."""
    import json as _json
    import os
    import openai

    model = model or cfg("llm.deepinfra.model", "Qwen/Qwen2.5-72B-Instruct")
    key = api_key or cfg("llm.deepinfra.api_key") or os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise ValueError(
            "DeepInfra API key required. Set DEEPINFRA_API_KEY env var "
            "or pass api_key parameter."
        )
    client = openai.OpenAI(
        api_key=key,
        base_url="https://api.deepinfra.com/v1/openai",
    )

    def call(system: str, user: str, response_schema: dict = None):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if response_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": response_schema,
                },
            }
        response = client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        if response_schema:
            return _json.loads(content)
        return content
    return call


def make_llm(provider: str, model: str = None, base_url: str = None):
    """Create an LLM callable by provider name. Returns None for 'none'."""
    if provider == "none" or not provider:
        return None
    factories = {
        "openai": lambda: make_openai_llm(model=model),
        "anthropic": lambda: make_anthropic_llm(model=model),
        "ollama": lambda: make_ollama_llm(model=model, base_url=base_url),
        "vllm": lambda: make_vllm_llm(model=model, base_url=base_url),
        "deepinfra": lambda: make_deepinfra_llm(model=model),
    }
    factory = factories.get(provider)
    if not factory:
        raise ValueError(f"Unknown LLM provider: {provider}")
    return factory()
