"""Tests for the chart transformer module."""

import pytest
import yaml
from pathlib import Path

from quickpat.analyzer import QuickstartAnalysis, SecretRef, ChartInfo
from quickpat.transformer import (
    transform_chart,
    TransformResult,
    _detect_helper_prefix,
    _group_secrets,
    _externalize_secrets,
    _convert_hooks,
    _add_registry_override,
    _rewrite_hooks,
    _clear_secret_values,
    _add_secret_store_config,
)

# Import helpers from conftest (not fixtures, just utility functions)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from conftest import write_chart, write_values, write_template


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_chart_with_secrets(tmp_path):
    """Create a chart with plaintext secrets in values and deployment."""
    chart_dir = tmp_path / "myapp"
    write_chart(chart_dir, "myapp", "1.0.0")

    values_content = (
        'replicaCount: 1\n'
        '\n'
        'image:\n'
        '  repository: quay.io/example/myapp\n'
        '  tag: "1.0"\n'
        '\n'
        'pgvector:\n'
        '  enabled: true\n'
        '  secret:\n'
        '    user: postgres\n'
        '    password: my_secret_password\n'
        '    dbname: mydb\n'
        '    host: pgvector\n'
        '    port: "5432"\n'
        '\n'
        'llm-service:\n'
        '  enabled: true\n'
        '  secret:\n'
        '    hf_token: ""\n'
    )
    (chart_dir / "values.yaml").write_text(values_content)

    helpers_content = (
        '{{- define "myapp.fullname" -}}\n'
        '{{- .Release.Name }}-myapp\n'
        '{{- end }}\n'
        '{{- define "myapp.name" -}}\n'
        'myapp\n'
        '{{- end }}\n'
    )
    write_template(chart_dir, "_helpers.tpl", helpers_content)

    deployment_content = (
        'apiVersion: apps/v1\n'
        'kind: Deployment\n'
        'metadata:\n'
        '  name: {{ include "myapp.fullname" . }}\n'
        'spec:\n'
        '  template:\n'
        '    spec:\n'
        '      containers:\n'
        '        - name: myapp\n'
        '          env:\n'
        '            - name: PGVECTOR_PASSWORD\n'
        '              value: {{ .Values.pgvector.secret.password | quote }}\n'
        '            - name: PGVECTOR_USER\n'
        '              value: {{ .Values.pgvector.secret.user | quote }}\n'
        '            - name: DB_HOST\n'
        '              value: {{ .Values.pgvector.secret.host | quote }}\n'
    )
    write_template(chart_dir, "deployment.yaml", deployment_content)

    secrets = [
        SecretRef(name="password", path="myapp.pgvector.secret.password"),
        SecretRef(name="user", path="myapp.pgvector.secret.user"),
        SecretRef(name="hf_token", path="myapp.llm-service.secret.hf_token"),
    ]
    chart_info = ChartInfo(name="myapp")

    return chart_dir, secrets, chart_info


def _make_chart_with_hooks(tmp_path):
    """Create a chart with Helm hook annotations."""
    chart_dir = tmp_path / "hookapp"
    write_chart(chart_dir, "hookapp", "1.0.0")
    write_values(chart_dir, {"hookapp": {"enabled": True}})

    job_content = (
        'apiVersion: batch/v1\n'
        'kind: Job\n'
        'metadata:\n'
        '  name: init-db\n'
        '  annotations:\n'
        '    "helm.sh/hook": pre-install\n'
        '    "helm.sh/hook-weight": "-3"\n'
        '    "helm.sh/hook-delete-policy": before-hook-creation\n'
        'spec:\n'
        '  template:\n'
        '    spec:\n'
        '      containers:\n'
        '        - name: init\n'
        '          image: busybox\n'
    )
    write_template(chart_dir, "init-job.yaml", job_content)

    post_job_content = (
        'apiVersion: batch/v1\n'
        'kind: Job\n'
        'metadata:\n'
        '  name: post-setup\n'
        '  annotations:\n'
        '    "helm.sh/hook": post-install\n'
        'spec:\n'
        '  template:\n'
        '    spec:\n'
        '      containers:\n'
        '        - name: setup\n'
        '          image: busybox\n'
    )
    write_template(chart_dir, "post-job.yaml", post_job_content)

    return chart_dir


