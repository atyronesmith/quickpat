"""Tests for the sub-chart inspector module."""

import pytest
import yaml
from pathlib import Path

from quickpat.subchart import (
    SubChartInfo,
    SecretGate,
    ComputedField,
    analyze_subchart,
    _detect_secret_gates,
    _detect_env_secret_refs,
    _extract_resource_types,
    _extract_secret_info,
    _find_chart_dir,
    _find_secret_name,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _write_subchart(path, name, version="0.1.0", values=None, templates=None):
    """Create a mock sub-chart directory structure."""
    path.mkdir(parents=True, exist_ok=True)
    chart = {"apiVersion": "v2", "name": name, "version": version, "type": "application"}
    (path / "Chart.yaml").write_text(yaml.dump(chart))
    if values is not None:
        if isinstance(values, str):
            (path / "values.yaml").write_text(values)
        else:
            (path / "values.yaml").write_text(yaml.dump(values))
    tmpl_dir = path / "templates"
    tmpl_dir.mkdir(exist_ok=True)
    if templates:
        for fname, content in templates.items():
            (tmpl_dir / fname).write_text(content)


# ── Chart Directory Discovery ───────────────────────────────────────


class TestFindChartDir:
    def test_direct(self, tmp_path):
        (tmp_path / "Chart.yaml").write_text("name: test\n")
        assert _find_chart_dir(tmp_path) == tmp_path

    def test_nested_one_level(self, tmp_path):
        sub = tmp_path / "myapp"
        sub.mkdir()
        (sub / "Chart.yaml").write_text("name: test\n")
        assert _find_chart_dir(tmp_path) == sub

    def test_nested_two_levels(self, tmp_path):
        sub = tmp_path / "pgvector" / "helm"
        sub.mkdir(parents=True)
        (sub / "Chart.yaml").write_text("name: pgvector\n")
        assert _find_chart_dir(tmp_path) == sub

    def test_no_chart(self, tmp_path):
        assert _find_chart_dir(tmp_path) is None


# ── Secret Gate Detection ────────────────────────────────────────────


class TestSecretGateDetection:
    def test_detects_secret_create(self, tmp_path):
        values = {"secret": {"create": True, "user": "pg", "password": "pw"}}
        gates = _detect_secret_gates(values)
        assert len(gates) == 1
        assert gates[0].condition_path == "secret.create"
        assert gates[0].default_value is True

    def test_detects_secret_enabled(self, tmp_path):
        values = {"secret": {"enabled": True, "hf_token": ""}}
        gates = _detect_secret_gates(values)
        assert len(gates) == 1
        assert gates[0].condition_path == "secret.enabled"

    def test_nested_secret_gate(self, tmp_path):
        values = {
            "minio": {
                "secret": {"create": True, "user": "u", "password": "p"},
            },
        }
        gates = _detect_secret_gates(values)
        assert len(gates) == 1
        assert gates[0].condition_path == "minio.secret.create"

    def test_multiple_gates(self, tmp_path):
        values = {
            "secret": {"create": True},
            "minio": {"secret": {"create": True}},
        }
        gates = _detect_secret_gates(values)
        assert len(gates) == 2
        paths = {g.condition_path for g in gates}
        assert "secret.create" in paths
        assert "minio.secret.create" in paths

    def test_no_gates(self):
        values = {"image": {"repository": "quay.io/test"}, "replicas": 1}
        gates = _detect_secret_gates(values)
        assert gates == []

    def test_false_default(self):
        values = {"secret": {"create": False}}
        gates = _detect_secret_gates(values)
        assert len(gates) == 1
        assert gates[0].default_value is False

    def test_finds_k8s_secret_name(self, tmp_path):
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "secret.yaml").write_text(
            '{{- if .Values.secret.create }}\n'
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: pgvector\n'
            'data:\n'
            '  user: {{ .Values.secret.user | b64enc }}\n'
            '{{- end }}\n'
        )
        values = {"secret": {"create": True, "user": "pg"}}
        gates = _detect_secret_gates(values, tmpl_dir)
        assert len(gates) == 1
        assert gates[0].k8s_secret_name == "pgvector"


# ── Env SecretKeyRef Detection ───────────────────────────────────────


