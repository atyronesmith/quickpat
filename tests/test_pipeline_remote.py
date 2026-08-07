"""Tests for remote strategy pipeline functions."""

import yaml
from pathlib import Path

from quickpat.analyzer import QuickstartAnalysis, ChartInfo, ChartDependency
from quickpat.pipeline import (
    _default_classify_secrets,
    _llm_classify_secrets,
    _build_new_profile,
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

    def test_service_prefixed_token_gets_vault_secret(self):
        info = SubChartInfo(
            name="llm-service", version="0.5.9",
            secret_fields=["hf_token"],
        )
        decisions = _default_classify_secrets({"llm-service": info})
        assert decisions[0].classification == "vault-secret"

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
                    reason="Secrets managed by secrets chart",
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
        assert (Path(out) / "values-prod.yaml").exists()
        assert any(d.name.endswith("-secrets") for d in (Path(out) / "charts").iterdir() if d.is_dir())
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

    def test_skips_regeneration_when_upstream_unchanged(self, tmp_path):
        qs = tmp_path / 'qs'
        chart_dir = qs / 'deploy' / 'helm' / 'myapp'
        write_chart(chart_dir, 'myapp', '1.0.0', dependencies=[
            {'name': 'pgvector', 'version': '0.5.0',
             'repository': 'https://rh-ai-quickstart.github.io/ai-architecture-charts'},
        ])
        write_values(chart_dir, {'myapp': {'password': 'changeme'}})

        out = str(tmp_path / 'output')
        transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')

        r2 = transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')
        assert r2.success is True
        assert any('skipping regeneration' in w.lower() for w in r2.warnings)

    def test_force_regenerates_when_upstream_unchanged(self, tmp_path):
        qs = tmp_path / 'qs'
        chart_dir = qs / 'deploy' / 'helm' / 'myapp'
        write_chart(chart_dir, 'myapp', '1.0.0', dependencies=[
            {'name': 'pgvector', 'version': '0.5.0',
             'repository': 'https://rh-ai-quickstart.github.io/ai-architecture-charts'},
        ])
        write_values(chart_dir, {'myapp': {'password': 'changeme'}})

        out = str(tmp_path / 'output')
        transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')

        r2 = transform_remote(
            str(qs), output_dir=out, pattern_name='test-pattern', force=True,
        )
        assert r2.success is True
        assert not any('skipping regeneration' in w.lower() for w in r2.warnings)
        assert (Path(out) / 'values-prod.yaml').exists()

    def test_values_yaml_change_does_not_skip(self, tmp_path):
        qs = tmp_path / 'qs'
        chart_dir = qs / 'deploy' / 'helm' / 'myapp'
        write_chart(chart_dir, 'myapp', '1.0.0')
        write_values(chart_dir, {'myapp': {'password': 'changeme'}})

        out = str(tmp_path / 'output')
        transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')

        write_values(chart_dir, {'myapp': {'password': 'changed', 'replicas': 2}})
        r2 = transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')
        assert r2.success is True
        assert not any('skipping regeneration' in w.lower() for w in r2.warnings)
        assert any('values.yaml' in d for d in r2.llm_decisions)

    def test_values_yaml_change_then_unchanged_skips(self, tmp_path):
        """After regenerating for a values change, fingerprint updates so next run skips."""
        qs = tmp_path / 'qs'
        chart_dir = qs / 'deploy' / 'helm' / 'myapp'
        write_chart(chart_dir, 'myapp', '1.0.0')
        write_values(chart_dir, {'myapp': {'password': 'v1'}})

        out = str(tmp_path / 'output')
        transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')

        write_values(chart_dir, {'myapp': {'password': 'v2'}})
        r2 = transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')
        assert not any('skipping regeneration' in w.lower() for w in r2.warnings)

        r3 = transform_remote(str(qs), output_dir=out, pattern_name='test-pattern')
        assert any('skipping regeneration' in w.lower() for w in r3.warnings)


# ── Static Drift Entries ─────────────────────────────────────────────


class TestStaticDriftEntries:
    """KNOWN_IGNORE_RULES is empty by design — ignoreDifferences should be
    explicitly provided via spec YAML or --ignore-differences, not auto-generated."""

    def test_no_auto_drift_for_routes(self):
        entries = _static_drift_entries([("route.openshift.io", "Route")])
        assert entries == []

    def test_no_auto_drift_for_notebooks(self):
        entries = _static_drift_entries([("kubeflow.org", "Notebook")])
        assert entries == []

    def test_no_auto_drift_for_any_type(self):
        entries = _static_drift_entries([
            ("route.openshift.io", "Route"),
            ("apps", "Deployment"),
            ("kubeflow.org", "Notebook"),
        ])
        assert entries == []

    def test_empty_input(self):
        assert _static_drift_entries([]) == []


# ── Validation success semantics ─────────────────────────────────────


class TestPipelineValidationSuccess:
    """Pipeline success reflects post-generation validation."""

    def test_transform_success_false_when_validation_fails(
        self, single_chart_quickstart, tmp_path, monkeypatch,
    ):
        from quickpat.pipeline import transform
        from quickpat.validator import ValidationResult, Issue

        def fake_validate_and_fix(*args, **kwargs):
            return ValidationResult(valid=False, issues=[
                Issue('values-global.yaml', 'error', 'forced validation failure'),
            ])

        monkeypatch.setattr(
            'quickpat.pipeline.validate_and_fix', fake_validate_and_fix,
        )

        result = transform(
            str(single_chart_quickstart),
            output_dir=str(tmp_path / 'out'),
            chart_strategy='local',
        )
        assert result.validation is not None
        assert not result.validation.valid
        assert not result.success

    def test_transform_remote_success_false_when_validation_fails(
        self, tmp_path, monkeypatch,
    ):
        from quickpat.validator import ValidationResult, Issue

        qs = tmp_path / 'qs'
        chart_dir = qs / 'deploy' / 'helm' / 'myapp'
        write_chart(chart_dir, 'myapp', '1.0.0')
        write_values(chart_dir, {'myapp': {'password': 'changeme'}})

        def fake_validate_and_fix(*args, **kwargs):
            return ValidationResult(valid=False, issues=[
                Issue('values-global.yaml', 'error', 'forced validation failure'),
            ])

        monkeypatch.setattr(
            'quickpat.pipeline.validate_and_fix', fake_validate_and_fix,
        )

        result = transform_remote(
            str(qs), output_dir=str(tmp_path / 'out'), pattern_name='test-pattern',
        )
        assert result.validation is not None
        assert not result.validation.valid
        assert not result.success


# ── LLM classify fallback warning ────────────────────────────────────


class TestLLMClassifySecretsWarning:
    """_llm_classify_secrets should surface a warning when the LLM call fails."""

    def _make_analysis(self):
        return QuickstartAnalysis(name="myapp", version="1.0.0")

    def _make_subchart_info(self):
        return {
            "pgvector": SubChartInfo(
                name="pgvector", version="0.5.5",
                secret_fields=["password", "host"],
            ),
        }

    def test_returns_warning_on_llm_exception(self):
        class _FailingProvider:
            def complete(self, system, prompt, **kw):
                raise RuntimeError("API unavailable")

        decisions, warning = _llm_classify_secrets(
            _FailingProvider(), self._make_analysis(), self._make_subchart_info(),
        )
        assert len(decisions) == 2
        assert warning is not None
        assert "LLM secret classification failed" in warning
        assert "API unavailable" in warning

    def test_returns_no_warning_on_success(self):
        class _SuccessProvider:
            def complete(self, system, prompt, **kw):
                from types import SimpleNamespace
                return SimpleNamespace(
                    parsed={
                        "secrets": [
                            {"name": "password", "group": "pgvector",
                             "classification": "auto-generate"},
                            {"name": "host", "group": "pgvector",
                             "classification": "static-config",
                             "default_value": "localhost"},
                        ],
                    },
                    content="",
                )

        decisions, warning = _llm_classify_secrets(
            _SuccessProvider(), self._make_analysis(), self._make_subchart_info(),
        )
        assert warning is None
        assert len(decisions) == 2

    def test_build_new_profile_surfaces_warning(self):
        class _FailingProvider:
            def complete(self, system, prompt, **kw):
                raise RuntimeError("timeout")

        from quickpat.pipeline import TransformResult
        result = TransformResult(success=False)
        profile = _build_new_profile(
            self._make_analysis(), self._make_subchart_info(),
            _FailingProvider(), result, "", "",
        )
        assert any("LLM secret classification failed" in w for w in result.warnings)
        assert len(profile.secret_decisions) == 2