def _make_chart_with_registry(tmp_path):
    """Create a chart with hardcoded image registry."""
    chart_dir = tmp_path / "regapp"
    write_chart(chart_dir, "regapp", "1.0.0")

    values_content = (
        'replicaCount: 1\n'
        '\n'
        'image:\n'
        '  repository: quay.io/rh-ai-quickstart/llamastack-dist-ui\n'
        '  pullPolicy: Always\n'
        '  tag: 0.2.33\n'
    )
    (chart_dir / "values.yaml").write_text(values_content)

    deployment_content = (
        'apiVersion: apps/v1\n'
        'kind: Deployment\n'
        'spec:\n'
        '  template:\n'
        '    spec:\n'
        '      containers:\n'
        '        - name: app\n'
        '          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"\n'
    )
    write_template(chart_dir, "deployment.yaml", deployment_content)

    return chart_dir


# ── Helper Tests ──────────────────────────────────────────────────────


class TestDetectHelperPrefix:
    def test_detects_fullname_prefix(self, tmp_path):
        chart_dir = tmp_path / "testchart"
        chart_dir.mkdir()
        helpers = '{{- define "rag.fullname" -}}\n{{- end }}\n'
        write_template(chart_dir, "_helpers.tpl", helpers)
        assert _detect_helper_prefix(chart_dir) == "rag"

    def test_detects_name_prefix(self, tmp_path):
        chart_dir = tmp_path / "testchart"
        chart_dir.mkdir()
        helpers = '{{- define "myapp.name" -}}\nmyapp\n{{- end }}\n'
        write_template(chart_dir, "_helpers.tpl", helpers)
        assert _detect_helper_prefix(chart_dir) == "myapp"

    def test_falls_back_to_dirname(self, tmp_path):
        chart_dir = tmp_path / "fallback-chart"
        chart_dir.mkdir()
        assert _detect_helper_prefix(chart_dir) == "fallback-chart"

    def test_no_templates_dir(self, tmp_path):
        chart_dir = tmp_path / "notemplates"
        chart_dir.mkdir()
        assert _detect_helper_prefix(chart_dir) == "notemplates"


class TestGroupSecrets:
    def test_groups_by_first_segment(self):
        secrets = [
            SecretRef(name="password", path="myapp.pgvector.secret.password"),
            SecretRef(name="user", path="myapp.pgvector.secret.user"),
            SecretRef(name="hf_token", path="myapp.llm-service.secret.hf_token"),
        ]
        groups = _group_secrets(secrets, "myapp")
        assert set(groups.keys()) == {"pgvector", "llm-service"}
        assert len(groups["pgvector"]) == 2
        assert len(groups["llm-service"]) == 1

    def test_single_segment_uses_chart_name(self):
        secrets = [SecretRef(name="token", path="myapp.token")]
        groups = _group_secrets(secrets, "myapp")
        assert "myapp" in groups

    def test_strips_chart_name_prefix(self):
        secrets = [SecretRef(name="key", path="rag.config.key")]
        groups = _group_secrets(secrets, "rag")
        assert "config" in groups

    def test_no_secrets(self):
        groups = _group_secrets([], "app")
        assert groups == {}


# ── Rule 1: Secret Externalization Tests ──────────────────────────────


