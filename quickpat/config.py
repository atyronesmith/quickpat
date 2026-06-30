"""Configuration management for quickpat.

Loads settings from quickpat.yaml (project root) or
~/.config/quickpat/config.yaml, with env var overrides.
"""

from pathlib import Path

import yaml


DEFAULTS = {
    "llm": {
        "provider": "none",
        "openai": {"model": "gpt-4o-mini"},
        "anthropic": {"model": "claude-sonnet-4-20250514"},
        "ollama": {"model": "llama3.1", "base_url": "http://localhost:11434"},
        "vllm": {"model": "default", "base_url": "http://localhost:8000"},
        "deepinfra": {"model": "Qwen/Qwen2.5-72B-Instruct"},
    },
    "pattern": {
        "output_dir": "~/patterns",
        "chart_strategy": "local",
        "clustergroup_version": "0.9.*",
        "default_vault_prefix": "hub",
        "subchart_cache_dir": "~/.cache/quickpat/charts",
    },
    "infrastructure": {
        "vault_chart_version": "0.1.*",
        "external_secrets_chart_version": "0.0.*",
    },
    "registry": {
        "quickstart_url": (
            "https://raw.githubusercontent.com/rh-ai-quickstart/"
            "ai-quickstart-pub/main/.gitmodules"
        ),
        "chart_repo_index_url": (
            "https://rh-ai-quickstart.github.io/"
            "ai-architecture-charts/index.yaml"
        ),
        "github_base": "https://github.com/rh-ai-quickstart",
        "timeout": 10,
    },
    "platforms": ["AWS", "Azure", "GCP", "IBMCloud", "None"],
}

_config = None


def _deep_merge(base, override):
    """Merge override into base dict recursively."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _find_config_file():
    """Find config file: project root first, then ~/.config/quickpat/."""
    local = Path("quickpat.yaml")
    if local.exists():
        return local

    xdg = Path.home() / ".config" / "quickpat" / "config.yaml"
    if xdg.exists():
        return xdg

    return None


def load_config(path=None):
    """Load config from file, merging with defaults. Env vars override both."""
    global _config

    config_path = Path(path) if path else _find_config_file()

    if config_path and config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        _config = _deep_merge(DEFAULTS, user_config)
    else:
        _config = _deep_merge({}, DEFAULTS)

    return _config


def get_config():
    """Get the current config, loading if needed."""
    global _config
    if _config is None:
        load_config()
    return _config


def get(key_path, default=None):
    """Get a config value by dot-separated path.

    Example: get('llm.openai.model') -> 'gpt-4o-mini'
    """
    config = get_config()
    keys = key_path.split(".")
    d = config
    for key in keys:
        if isinstance(d, dict) and key in d:
            d = d[key]
        else:
            return default
    return d
