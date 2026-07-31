"""Tests for the spec-level semantic validator (spec_validator.py)."""

import textwrap
import pytest
from pathlib import Path

from quickpat.compose.parser import load_application_spec
from quickpat.compose.spec_validator import validate_spec


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse(yaml_text: str, tmp_path: Path = None) -> object:
    """Write spec to a temp file and parse it."""
    if tmp_path is None:
        import tempfile, os
        tmp = tempfile.mkdtemp()
        tmp_path = Path(tmp)
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(textwrap.dedent(yaml_text))
    return load_application_spec(str(spec_file))


def _validate(yaml_text: str, tmp_path: Path = None):
    """Parse and validate; return (spec, result, spec_dir)."""
    if tmp_path is None:
        import tempfile
        tmp_path = Path(tempfile.mkdtemp())
    spec_file = tmp_path / 'spec.yaml'
    spec_file.write_text(textwrap.dedent(yaml_text))
    spec = load_application_spec(str(spec_file))
    result = validate_spec(spec, spec_dir=str(tmp_path))
    return spec, result, str(tmp_path)


def _errors(result) -> list:
    return [i.message for i in result.issues if i.severity == 'error']

def _warnings(result) -> list:
    return [i.message for i in result.issues if i.severity == 'warning']


_MINIMAL = """\
    apiVersion: supplychain/v1alpha1
    kind: ApplicationSpec
    metadata:
      name: test-pattern
      tier: sandbox
      upstream: {}
    blocks: {}
    wiring: []
    custom: {}
    """


# ── Valid spec — no issues ─────────────────────────────────────────────────────

