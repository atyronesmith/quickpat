"""Tests for target versioning: version_registry, parser, compiler, generator."""

import textwrap
import pytest
from pathlib import Path

from quickpat.compose.version_registry import (
    resolve_version, list_versions, get_breaking_changes, PLATFORM_VERSIONS,
)
from quickpat.compose.parser import load_application_spec, AppSpecError, TargetSpec
from quickpat.pipeline import compose_from_spec


# ── Helpers ────────────────────────────────────────────────────────────────────

def _compose(tmp_path, spec_yaml: str) -> Path:
    spec_file = tmp_path / 'spec.yaml'
    # dedent only if the string has common leading whitespace (inline test strings)
    spec_file.write_text(textwrap.dedent(spec_yaml).strip() + '\n')
    result = compose_from_spec(str(spec_file), output_dir=str(tmp_path / 'out'))
    assert result.success, f"compose failed: {result.warnings}"
    return tmp_path / 'out'


def _read_yaml(path: Path) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def _rhoai_spec(version: str = None) -> str:
    """Return a minimal RHOAI spec, optionally pinned to a target version."""
    lines = [
        "apiVersion: supplychain/v1alpha1",
        "kind: ApplicationSpec",
        "metadata:",
        "  name: rhoai-test",
        "  tier: sandbox",
        "  upstream: {}",
        "blocks:",
        "  platform:",
        "    type: ai-platform-foundation",
        "    config:",
        "      dsc:",
        "        kserve: Managed",
        "        dashboard: Managed",
        "        datasciencepipelines: Removed",
        "        workbenches: Removed",
        "wiring: []",
        "custom: {}",
    ]
    if version:
        lines += [
            "target:",
            f"  platform: rhoai",
            f"  version: \"{version}\"",
        ]
    return "\n".join(lines) + "\n"


# ── version_registry unit tests ───────────────────────────────────────────────

class TestVersionRegistry:
    def test_resolve_known_version(self):
        data = resolve_version('rhoai', '3.5')
        assert 'operators' in data
        assert 'openshift-ai' in data['operators']

    def test_resolve_unknown_platform_raises(self):
        with pytest.raises(ValueError, match="Unknown platform"):
            resolve_version('madeup', '1.0')

    def test_resolve_unknown_version_raises(self):
        with pytest.raises(ValueError, match="Unknown version"):
            resolve_version('rhoai', '9.9')

    def test_rhoai_3x_has_stable_versioned_channel(self):
        for ver in ('3.0', '3.4', '3.5'):
            data = resolve_version('rhoai', ver)
            channel = data['operators']['openshift-ai']['channel']
            assert channel.startswith('stable-'), f"Expected stable-X.Y channel for {ver}, got {channel!r}"

    def test_rhoai_3x_has_manual_install_plan(self):
        for ver in ('3.0', '3.4', '3.5'):
            data = resolve_version('rhoai', ver)
            assert data['operators']['openshift-ai']['installPlanApproval'] == 'Manual'

    def test_rhoai_3x_has_cert_manager_and_jobset_codeps(self):
        for ver in ('3.0', '3.4', '3.5'):
            data = resolve_version('rhoai', ver)
            assert 'cert-manager' in data['co_dependencies']
            assert 'jobset' in data['co_dependencies']

    def test_rhoai_2x_has_no_codeps(self):
        data = resolve_version('rhoai', '2.25')
        assert data['co_dependencies'] == []

    def test_rhoai_34_has_mlflow_in_dsc(self):
        data = resolve_version('rhoai', '3.4')
        assert data['dsc_defaults'].get('mlflowoperator') == 'Managed'

    def test_rhoai_2x_no_mlflow_in_dsc(self):
        data = resolve_version('rhoai', '2.25')
        assert 'mlflowoperator' not in data['dsc_defaults']

    def test_list_versions_returns_known_versions(self):
        versions = list_versions('rhoai')
        assert '3.5' in versions
        assert '3.4' in versions
        assert '2.25' in versions

    def test_get_breaking_changes_2x_to_3x(self):
        changes = get_breaking_changes('rhoai', '2.25', '3.0')
        assert len(changes) > 0
        severities = {c['severity'] for c in changes}
        assert 'blocking' in severities

    def test_get_breaking_changes_no_path_returns_empty(self):
        changes = get_breaking_changes('rhoai', '3.5', '3.6')
        assert changes == []


# ── spec.yaml target: parsing ─────────────────────────────────────────────────

class TestTargetParsing:
    def test_parse_valid_target(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: t
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            target:
              platform: rhoai
              version: "3.5"
            """
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        spec = load_application_spec(str(spec_file))
        assert spec.target is not None
        assert spec.target.platform == 'rhoai'
        assert spec.target.version == '3.5'

    def test_no_target_is_none(self, tmp_path):
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent("""\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: t
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            """))
        spec = load_application_spec(str(spec_file))
        assert spec.target is None

    def test_unknown_platform_raises_app_spec_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: t
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            target:
              platform: fakeplat
              version: "1.0"
            """
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        with pytest.raises(AppSpecError, match="Unknown platform"):
            load_application_spec(str(spec_file))

    def test_unknown_version_raises_app_spec_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: t
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            target:
              platform: rhoai
              version: "99.0"
            """
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        with pytest.raises(AppSpecError, match="Unknown version"):
            load_application_spec(str(spec_file))

    def test_missing_platform_raises(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: t
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            target:
              version: "3.5"
            """
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        with pytest.raises(AppSpecError, match="platform"):
            load_application_spec(str(spec_file))


# ── Compose integration: target channels in values-prod.yaml ─────────────────

class TestTargetChannelsInOutput:
    def test_rhoai_35_channel_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('3.5'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub is not None
        assert rhoai_sub['channel'] == 'stable-3.5'

    def test_rhoai_35_install_plan_manual(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('3.5'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub.get('installPlanApproval') == 'Manual'

    def test_rhoai_34_channel_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('3.4'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub['channel'] == 'stable-3.4'

    def test_rhoai_2x_channel_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('2.25'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub['channel'] == 'stable-2.25'

    def test_rhoai_3x_cert_manager_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('3.5'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert 'cert-manager' in subs

    def test_rhoai_3x_jobset_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('3.5'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert 'jobset' in subs

    def test_rhoai_2x_no_cert_manager_in_subscriptions(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec('2.25'))
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        assert 'cert-manager' not in subs

    def test_no_target_uses_operator_default_channel(self, tmp_path):
        out = _compose(tmp_path, _rhoai_spec())  # no version → OPERATORS default
        subs = _read_yaml(out / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub['channel'] == 'fast'

    def test_cli_target_overrides_spec_target(self, tmp_path):
        # spec says 3.4, CLI says 3.5 → CLI wins
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(_rhoai_spec('3.4'))
        from quickpat.compose.parser import TargetSpec
        cli_target = TargetSpec(platform='rhoai', version='3.5')
        result = compose_from_spec(
            str(spec_file), output_dir=str(tmp_path / 'out'), cli_target=cli_target
        )
        assert result.success
        subs = _read_yaml(tmp_path / 'out' / 'values-prod.yaml')['clusterGroup']['subscriptions']
        rhoai_sub = subs.get('rhoai') or subs.get('openshift-ai')
        assert rhoai_sub['channel'] == 'stable-3.5'
