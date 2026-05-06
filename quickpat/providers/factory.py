from __future__ import annotations

from .base import Provider


def make_provider(config: dict) -> Provider | None:
    """Build a provider from a config dict.

    Config keys: provider, model, api_key, base_url (provider-specific).
    Returns None for provider="none" or missing provider.
    """
    provider_name = config.get("provider", "none")
    if provider_name == "none" or not provider_name:
        return None

    model = config.get("model")

    if provider_name == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(model=model, api_key=config.get("api_key"))
    elif provider_name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(model=model, api_key=config.get("api_key"))
    elif provider_name == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(model=model, base_url=config.get("base_url"))
    elif provider_name == "vllm":
        from .vllm import VLLMProvider
        return VLLMProvider(model=model, base_url=config.get("base_url"))
    elif provider_name == "deepinfra":
        from .deepinfra import DeepInfraProvider
        return DeepInfraProvider(model=model, api_key=config.get("api_key"))

    raise ValueError(f"Unknown LLM provider: {provider_name}")
