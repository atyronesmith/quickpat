"""Tests for Anthropic LLM provider."""

import pytest
from unittest.mock import MagicMock, patch

from quickpat import config

pytest.importorskip("anthropic")

from quickpat.providers.anthropic import AnthropicProvider   # noqa: E402
from quickpat.providers.factory import make_provider   # noqa: E402


class TestAnthropicMaxTokens:
    def test_default_from_config(self):
        config.load_config(path='/nonexistent')
        provider = AnthropicProvider()
        assert provider.max_tokens == 4096

    def test_config_file_override(self, tmp_path):
        cfg_file = tmp_path / 'quickpat.yaml'
        cfg_file.write_text('llm:\n  anthropic:\n    max_tokens: 8192\n')
        config.load_config(path=str(cfg_file))
        provider = AnthropicProvider()
        assert provider.max_tokens == 8192

    def test_constructor_override(self):
        config.load_config(path='/nonexistent')
        provider = AnthropicProvider(max_tokens=2048)
        assert provider.max_tokens == 2048

    def test_factory_passes_max_tokens(self):
        config.load_config(path='/nonexistent')
        provider = make_provider({
            'provider': 'anthropic',
            'max_tokens': 6000,
        })
        assert provider.max_tokens == 6000

    @patch('anthropic.Anthropic')
    def test_complete_uses_configured_max_tokens(self, mock_anthropic_cls):
        config.load_config(path='/nonexistent')
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='ok')],
            model='claude-test',
            usage=MagicMock(input_tokens=1, output_tokens=2),
        )

        provider = AnthropicProvider(max_tokens=5000)
        provider.complete('system', 'prompt')

        mock_client.messages.create.assert_called_once()
        assert mock_client.messages.create.call_args.kwargs['max_tokens'] == 5000

    @patch('anthropic.Anthropic')
    def test_complete_kwargs_override(self, mock_anthropic_cls):
        config.load_config(path='/nonexistent')
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text='ok')],
            model='claude-test',
            usage=MagicMock(input_tokens=1, output_tokens=2),
        )

        provider = AnthropicProvider(max_tokens=5000)
        provider.complete('system', 'prompt', max_tokens=9000)

        assert mock_client.messages.create.call_args.kwargs['max_tokens'] == 9000