class TestEnvSecretRefs:
    def test_detects_secretKeyRef(self):
        values = {
            "env": [
                {
                    "name": "POSTGRES_USER",
                    "valueFrom": {
                        "secretKeyRef": {"key": "user", "name": "pgvector"},
                    },
                },
                {
                    "name": "POSTGRES_PASSWORD",
                    "valueFrom": {
                        "secretKeyRef": {"key": "password", "name": "pgvector"},
                    },
                },
            ],
        }
        refs = _detect_env_secret_refs(values)
        assert refs["POSTGRES_USER"] == ("pgvector", "user")
        assert refs["POSTGRES_PASSWORD"] == ("pgvector", "password")

    def test_no_env(self):
        assert _detect_env_secret_refs({}) == {}

    def test_env_without_secretKeyRef(self):
        values = {
            "env": [
                {"name": "PORT", "value": "8080"},
            ],
        }
        assert _detect_env_secret_refs(values) == {}

    def test_mixed_env(self):
        values = {
            "env": [
                {"name": "PORT", "value": "8080"},
                {
                    "name": "TOKEN",
                    "valueFrom": {
                        "secretKeyRef": {"key": "token", "name": "my-secret"},
                    },
                },
            ],
        }
        refs = _detect_env_secret_refs(values)
        assert len(refs) == 1
        assert refs["TOKEN"] == ("my-secret", "token")


# ── Resource Type Extraction ────────────────────────────────────────


class TestResourceTypes:
    def test_extracts_resource_types(self):
        content = (
            'apiVersion: apps/v1\n'
            'kind: StatefulSet\n'
            '---\n'
            'apiVersion: v1\n'
            'kind: Service\n'
        )
        info = SubChartInfo()
        _extract_resource_types(content, info)
        assert ("apps", "StatefulSet") in info.resource_types
        # Service is not filtered but has no API group
        assert ("", "Service") in info.resource_types

    def test_skips_common_resources(self):
        content = (
            'apiVersion: v1\n'
            'kind: Secret\n'
            '---\n'
            'apiVersion: v1\n'
            'kind: ConfigMap\n'
        )
        info = SubChartInfo()
        _extract_resource_types(content, info)
        assert info.resource_types == []

    def test_extracts_crd_types(self):
        content = (
            'apiVersion: datasciencepipelinesapplications.opendatahub.io/v1\n'
            'kind: DataSciencePipelinesApplication\n'
        )
        info = SubChartInfo()
        _extract_resource_types(content, info)
        assert (
            "datasciencepipelinesapplications.opendatahub.io",
            "DataSciencePipelinesApplication",
        ) in info.resource_types

    def test_no_duplicates(self):
        content = (
            'apiVersion: apps/v1\nkind: Deployment\n'
            '---\n'
            'apiVersion: apps/v1\nkind: Deployment\n'
        )
        info = SubChartInfo()
        _extract_resource_types(content, info)
        assert len(info.resource_types) == 1


# ── Secret Info Extraction ───────────────────────────────────────────


class TestSecretInfoExtraction:
    def test_extracts_secret_fields(self):
        content = (
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: pgvector\n'
            'data:\n'
            '  user: {{ .Values.secret.user | b64enc }}\n'
            '  password: {{ .Values.secret.password | b64enc }}\n'
            '  host: {{ .Values.secret.host | b64enc }}\n'
        )
        info = SubChartInfo()
        _extract_secret_info(content, "secret.yaml", info)
        assert "user" in info.secret_fields
        assert "password" in info.secret_fields
        assert "host" in info.secret_fields

    def test_detects_computed_field_printf(self):
        content = (
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: pgvector\n'
            'data:\n'
            '  user: {{ .Values.secret.user | b64enc }}\n'
            '  password: {{ .Values.secret.password | b64enc }}\n'
            '  jdbc-uri: {{ printf "jdbc:postgresql://%s:%s/%s" .Values.secret.host .Values.secret.port .Values.secret.dbname | b64enc }}\n'
        )
        info = SubChartInfo()
        _extract_secret_info(content, "secret.yaml", info)
        assert len(info.computed_fields) == 1
        cf = info.computed_fields[0]
        assert cf.name == "jdbc-uri"
        assert "printf" in cf.template
        assert "host" in cf.source_fields
        assert "port" in cf.source_fields
        assert "dbname" in cf.source_fields

    def test_non_secret_template_ignored(self):
        content = (
            'apiVersion: apps/v1\n'
            'kind: Deployment\n'
            'metadata:\n'
            '  name: myapp\n'
        )
        info = SubChartInfo()
        _extract_secret_info(content, "deployment.yaml", info)
        assert info.secret_fields == []

    def test_stringdata_fields(self):
        content = (
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: my-secret\n'
            'stringData:\n'
            '  MINIO_ENDPOINT: "http://{{ .Values.minio.host }}:{{ .Values.minio.port }}"\n'
            '  MINIO_ACCESS_KEY: "{{ .Values.minio.user }}"\n'
        )
        info = SubChartInfo()
        _extract_secret_info(content, "secret.yaml", info)
        assert "MINIO_ENDPOINT" in info.secret_fields
        assert "MINIO_ACCESS_KEY" in info.secret_fields
        # MINIO_ENDPOINT has two .Values refs -> computed
        assert any(cf.name == "MINIO_ENDPOINT" for cf in info.computed_fields)


