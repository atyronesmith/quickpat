"""Tests for _cmd_create interactive/non-interactive behavior."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from quickpat.cli import _cmd_create
from quickpat.pipeline import TransformResult


def _make_args(**overrides):
    """Build a minimal Namespace mimicking argparse output for `create`."""
    defaults = {
        'path': '/tmp/fake',
        'output': None,
        'name': None,
        'non_interactive': False,
        'crc_scripts': False,
        'ignore_differences': [],
        'llm': 'none',
        'model': None,
        'llm_url': None,
        'transform': False,
        'transform_rules': None,
        'patterns_dir': '/tmp/patterns',
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


@pytest.fixture
def mock_analysis():
    """Patch skill_analyze to return a minimal analysis."""
    analysis = MagicMock()
    analysis.name = 'testapp'
    analysis.version = '1.0.0'
    analysis.charts = []
    analysis.detected_operators = []
    analysis.detected_secrets = []
    analysis.dependencies = []
    analysis.has_vector_db = False
    analysis.has_llm_service = False
    analysis.has_object_storage = False
    analysis.has_pipeline = False
    analysis.has_gpu_requirement = False
    analysis.resource_types = []
    analysis.chart_path = '/tmp/fake'
    analysis.description = ''
    with patch('quickpat.cli.skill_analyze', return_value=analysis):
        yield analysis


class TestInteractiveExitCodes:
    """Interactive mode must exit non-zero on failure."""

    def test_interactive_local_exits_nonzero_on_failure(self, mock_analysis, tmp_path):
        failed_result = TransformResult(success=False, warnings=['something broke'])
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'local',
            'use_vault': False,
        }

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform', return_value=failed_result), \
             pytest.raises(SystemExit) as exc_info:
            _cmd_create(str(tmp_path), _make_args())

        assert exc_info.value.code == 1

    def test_interactive_local_exits_zero_on_success(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'local',
            'use_vault': False,
        }

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform', return_value=ok_result), \
             pytest.raises(SystemExit) as exc_info:
            _cmd_create(str(tmp_path), _make_args())

        assert exc_info.value.code == 0

    def test_interactive_remote_exits_nonzero_on_failure(self, mock_analysis, tmp_path):
        failed_result = TransformResult(success=False, warnings=['remote failed'])
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'remote',
            'use_vault': True,
            'git_repo_url': 'https://github.com/test/repo',
            'chart_path_in_repo': 'deploy/helm',
            'chart_branch': 'main',
            'operators': ['openshift-ai'],
            'tier': 'sandbox',
        }

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform_remote', return_value=failed_result), \
             pytest.raises(SystemExit) as exc_info:
            _cmd_create(str(tmp_path), _make_args())

        assert exc_info.value.code == 1

    def test_interactive_remote_exits_zero_on_success(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'remote',
            'use_vault': True,
            'git_repo_url': 'https://github.com/test/repo',
            'chart_path_in_repo': 'deploy/helm',
            'chart_branch': 'main',
            'operators': ['openshift-ai'],
            'tier': 'sandbox',
        }

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform_remote', return_value=ok_result), \
             pytest.raises(SystemExit) as exc_info:
            _cmd_create(str(tmp_path), _make_args())

        assert exc_info.value.code == 0


class TestLlmDecoupledFromInteractive:
    """--llm flag no longer forces non-interactive mode."""

    def test_llm_flag_still_enters_interactive(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'local',
            'use_vault': False,
        }

        with patch('quickpat.cli.interactive_config', return_value=config) as mock_ic, \
             patch('quickpat.cli.transform', return_value=ok_result), \
             patch('quickpat.cli.make_provider', return_value=MagicMock()) as mock_mp, \
             pytest.raises(SystemExit):
            _cmd_create(str(tmp_path), _make_args(llm='openai'))

        mock_ic.assert_called_once()
        mock_mp.assert_called_once()

    def test_non_interactive_flag_skips_prompts(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )

        with patch('quickpat.cli.interactive_config') as mock_ic, \
             patch('quickpat.cli.build_default_config', return_value={
                 'pattern_name': 'test',
                 'output_dir': str(tmp_path / 'out'),
                 'chart_strategy': 'local',
                 'use_vault': False,
             }), \
             patch('quickpat.cli.transform', return_value=ok_result), \
             pytest.raises(SystemExit):
            _cmd_create(str(tmp_path), _make_args(non_interactive=True))

        mock_ic.assert_not_called()


class TestInteractiveRemoteConfigPlumbing:
    """Interactive mode passes user config into transform_remote."""

    def test_passes_extra_config_to_remote(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'remote',
            'use_vault': False,
            'git_repo_url': 'https://github.com/user/custom-repo',
            'chart_path_in_repo': 'charts/myapp',
            'chart_branch': 'develop',
            'operators': ['openshift-ai', 'serverless'],
            'tier': 'tested',
        }

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform_remote', return_value=ok_result) as mock_tr, \
             pytest.raises(SystemExit):
            _cmd_create(str(tmp_path), _make_args())

        mock_tr.assert_called_once()
        call_kwargs = mock_tr.call_args[1]
        extra = call_kwargs['extra_config']
        assert extra['git_repo_url'] == 'https://github.com/user/custom-repo'
        assert extra['chart_path_in_repo'] == 'charts/myapp'
        assert extra['chart_branch'] == 'develop'
        assert extra['operators'] == ['openshift-ai', 'serverless']
        assert extra['tier'] == 'tested'
        assert extra['use_vault'] is False

    def test_passes_llm_to_remote_interactive(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'remote',
            'use_vault': True,
            'git_repo_url': '',
            'chart_path_in_repo': '',
            'chart_branch': '',
            'operators': [],
            'tier': 'sandbox',
        }
        fake_llm = MagicMock()

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform_remote', return_value=ok_result) as mock_tr, \
             patch('quickpat.cli.make_provider', return_value=fake_llm), \
             pytest.raises(SystemExit):
            _cmd_create(str(tmp_path), _make_args(llm='anthropic'))

        call_kwargs = mock_tr.call_args[1]
        assert call_kwargs['llm'] is fake_llm

    def test_passes_llm_to_local_interactive(self, mock_analysis, tmp_path):
        ok_result = TransformResult(
            success=True, pattern_dir=str(tmp_path / 'out'), files_created=['a.yaml'],
        )
        config = {
            'pattern_name': 'test',
            'output_dir': str(tmp_path / 'out'),
            'chart_strategy': 'local',
            'use_vault': False,
        }
        fake_llm = MagicMock()

        with patch('quickpat.cli.interactive_config', return_value=config), \
             patch('quickpat.cli.transform', return_value=ok_result) as mock_t, \
             patch('quickpat.cli.make_provider', return_value=fake_llm), \
             pytest.raises(SystemExit):
            _cmd_create(str(tmp_path), _make_args(llm='openai'))

        call_kwargs = mock_t.call_args[1]
        assert call_kwargs['llm'] is fake_llm