class TestValidSpec:
    def test_minimal_spec_is_valid(self, tmp_path):
        _, result, _ = _validate(_MINIMAL, tmp_path)
        assert result.valid
        assert not result.issues

    def test_valid_wiring_no_issues(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              platform:
                type: ai-platform-foundation
              gpu:
                type: gpu-compute
            wiring:
              - from: gpu
                to: platform
                via: operator-dependency
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert result.valid
        assert not _errors(result)


# ── SV-1: Wiring references ────────────────────────────────────────────────────

class TestWiringReferences:
    def test_from_referencing_nonexistent_block_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              platform:
                type: ai-platform-foundation
            wiring:
              - from: nonexistent
                to: platform
                via: something
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('nonexistent' in e and 'from' in e for e in _errors(result))

    def test_to_referencing_nonexistent_block_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              platform:
                type: ai-platform-foundation
            wiring:
              - from: platform
                to: ghost
                via: something
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('ghost' in e and 'to' in e for e in _errors(result))

    def test_empty_via_is_warning_not_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              a:
                type: ai-platform-foundation
              b:
                type: gpu-compute
            wiring:
              - from: a
                to: b
                via: ""
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert result.valid          # warnings don't make it invalid
        assert any('via' in w for w in _warnings(result))


# ── SV-2 / SV-4: Template expression references ────────────────────────────────

class TestTemplateRefs:
    def test_valid_block_ref_in_inputs_no_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              virt:
                type: openshift-virtualization
              identity:
                type: keycloak-oidc
              sandbox:
                type: vm-workspace
                inputs:
                  oidc_issuer: "{{ blocks.identity.output.issuer_url }}"
            wiring:
              - from: identity
                to: sandbox
                via: oidc-jwks
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not _errors(result)

    def test_bad_block_ref_in_inputs_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              sandbox:
                type: vm-workspace
                inputs:
                  oidc_issuer: "{{ blocks.ghost_block.output.issuer_url }}"
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('ghost_block' in e for e in _errors(result))

    def test_bad_block_ref_in_custom_env_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              platform:
                type: ai-platform-foundation
            wiring: []
            custom:
              my-app:
                env:
                  ENDPOINT: "{{ blocks.missing_block.output.host }}"
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('missing_block' in e for e in _errors(result))


# ── SV-11: Unknown block type suggestions ──────────────────────────────────────

class TestUnknownBlockType:
    def test_unknown_type_is_warning_with_suggestion(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              myblock:
                type: model-servng    # typo: missing 'i'
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('model-servng' in w for w in _warnings(result))
        # should suggest the correct spelling
        assert any('model-serving' in w for w in _warnings(result))

    def test_completely_unknown_type_warns_without_suggestion(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              myblock:
                type: xyzzy-unknown-block
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('xyzzy-unknown-block' in w for w in _warnings(result))


# ── SV-13: vm-workspace without openshift-virtualization ───────────────────────

class TestBlockCombos:
    def test_vm_workspace_without_virt_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              sandbox:
                type: vm-workspace
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('openshift-virtualization' in e for e in _errors(result))

    def test_vm_workspace_with_virt_no_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              virt:
                type: openshift-virtualization
              sandbox:
                type: vm-workspace
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not _errors(result)

    def test_keycloak_and_vm_unwired_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              virt:
                type: openshift-virtualization
              identity:
                type: keycloak-oidc
              sandbox:
                type: vm-workspace
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('keycloak' in w.lower() or 'oidc' in w.lower() for w in _warnings(result))

    def test_keycloak_and_vm_wired_no_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              virt:
                type: openshift-virtualization
              identity:
                type: keycloak-oidc
              sandbox:
                type: vm-workspace
            wiring:
              - from: identity
                to: sandbox
                via: oidc-jwks
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('keycloak' in w.lower() and 'wiring' in w.lower() for w in _warnings(result))


# ── SV-8: Duplicate secret names ───────────────────────────────────────────────

class TestSecretDuplicates:
    def test_duplicate_secret_name_is_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: my-key
                vault_path: test/my-key
                fields:
                  - name: value
              - name: my-key
                vault_path: test/my-key
                fields:
                  - name: value
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('duplicate' in e.lower() for e in _errors(result))

    def test_unique_secret_names_no_error(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: key-a
                vault_path: test/key-a
                fields:
                  - name: value
              - name: key-b
                vault_path: test/key-b
                fields:
                  - name: value
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('duplicate' in e.lower() for e in _errors(result))


# ── SV-6: Secrets with no fields ───────────────────────────────────────────────

class TestSecretFields:
    def test_secret_with_no_fields_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: my-key
                vault_path: test/my-key
                fields: []
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert result.valid  # warning only
        assert any('no fields' in w or 'fields' in w for w in _warnings(result))

    def test_secret_with_fields_no_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: my-key
                vault_path: test/my-key
                fields:
                  - name: api_key
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('fields' in w for w in _warnings(result))


# ── SV-7: vault_path convention ────────────────────────────────────────────────

class TestVaultPathConvention:
    def test_mismatched_vault_path_segment_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: anthropic
                vault_path: test/credentials    # last segment 'credentials' != 'anthropic'
                fields:
                  - name: api_key
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('credentials' in w and 'anthropic' in w for w in _warnings(result))

    def test_matching_vault_path_segment_no_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: anthropic
                vault_path: test/anthropic
                fields:
                  - name: api_key
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('vault_path' in w for w in _warnings(result))


# ── SV-15: vault enabled but no secrets ────────────────────────────────────────

class TestVaultWithNoSecrets:
    def test_vault_enabled_no_secrets_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('vault' in w.lower() and 'secret' in w.lower() for w in _warnings(result))

    def test_vault_enabled_with_secrets_no_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            secrets:
              - name: key
                vault_path: test/key
                fields:
                  - name: value
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('no secrets' in w.lower() for w in _warnings(result))


# ── SV-5: Custom chart paths ────────────────────────────────────────────────────

class TestChartPaths:
    def test_missing_chart_path_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom:
              my-app:
                source:
                  chart: charts/nonexistent-chart
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('nonexistent-chart' in w for w in _warnings(result))

    def test_existing_chart_path_no_warning(self, tmp_path):
        chart_dir = tmp_path / 'charts' / 'my-app'
        chart_dir.mkdir(parents=True)
        (chart_dir / 'Chart.yaml').write_text('apiVersion: v2\nname: my-app\nversion: 0.1.0')
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom:
              my-app:
                source:
                  chart: charts/my-app
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('nonexistent' in w or 'stubbed' in w for w in _warnings(result))


# ── SV-9 / SV-10: Doc files and markers ────────────────────────────────────────

class TestDocValidation:
    def test_missing_doc_source_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            docs:
              - source: docs/missing.md
                target: README.md
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('missing.md' in w for w in _warnings(result))

    def test_balanced_markers_no_error(self, tmp_path):
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'guide.md').write_text(
            "# Title\n\n<!-- vp-only -->\nVP content\n<!-- end -->\n\nShared\n"
        )
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            docs:
              - source: docs/guide.md
                target: README.md
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not _errors(result)

    def test_unclosed_marker_is_error(self, tmp_path):
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'guide.md').write_text(
            "# Title\n\n<!-- vp-only -->\nVP content\n\nNo closing end marker\n"
        )
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            docs:
              - source: docs/guide.md
                target: README.md
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('unclosed' in e.lower() or 'end' in e.lower() for e in _errors(result))

    def test_extra_end_marker_is_error(self, tmp_path):
        (tmp_path / 'docs').mkdir()
        (tmp_path / 'docs' / 'guide.md').write_text(
            "# Title\n\n<!-- end -->\n\nContent after stray end\n"
        )
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            docs:
              - source: docs/guide.md
                target: README.md
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not result.valid
        assert any('no matching' in e.lower() for e in _errors(result))


# ── SV-12: Block secrets conflict with pattern-secrets ─────────────────────────

class TestBlockSecretsConflict:
    def test_block_secrets_with_pattern_secrets_component_is_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              llm:
                type: model-serving
                secrets:
                  api-key:
                    vault_path: test/api-key
            wiring: []
            custom:
              pattern-secrets:
                description: ExternalSecrets chart
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert any('pattern-secrets' in w or 'duplicate' in w.lower() for w in _warnings(result))

    def test_block_secrets_without_pattern_secrets_no_warning(self, tmp_path):
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: test
              tier: sandbox
              upstream: {}
            blocks:
              llm:
                type: model-serving
                secrets:
                  api-key:
                    vault_path: test/api-key
            wiring: []
            custom: {}
            """
        _, result, _ = _validate(spec_yaml, tmp_path)
        assert not any('pattern-secrets' in w for w in _warnings(result))


# ── Compose integration: errors abort compose ────────────────────────────────────

class TestComposeIntegration:
    def test_spec_error_aborts_compose(self, tmp_path):
        """A spec with a wiring error should cause compose_from_spec to fail."""
        from quickpat.pipeline import compose_from_spec
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: abort-test
              tier: sandbox
              upstream: {}
            blocks:
              platform:
                type: ai-platform-foundation
            wiring:
              - from: platform
                to: nonexistent_block
                via: test
            custom: {}
            """
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        result = compose_from_spec(str(spec_file), output_dir=str(tmp_path / 'out'))
        assert not result.success
        assert any('[spec:error]' in w for w in result.warnings)

    def test_spec_warning_does_not_abort_compose(self, tmp_path):
        """A spec with only warnings should still compose successfully."""
        from quickpat.pipeline import compose_from_spec
        spec_yaml = """\
            apiVersion: supplychain/v1alpha1
            kind: ApplicationSpec
            metadata:
              name: warn-test
              tier: sandbox
              upstream: {}
            blocks: {}
            wiring: []
            custom: {}
            vault:
              enabled: true
            """
        # vault enabled but no secrets → warning only
        spec_file = tmp_path / 'spec.yaml'
        spec_file.write_text(textwrap.dedent(spec_yaml))
        result = compose_from_spec(str(spec_file), output_dir=str(tmp_path / 'out'))
        assert result.success
        assert any('[spec:warning]' in w for w in result.warnings)