# ── Full Analysis ────────────────────────────────────────────────────


class TestAnalyzeSubchart:
    def test_pgvector_style(self, tmp_path):
        chart_dir = tmp_path / "pgvector"
        values_text = (
            'replicaCount: 1\n'
            'env:\n'
            '  - name: POSTGRES_USER\n'
            '    valueFrom:\n'
            '      secretKeyRef:\n'
            '        key: user\n'
            '        name: pgvector\n'
            '  - name: POSTGRES_PASSWORD\n'
            '    valueFrom:\n'
            '      secretKeyRef:\n'
            '        key: password\n'
            '        name: pgvector\n'
            'secret:\n'
            '  create: true\n'
            '  user: postgres\n'
            '  password: rag_password\n'
        )
        secret_tmpl = (
            '{{- if .Values.secret.create }}\n'
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: pgvector\n'
            'data:\n'
            '  user: {{ .Values.secret.user | b64enc }}\n'
            '  password: {{ .Values.secret.password | b64enc }}\n'
            '{{- end }}\n'
        )
        statefulset_tmpl = (
            'apiVersion: apps/v1\n'
            'kind: StatefulSet\n'
            'metadata:\n'
            '  name: pgvector\n'
        )
        _write_subchart(
            chart_dir, "pgvector", "0.5.5",
            values=values_text,
            templates={
                "secret.yaml": secret_tmpl,
                "statefulset.yaml": statefulset_tmpl,
            },
        )

        info = analyze_subchart(tmp_path)
        assert info.name == "pgvector"
        assert info.version == "0.5.5"
        assert len(info.secret_gates) == 1
        assert info.secret_gates[0].condition_path == "secret.create"
        assert info.secret_gates[0].k8s_secret_name == "pgvector"
        assert "user" in info.secret_fields
        assert "password" in info.secret_fields
        assert info.env_secret_refs["POSTGRES_USER"] == ("pgvector", "user")
        assert ("apps", "StatefulSet") in info.resource_types
        assert info.template_hash != ""

    def test_llm_service_style(self, tmp_path):
        chart_dir = tmp_path / "llm-service"
        values_text = (
            'secret:\n'
            '  enabled: true\n'
            '  hf_token: ""\n'
        )
        secret_tmpl = (
            '{{- if .Values.secret.enabled }}\n'
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: huggingface-secret\n'
            'data:\n'
            '  HF_TOKEN: {{ .Values.secret.hf_token | b64enc }}\n'
            '{{- end }}\n'
        )
        _write_subchart(
            chart_dir, "llm-service", "0.5.9",
            values=values_text,
            templates={"secret.yaml": secret_tmpl},
        )

        info = analyze_subchart(tmp_path)
        assert info.name == "llm-service"
        assert len(info.secret_gates) == 1
        assert info.secret_gates[0].condition_path == "secret.enabled"
        assert info.secret_gates[0].k8s_secret_name == "huggingface-secret"
        assert "HF_TOKEN" in info.secret_fields

    def test_chart_with_no_secrets(self, tmp_path):
        chart_dir = tmp_path / "nfd"
        _write_subchart(
            chart_dir, "nfd", "1.0.0",
            values={"enabled": True},
            templates={
                "config.yaml": "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: nfd\n",
            },
        )

        info = analyze_subchart(tmp_path)
        assert info.name == "nfd"
        assert info.secret_gates == []
        assert info.secret_fields == []
        assert info.computed_fields == []

    def test_chart_with_computed_fields(self, tmp_path):
        chart_dir = tmp_path / "db"
        values_text = 'secret:\n  create: true\n  host: db\n  port: "5432"\n  dbname: mydb\n  user: pg\n  password: pw\n'
        secret_tmpl = (
            '{{- if .Values.secret.create }}\n'
            'kind: Secret\n'
            'apiVersion: v1\n'
            'metadata:\n'
            '  name: db\n'
            'data:\n'
            '  user: {{ .Values.secret.user | b64enc }}\n'
            '  password: {{ .Values.secret.password | b64enc }}\n'
            '  jdbc-uri: {{ printf "jdbc:postgresql://%s:%s/%s" .Values.secret.host .Values.secret.port .Values.secret.dbname | b64enc }}\n'
            '{{- end }}\n'
        )
        _write_subchart(
            chart_dir, "db", "1.0.0",
            values=values_text,
            templates={"secret.yaml": secret_tmpl},
        )

        info = analyze_subchart(tmp_path)
        assert len(info.computed_fields) == 1
        assert info.computed_fields[0].name == "jdbc-uri"

    def test_empty_dir(self, tmp_path):
        info = analyze_subchart(tmp_path)
        assert info.name == ""
        assert info.secret_fields == []