class TestSecretExternalization:
    def test_clears_secret_values(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        result = _externalize_secrets(chart_dir, secrets, chart_info)

        values = (chart_dir / "values.yaml").read_text()
        assert 'password: ""' in values
        assert 'my_secret_password' not in values
        assert 'values.yaml' in result.files_modified

    def test_adds_secret_store_config(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)

        values = (chart_dir / "values.yaml").read_text()
        assert 'secretStore:' in values
        assert 'vault-backend' in values

    def test_generates_external_secret_crds(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        result = _externalize_secrets(chart_dir, secrets, chart_info)

        assert any('external-secret-pgvector' in f for f in result.files_created)
        assert any('external-secret-llm-service' in f for f in result.files_created)

        es_file = chart_dir / "templates" / "external-secret-pgvector.yaml"
        assert es_file.exists()
        content = es_file.read_text()
        assert 'ExternalSecret' in content
        assert 'vault-backend' not in content  # uses template variable
        assert '.Values.secretStore.name' in content
        assert 'pgvector' in content

    def test_rewrites_deployment_secret_refs(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)

        deployment = (chart_dir / "templates" / "deployment.yaml").read_text()
        assert 'secretKeyRef' in deployment
        assert 'key: password' in deployment
        # The direct .Values reference should be gone
        assert '.Values.pgvector.secret.password | quote' not in deployment

    def test_preserves_non_secret_values(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)

        values = (chart_dir / "values.yaml").read_text()
        assert 'replicaCount: 1' in values
        assert 'enabled: true' in values

    def test_idempotent(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)

        values_after_first = (chart_dir / "values.yaml").read_text()
        deployment_after_first = (chart_dir / "templates" / "deployment.yaml").read_text()

        # Run again
        _externalize_secrets(chart_dir, secrets, chart_info)

        assert (chart_dir / "values.yaml").read_text() == values_after_first
        assert (chart_dir / "templates" / "deployment.yaml").read_text() == deployment_after_first

    def test_secret_store_not_duplicated(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)
        _externalize_secrets(chart_dir, secrets, chart_info)

        values = (chart_dir / "values.yaml").read_text()
        assert values.count('secretStore:') == 1

    def test_external_secret_gated_by_condition(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        _externalize_secrets(chart_dir, secrets, chart_info)

        es_file = chart_dir / "templates" / "external-secret-pgvector.yaml"
        content = es_file.read_text()
        assert '{{- if .Values.secretStore }}' in content
        assert '{{- end }}' in content

    def test_rules_applied_tracking(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        result = _externalize_secrets(chart_dir, secrets, chart_info)
        assert 'secrets' in result.rules_applied

    def test_no_secrets_no_changes(self, tmp_path):
        chart_dir = tmp_path / "nosecrets"
        write_chart(chart_dir, "nosecrets")
        write_values(chart_dir, {"config": {"replicas": 1}})
        result = _externalize_secrets(chart_dir, [], None)
        assert result.rules_applied == []
        assert result.files_modified == []


# ── Rule 2: Hook Conversion Tests ────────────────────────────────────


class TestHookConversion:
    def test_converts_pre_install_hook(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        result = _convert_hooks(chart_dir)

        content = (chart_dir / "templates" / "init-job.yaml").read_text()
        assert 'argocd.argoproj.io/sync-wave' in content
        assert 'helm.sh/hook' not in content
        assert '"-3"' in content  # uses hook-weight value

    def test_converts_post_install_hook(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        _convert_hooks(chart_dir)

        content = (chart_dir / "templates" / "post-job.yaml").read_text()
        assert 'argocd.argoproj.io/sync-wave: "5"' in content
        assert 'helm.sh/hook' not in content

    def test_removes_hook_delete_policy(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        _convert_hooks(chart_dir)

        content = (chart_dir / "templates" / "init-job.yaml").read_text()
        assert 'hook-delete-policy' not in content

    def test_removes_hook_weight(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        _convert_hooks(chart_dir)

        content = (chart_dir / "templates" / "init-job.yaml").read_text()
        assert 'hook-weight' not in content

    def test_no_hooks_no_changes(self, tmp_path):
        chart_dir = tmp_path / "nohooks"
        write_chart(chart_dir, "nohooks")
        write_values(chart_dir, {"x": 1})
        write_template(chart_dir, "deployment.yaml", "apiVersion: apps/v1\nkind: Deployment\n")
        result = _convert_hooks(chart_dir)
        assert result.rules_applied == []

    def test_idempotent(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        _convert_hooks(chart_dir)
        content_first = (chart_dir / "templates" / "init-job.yaml").read_text()
        _convert_hooks(chart_dir)
        content_second = (chart_dir / "templates" / "init-job.yaml").read_text()
        assert content_first == content_second

    def test_rewrite_hooks_default_wave(self):
        content = (
            'metadata:\n'
            '  annotations:\n'
            '    "helm.sh/hook": post-install\n'
        )
        result = _rewrite_hooks(content)
        assert 'sync-wave: "5"' in result

    def test_rewrite_hooks_pre_delete(self):
        content = (
            'metadata:\n'
            '  annotations:\n'
            '    "helm.sh/hook": pre-delete\n'
        )
        result = _rewrite_hooks(content)
        assert 'sync-wave: "-10"' in result

    def test_tracks_modified_files(self, tmp_path):
        chart_dir = _make_chart_with_hooks(tmp_path)
        result = _convert_hooks(chart_dir)
        assert 'hooks' in result.rules_applied
        assert any('init-job.yaml' in f for f in result.files_modified)
        assert any('post-job.yaml' in f for f in result.files_modified)


# ── Rule 3: Registry Override Tests ──────────────────────────────────


class TestRegistryOverride:
    def test_splits_registry_from_repository(self, tmp_path):
        chart_dir = _make_chart_with_registry(tmp_path)
        result = _add_registry_override(chart_dir)

        values = (chart_dir / "values.yaml").read_text()
        assert 'registry: quay.io' in values
        assert 'repository: rh-ai-quickstart/llamastack-dist-ui' in values
        assert 'values.yaml' in result.files_modified

    def test_adds_global_image_registry(self, tmp_path):
        chart_dir = _make_chart_with_registry(tmp_path)
        _add_registry_override(chart_dir)

        values = (chart_dir / "values.yaml").read_text()
        assert 'imageRegistry' in values

    def test_rewrites_template_image_line(self, tmp_path):
        chart_dir = _make_chart_with_registry(tmp_path)
        _add_registry_override(chart_dir)

        deployment = (chart_dir / "templates" / "deployment.yaml").read_text()
        assert '.Values.global.imageRegistry' in deployment
        assert '.Values.image.registry' in deployment
        assert '.Values.image.repository' in deployment

    def test_no_registry_no_changes(self, tmp_path):
        chart_dir = tmp_path / "noreg"
        write_chart(chart_dir, "noreg")
        values_content = 'image:\n  repository: myapp\n  tag: latest\n'
        (chart_dir / "values.yaml").write_text(values_content)
        result = _add_registry_override(chart_dir)
        assert result.rules_applied == []

    def test_tracks_rules_applied(self, tmp_path):
        chart_dir = _make_chart_with_registry(tmp_path)
        result = _add_registry_override(chart_dir)
        assert 'registry' in result.rules_applied


# ── Full Transform Tests ─────────────────────────────────────────────


class TestTransformChart:
    def test_applies_all_rules(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        analysis = QuickstartAnalysis(
            name="myapp", detected_secrets=secrets,
        )
        result = transform_chart(str(chart_dir), analysis, chart_info)
        assert 'secrets' in result.rules_applied
        assert 'registry' in result.rules_applied

    def test_selective_rules(self, tmp_path):
        chart_dir, secrets, chart_info = _make_chart_with_secrets(tmp_path)
        analysis = QuickstartAnalysis(
            name="myapp", detected_secrets=secrets,
        )
        result = transform_chart(
            str(chart_dir), analysis, chart_info, rules=['secrets'],
        )
        assert 'secrets' in result.rules_applied
        # registry not applied since not in rules list
        assert 'registry' not in result.rules_applied

    def test_missing_dir_returns_warning(self, tmp_path):
        analysis = QuickstartAnalysis(name="noexist")
        result = transform_chart(str(tmp_path / "noexist"), analysis)
        assert len(result.warnings) > 0
        assert 'not found' in result.warnings[0]

    def test_no_secrets_skips_secret_rule(self, tmp_path):
        chart_dir = tmp_path / "nosec"
        write_chart(chart_dir, "nosec")
        write_values(chart_dir, {"config": 1})
        analysis = QuickstartAnalysis(name="nosec", detected_secrets=[])
        result = transform_chart(str(chart_dir), analysis, rules=['secrets'])
        assert 'secrets' not in result.rules_applied

    def test_result_merge(self):
        r1 = transform_chart.__wrapped__ if hasattr(transform_chart, '__wrapped__') else None
        # Just test the TransformResult.merge method directly
        from quickpat.transformer import TransformResult
        a = TransformResult(
            rules_applied=['secrets'],
            files_modified=['a.yaml'],
        )
        b = TransformResult(
            rules_applied=['hooks'],
            files_created=['b.yaml'],
            warnings=['something'],
        )
        a.merge(b)
        assert a.rules_applied == ['secrets', 'hooks']
        assert a.files_modified == ['a.yaml']
        assert a.files_created == ['b.yaml']
        assert a.warnings == ['something']
