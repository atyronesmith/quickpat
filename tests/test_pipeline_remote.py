"""Tests for remote strategy pipeline functions."""

import yaml
from pathlib import Path

from quickpat.analyzer import QuickstartAnalysis, ChartInfo, ChartDependency
from quickpat.pipeline import (
    _default_classify_secrets,
    _profile_to_config,
    _static_drift_entries,
    KNOWN_IGNORE_RULES,
    transform_remote,
)
from quickpat.profile import (
    PatternProfile, SecretDecision, ComputedFieldDecision,
    DriftEntry, OverrideEntry, SourceFingerprint,
)
from quickpat.subchart import SubChartInfo, SecretGate, ComputedField
from tests.conftest import write_chart, write_values


# ── Default Secret Classification ──────────────────────────────────


class TestDefaultClassifySecrets:
    def test_password_gets_auto_generate(self):
        info = SubChartInfo(
            name="pgvector", version="0.5.5",
            secret_fields=["password"],
        )
        decisions = _default_classify_secrets({"pgvector": info})
        assert len(decisions) == 1
        assert decisions[0].classification == "auto-generate"

    def test_host_gets_static_config(self):
        info = SubChartInfo(
            name="pgvector", version="0.5.5",
            secret_fields=["host", "port", "dbname"],
        )
        decisions = _default_classify_secrets({"pgvector": info})
        assert all(d.classification == "static-config" for d in decisions)

    def test_token_gets_vault_secret(self):
        info = SubChartInfo(
            name="llm-service", version="0.5.9",
            secret_fields=["hf_token"],
        )
        decisions = _default_classify_secrets({"llm-service": info})
        # hf_token contains "token" which is in password_patterns
        assert decisions[0].classification == "auto-generate"

    def test_unknown_field_gets_vault_secret(self):
        info = SubChartInfo(
            name="custom", version="1.0",
            secret_fields=["custom_value"],
        )
        decisions = _default_classify_secrets({"custom": info})
        assert decisions[0].classification == "vault-secret"

    def test_multiple_subcharts(self):
        infos = {
            "pgvector": SubChartInfo(
                name="pgvector", version="0.5.5",
                secret_fields=["user", "password"],
            ),
            "llm-service": SubChartInfo(
                name="llm-service", version="0.5.9",
                secret_fields=["hf_token"],
            ),
        }
        decisions = _default_classify_secrets(infos)
        assert len(decisions) == 3
        groups = {d.group for d in decisions}
        assert groups == {"pgvector", "llm-service"}

    def test_empty_subcharts(self):
        assert _default_classify_secrets({}) == []


# ── Profile to Config Conversion ──────────────────────────────────


