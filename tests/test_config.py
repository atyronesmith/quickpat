"""Tests for quickpat.config."""

import os

import yaml

from quickpat import config


class TestConfigDefaults:
    def test_defaults_loaded(self):
        cfg = config.load_config(path="/nonexistent")
        assert cfg["llm"]["provider"] == "none"
        assert cfg["llm"]["openai"]["model"] == "gpt-4o-mini"
        assert cfg["pattern"]["clustergroup_version"] == "0.9.*"
        assert cfg["infrastructure"]["vault_chart_version"] == "0.1.*"
        assert "AWS" in cfg["platforms"]

    def test_get_dot_path(self):
        config.load_config(path="/nonexistent")
        assert config.get("llm.openai.model") == "gpt-4o-mini"
        assert config.get("pattern.output_dir") == "~/patterns"
        assert config.get("nonexistent.key", "fallback") == "fallback"


class TestConfigFile:
    def test_file_overrides_defaults(self, tmp_path):
        cfg_file = tmp_path / "quickpat.yaml"
        cfg_file.write_text(yaml.dump({
            "llm": {"openai": {"model": "gpt-4o"}},
            "pattern": {"clustergroup_version": "1.0.*"},
        }))
        cfg = config.load_config(path=str(cfg_file))
        # Overridden
        assert cfg["llm"]["openai"]["model"] == "gpt-4o"
        assert cfg["pattern"]["clustergroup_version"] == "1.0.*"
        # Defaults preserved
        assert cfg["llm"]["anthropic"]["model"] == "claude-sonnet-4-20250514"
        assert cfg["infrastructure"]["vault_chart_version"] == "0.1.*"

    def test_partial_override_merges_deeply(self, tmp_path):
        cfg_file = tmp_path / "quickpat.yaml"
        cfg_file.write_text(yaml.dump({
            "llm": {"deepinfra": {"model": "meta-llama/Llama-3.1-70B-Instruct"}},
        }))
        cfg = config.load_config(path=str(cfg_file))
        # Overridden
        assert cfg["llm"]["deepinfra"]["model"] == "meta-llama/Llama-3.1-70B-Instruct"
        # Other deepinfra defaults preserved
        assert cfg["llm"]["deepinfra"]["api_key"] == ""

    def test_custom_platforms(self, tmp_path):
        cfg_file = tmp_path / "quickpat.yaml"
        cfg_file.write_text(yaml.dump({
            "platforms": ["AWS", "GCP"],
        }))
        cfg = config.load_config(path=str(cfg_file))
        assert cfg["platforms"] == ["AWS", "GCP"]


class TestEnvOverrides:
    def test_env_overrides_config(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "quickpat.yaml"
        cfg_file.write_text(yaml.dump({
            "llm": {"openai": {"api_key": "from-file"}},
        }))
        monkeypatch.setenv("OPENAI_API_KEY", "from-env")
        cfg = config.load_config(path=str(cfg_file))
        assert cfg["llm"]["openai"]["api_key"] == "from-env"

    def test_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("DEEPINFRA_API_KEY", "di-key-123")
        cfg = config.load_config(path="/nonexistent")
        assert cfg["llm"]["deepinfra"]["api_key"] == "di-key-123"
