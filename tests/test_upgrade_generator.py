"""Tests for the upgrade runbook generator (Item 2b)."""

import textwrap
import pytest
from pathlib import Path

from quickpat.compose.upgrade_generator import (
    generate_upgrade_runbook,
    _diff_subscriptions,
    _diff_dsc,
)
from quickpat.compose.version_registry import resolve_version
from quickpat.compose.parser import load_application_spec
from quickpat.pipeline import compose_upgrade_from_spec


# ── Helpers ────────────────────────────────────────────────────────────────────

def _minimal_spec(tmp_path: Path) -> tuple:
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(textwrap.dedent("""\
        apiVersion: supplychain/v1alpha1
        kind: ApplicationSpec
        metadata:
          name: test-pattern
          tier: sandbox
          upstream: {}
        blocks:
          platform:
            type: ai-platform-foundation
        wiring: []
        custom: {}
    """))
    spec = load_application_spec(str(spec_file))
    return spec, str(spec_file)


# ── _diff_subscriptions unit tests ────────────────────────────────────────────

class TestDiffSubscriptions:
    def test_no_change_returns_empty(self):
        data = resolve_version('rhoai', '3.5')
        assert _diff_subscriptions(data, data) == []

    def test_channel_change_detected(self):
        from_data = resolve_version('rhoai', '3.4')
        to_data = resolve_version('rhoai', '3.5')
        changes = _diff_subscriptions(from_data, to_data)
        ai_change = next((c for c in changes if c['op_key'] == 'openshift-ai'), None)
        assert ai_change is not None
        assert ai_change['from']['channel'] == 'stable-3.4'
        assert ai_change['to']['channel'] == 'stable-3.5'

    def test_new_operator_marked_as_new(self):
        from_data = resolve_version('rhoai', '2.25')  # no cert-manager
        to_data = resolve_version('rhoai', '3.0')    # has cert-manager
        changes = _diff_subscriptions(from_data, to_data)
        new_ops = [c for c in changes if c['is_new']]
        new_keys = {c['op_key'] for c in new_ops}
        assert 'cert-manager' in new_keys
        assert 'jobset' in new_keys


# ── _diff_dsc unit tests ───────────────────────────────────────────────────────

class TestDiffDsc:
    def test_no_change_returns_empty(self):
        data = resolve_version('rhoai', '3.5')
        assert _diff_dsc(data, data) == []

    def test_new_component_detected(self):
        from_data = resolve_version('rhoai', '3.0')   # no mlflowoperator
        to_data = resolve_version('rhoai', '3.4')     # has mlflowoperator
        changes = _diff_dsc(from_data, to_data)
        new_comps = [c for c in changes if c['is_new']]
        assert any(c['component'] == 'mlflowoperator' for c in new_comps)

    def test_same_versions_no_dsc_change(self):
        data = resolve_version('rhoai', '3.4')
        assert _diff_dsc(data, data) == []


# ── generate_upgrade_runbook ───────────────────────────────────────────────────

class TestGenerateUpgradeRunbook:
    def test_creates_runbook_file(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        assert path.exists()
        assert path.name == 'RUNBOOK.md'

    def test_runbook_in_versioned_directory(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        assert 'rhoai-v3.4-to-v3.5' in str(path.parent)

    def test_runbook_contains_version_header(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert '3.4' in content
        assert '3.5' in content
        assert 'RUNBOOK' in content.upper() or 'Upgrade Runbook' in content

    def test_runbook_has_pre_upgrade_section(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'Pre-upgrade' in content or 'pre-upgrade' in content

    def test_runbook_has_subscription_section(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'Subscription' in content or 'subscription' in content

    def test_runbook_has_procedure_section(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'procedure' in content.lower() or 'Upgrade procedure' in content

    def test_runbook_has_verification_section(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'verification' in content.lower() or 'Post-upgrade' in content

    def test_blocking_change_2x_to_3x(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='2.25', to_version='3.0',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'BLOCKING' in content
        assert 'blocking' in content.lower()

    def test_no_blocking_change_34_to_35(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        # No blocking changes between 3.4 and 3.5
        assert 'BLOCKING' not in content

    def test_stable_channel_shown_in_runbook(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'stable-3.4' in content
        assert 'stable-3.5' in content

    def test_new_operators_2x_to_3x(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='2.25', to_version='3.0',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'cert-manager' in content.lower() or 'cert' in content.lower()
        assert 'jobset' in content.lower()

    def test_runbook_contains_compose_command(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        path = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=tmp_path / 'upgrade',
        )
        content = path.read_text()
        assert 'quickpat compose' in content
        assert 'rhoai=3.5' in content

    def test_mlflow_dsc_change_34_to_35(self, tmp_path):
        # mlflowoperator was ADDED in 3.4 vs 3.0, but is same in 3.4 and 3.5
        spec, _ = _minimal_spec(tmp_path)
        path_30_34 = generate_upgrade_runbook(
            spec=spec, platform='rhoai',
            from_version='3.0', to_version='3.4',
            output_dir=tmp_path / 'upgrade',
        )
        content = path_30_34.read_text()
        assert 'mlflowoperator' in content

    def test_invalid_platform_raises(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        with pytest.raises(ValueError, match="Unknown platform"):
            generate_upgrade_runbook(
                spec=spec, platform='fakeplat',
                from_version='1.0', to_version='2.0',
                output_dir=tmp_path / 'upgrade',
            )

    def test_invalid_version_raises(self, tmp_path):
        spec, _ = _minimal_spec(tmp_path)
        with pytest.raises(ValueError, match="Unknown version"):
            generate_upgrade_runbook(
                spec=spec, platform='rhoai',
                from_version='3.4', to_version='99.0',
                output_dir=tmp_path / 'upgrade',
            )


# ── Pipeline integration ────────────────────────────────────────────────────────

class TestComposeUpgradeFromSpec:
    def test_returns_success(self, tmp_path):
        _, spec_file = _minimal_spec(tmp_path)
        result = compose_upgrade_from_spec(
            spec_path=spec_file, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=str(tmp_path / 'upgrade'),
        )
        assert result.success

    def test_runbook_file_in_files_created(self, tmp_path):
        _, spec_file = _minimal_spec(tmp_path)
        result = compose_upgrade_from_spec(
            spec_path=spec_file, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=str(tmp_path / 'upgrade'),
        )
        assert 'RUNBOOK.md' in result.files_created

    def test_pattern_dir_is_runbook_directory(self, tmp_path):
        _, spec_file = _minimal_spec(tmp_path)
        result = compose_upgrade_from_spec(
            spec_path=spec_file, platform='rhoai',
            from_version='3.4', to_version='3.5',
            output_dir=str(tmp_path / 'upgrade'),
        )
        assert 'rhoai-v3.4-to-v3.5' in result.pattern_dir

    def test_default_output_dir_is_upgrade_subfolder(self, tmp_path):
        _, spec_file = _minimal_spec(tmp_path)
        result = compose_upgrade_from_spec(
            spec_path=spec_file, platform='rhoai',
            from_version='3.4', to_version='3.5',
        )
        # Default output is upgrade/ in spec dir
        assert 'upgrade' in result.pattern_dir
        assert result.success