class TestProfileToConfig:
    def _make_profile(self):
        return PatternProfile(
            source_repo_url="https://github.com/rh-ai-quickstart/RAG",
            source_chart_path="deploy/helm/rag",
            vault_prefix="hub",
            secret_decisions=[
                SecretDecision(
                    name="password", group="pgvector",
                    classification="auto-generate",
                    vault_key="password", source_path="pgvector.secret.password",
                ),
                SecretDecision(
                    name="user", group="pgvector",
                    classification="static-config",
                    vault_key="user", source_path="pgvector.secret.user",
                    default_value="postgres",
                ),
                SecretDecision(
                    name="hf_token", group="llm-service",
                    classification="vault-secret",
                    vault_key="hf_token", source_path="llm-service.secret.hf_token",
                ),
            ],
            computed_fields=[
                ComputedFieldDecision(
                    group="pgvector", field_name="jdbc-uri",
                    template="postgresql://{{ .user }}:{{ .password }}@{{ .host }}:{{ .port }}/{{ .dbname }}",
                    source_fields=["user", "password", "host", "port", "dbname"],
                ),
            ],
            drift_entries=[
                DriftEntry(
                    group="route.openshift.io", kind="Route",
                    json_pointers=["/spec/host"],
                    reason="OpenShift sets host",
                ),
            ],
            override_entries=[
                OverrideEntry(
                    path="pgvector.secret.create", value=False,
                    reason="Secrets managed by pattern-secrets chart",
                ),
            ],
            secret_target_names={"pgvector": "pgvector", "llm-service": "huggingface-secret"},
        )

    def _make_analysis(self):
        return QuickstartAnalysis(
            name="rag-quickstart", version="1.0.0",
        )

    def test_produces_remote_strategy(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        assert config["chart_strategy"] == "remote"

    def test_has_git_url(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        assert config["git_repo_url"] == "https://github.com/rh-ai-quickstart/RAG"
        assert config["chart_path_in_repo"] == "deploy/helm/rag"

    def test_secret_groups_built_correctly(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        groups = config["secret_groups"]
        assert "pgvector" in groups
        assert "llm-service" in groups
        pg_names = [f["name"] for f in groups["pgvector"]]
        assert "password" in pg_names
        assert "user" in pg_names
        assert "jdbc-uri" in pg_names  # computed field

    def test_computed_field_marked(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        pg = config["secret_groups"]["pgvector"]
        jdbc = next(f for f in pg if f["name"] == "jdbc-uri")
        assert jdbc["computed"] is True
        assert "postgresql://" in jdbc["template"]

    def test_override_entries(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        overrides = config["override_entries"]
        assert len(overrides) == 1
        assert overrides[0]["path"] == "pgvector.secret.create"
        assert overrides[0]["value"] is False

    def test_ignore_differences(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        diffs = config["ignore_differences"]
        assert len(diffs) == 1
        assert diffs[0]["kind"] == "Route"

    def test_extra_value_files_when_overrides(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        assert "/overrides/rag-quickstart.yaml" in config["extra_value_files"]

    def test_vault_prefix(self):
        config = _profile_to_config(
            self._make_profile(), self._make_analysis(),
            [], "/tmp/out", "rag-pattern",
        )
        assert config["vault_prefix"] == "hub"


# ── Integration: transform_remote ──────────────────────────────────


class TestTransformRemote:
    def test_produces_valid_pattern(self, tmp_path):
        qs = tmp_path / "qs"
        chart_dir = qs / "deploy" / "helm" / "myapp"
        write_chart(chart_dir, "myapp", "1.0.0", dependencies=[
            {"name": "pgvector", "version": "0.5.0",
             "repository": "https://rh-ai-quickstart.github.io/ai-architecture-charts"},
        ])
        write_values(chart_dir, {
            "myapp": {"password": "changeme"},
        })

        out = str(tmp_path / "output")
        result = transform_remote(
            str(qs), output_dir=out, pattern_name="test-pattern",
        )
        assert result.success is True
        assert (Path(out) / "values-hub.yaml").exists()
        assert (Path(out) / "charts" / "pattern-secrets").is_dir()
        assert (Path(out) / ".quickpat" / "profile.yaml").exists()

    def test_profile_saved(self, tmp_path):
        qs = tmp_path / "qs"
        chart_dir = qs / "helm" / "myapp"
        write_chart(chart_dir, "myapp", "1.0.0")
        write_values(chart_dir, {"password": "x"})

        out = str(tmp_path / "output")
        transform_remote(str(qs), output_dir=out, pattern_name="test")

        from quickpat.profile import load_profile
        profile = load_profile(out)
        assert profile is not None

    def test_replay_from_profile(self, tmp_path):
        qs = tmp_path / "qs"
        chart_dir = qs / "helm" / "myapp"
        write_chart(chart_dir, "myapp", "1.0.0")
        write_values(chart_dir, {"password": "x"})

        out = str(tmp_path / "output")
        r1 = transform_remote(str(qs), output_dir=out, pattern_name="test")
        assert r1.success is True

        # Second run should replay from profile
        r2 = transform_remote(str(qs), output_dir=out, pattern_name="test")
        assert r2.success is True
        assert any("Profile diff" in d for d in r2.llm_decisions)


# ── Static Drift Entries ─────────────────────────────────────────────


class TestStaticDriftEntries:
    def test_known_route(self):
        entries = _static_drift_entries([("route.openshift.io", "Route")])
        assert len(entries) == 1
        assert entries[0].kind == "Route"
        assert "/spec/host" in entries[0].json_pointers

    def test_known_notebook(self):
        entries = _static_drift_entries([("kubeflow.org", "Notebook")])
        assert len(entries) == 1
        assert "/spec" in entries[0].json_pointers
        assert "/metadata/annotations" in entries[0].json_pointers

    def test_known_dspa(self):
        entries = _static_drift_entries([
            ("datasciencepipelinesapplications.opendatahub.io",
             "DataSciencePipelinesApplication"),
        ])
        assert len(entries) == 1
        assert "/spec" in entries[0].json_pointers

    def test_unknown_type_returns_nothing(self):
        entries = _static_drift_entries([("apps", "Deployment")])
        assert entries == []

    def test_mixed_known_and_unknown(self):
        entries = _static_drift_entries([
            ("route.openshift.io", "Route"),
            ("apps", "Deployment"),
            ("kubeflow.org", "Notebook"),
        ])
        assert len(entries) == 2
        kinds = {e.kind for e in entries}
        assert kinds == {"Route", "Notebook"}

    def test_empty_input(self):
        assert _static_drift_entries([]) == []
