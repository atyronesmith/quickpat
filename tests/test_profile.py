"""Tests for the pattern profile system."""

import pytest
import yaml
from pathlib import Path

from quickpat.profile import (
    PatternProfile,
    SecretDecision,
    ComputedFieldDecision,
    DriftEntry,
    OverrideEntry,
    InfraDecision,
    SourceFingerprint,
    ProfileDiff,
    save_profile,
    load_profile,
    compute_fingerprint,
    diff_profile,
    hash_directory,
    PROFILE_DIR,
    PROFILE_FILE,
)


def _make_full_profile():
    """Build a profile with all decision types populated."""
    return PatternProfile(
        source_repo_url="https://github.com/rh-ai-quickstart/RAG",
        source_chart_path="deploy/helm/rag",
        source_fingerprint=SourceFingerprint(
            chart_yaml_hash="abc123",
            values_yaml_hash="def456",
            subchart_hashes={"pgvector": "aaa", "llm-service": "bbb"},
            operator_versions={"openshift-ai": "3.x"},
            timestamp="2026-05-06T12:00:00+00:00",
        ),
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
                json_pointers=["/spec/host", "/spec/alternateBackends"],
                reason="OpenShift sets host, alternateBackends causes drift",
            ),
            DriftEntry(
                group="kubeflow.org", kind="Notebook",
                json_pointers=["/spec", "/metadata/annotations", "/metadata/labels"],
                reason="RHOAI notebook controller adds annotations/labels",
            ),
        ],
        override_entries=[
            OverrideEntry(
                path="pgvector.secret.create", value=False,
                reason="Secrets managed by pattern-secrets chart",
            ),
            OverrideEntry(
                path="llm-service.secret.enabled", value=False,
                reason="Secrets managed by pattern-secrets chart",
            ),
        ],
        infra_decisions=[
            InfraDecision(
                chart_type="dsc", include=True,
                reason="RHOAI requires DataScienceCluster CRD",
            ),
            InfraDecision(
                chart_type="nfd", include=False,
                reason="CPU-only deployment, no GPU support needed",
            ),
        ],
        vault_prefix="hub",
        secret_target_names={"pgvector": "pgvector", "llm-service": "huggingface-secret"},
    )


# ── Save / Load Tests ───────────────────────────────────────────────


class TestSaveLoad:
    def test_round_trip(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))

        assert loaded is not None
        assert loaded.source_repo_url == profile.source_repo_url
        assert loaded.source_chart_path == profile.source_chart_path
        assert loaded.vault_prefix == "hub"
        assert len(loaded.secret_decisions) == 3
        assert len(loaded.computed_fields) == 1
        assert len(loaded.drift_entries) == 2
        assert len(loaded.override_entries) == 2
        assert len(loaded.infra_decisions) == 2
        assert loaded.secret_target_names == {"pgvector": "pgvector", "llm-service": "huggingface-secret"}

    def test_secret_decisions_preserved(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))

        pw = next(s for s in loaded.secret_decisions if s.name == "password")
        assert pw.group == "pgvector"
        assert pw.classification == "auto-generate"

        user = next(s for s in loaded.secret_decisions if s.name == "user")
        assert user.default_value == "postgres"

    def test_computed_fields_preserved(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))

        cf = loaded.computed_fields[0]
        assert cf.group == "pgvector"
        assert cf.field_name == "jdbc-uri"
        assert "postgresql://" in cf.template
        assert "user" in cf.source_fields

    def test_drift_entries_preserved(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))

        route = next(d for d in loaded.drift_entries if d.kind == "Route")
        assert "/spec/host" in route.json_pointers

    def test_fingerprint_preserved(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))

        assert loaded.source_fingerprint.chart_yaml_hash == "abc123"
        assert loaded.source_fingerprint.subchart_hashes["pgvector"] == "aaa"

    def test_profile_file_is_valid_yaml(self, tmp_path):
        profile = _make_full_profile()
        save_profile(str(tmp_path), profile)

        path = tmp_path / PROFILE_DIR / PROFILE_FILE
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data['profile_version'] == '1.0'
        assert data['source_repo_url'] == "https://github.com/rh-ai-quickstart/RAG"

    def test_creates_profile_dir(self, tmp_path):
        profile = PatternProfile()
        save_profile(str(tmp_path), profile)
        assert (tmp_path / PROFILE_DIR).is_dir()
        assert (tmp_path / PROFILE_DIR / PROFILE_FILE).exists()

    def test_load_nonexistent_returns_none(self, tmp_path):
        assert load_profile(str(tmp_path)) is None

    def test_load_empty_file_returns_none(self, tmp_path):
        profile_dir = tmp_path / PROFILE_DIR
        profile_dir.mkdir()
        (profile_dir / PROFILE_FILE).write_text("")
        assert load_profile(str(tmp_path)) is None

    def test_minimal_profile_round_trip(self, tmp_path):
        profile = PatternProfile(
            source_repo_url="https://example.com/repo",
        )
        save_profile(str(tmp_path), profile)
        loaded = load_profile(str(tmp_path))
        assert loaded.source_repo_url == "https://example.com/repo"
        assert loaded.secret_decisions == []
        assert loaded.drift_entries == []

    def test_overwrite_existing(self, tmp_path):
        p1 = PatternProfile(source_repo_url="old")
        save_profile(str(tmp_path), p1)

        p2 = PatternProfile(source_repo_url="new")
        save_profile(str(tmp_path), p2)

        loaded = load_profile(str(tmp_path))
        assert loaded.source_repo_url == "new"


# ── Fingerprint Tests ────────────────────────────────────────────────


class TestFingerprint:
    def test_deterministic(self, tmp_path):
        chart_dir = tmp_path / "chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text("name: test\nversion: 1.0\n")
        (chart_dir / "values.yaml").write_text("key: value\n")

        fp1 = compute_fingerprint(str(chart_dir))
        fp2 = compute_fingerprint(str(chart_dir))
        assert fp1.chart_yaml_hash == fp2.chart_yaml_hash
        assert fp1.values_yaml_hash == fp2.values_yaml_hash

    def test_different_content_different_hash(self, tmp_path):
        chart1 = tmp_path / "chart1"
        chart1.mkdir()
        (chart1 / "Chart.yaml").write_text("name: a\n")
        (chart1 / "values.yaml").write_text("x: 1\n")

        chart2 = tmp_path / "chart2"
        chart2.mkdir()
        (chart2 / "Chart.yaml").write_text("name: b\n")
        (chart2 / "values.yaml").write_text("x: 2\n")

        fp1 = compute_fingerprint(str(chart1))
        fp2 = compute_fingerprint(str(chart2))
        assert fp1.chart_yaml_hash != fp2.chart_yaml_hash
        assert fp1.values_yaml_hash != fp2.values_yaml_hash

    def test_missing_files_empty_hash(self, tmp_path):
        chart_dir = tmp_path / "empty"
        chart_dir.mkdir()
        fp = compute_fingerprint(str(chart_dir))
        assert fp.chart_yaml_hash == ""
        assert fp.values_yaml_hash == ""

    def test_includes_subchart_hashes(self, tmp_path):
        chart_dir = tmp_path / "chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text("name: test\n")

        class MockSubChartInfo:
            template_hash = "hash123"

        fp = compute_fingerprint(
            str(chart_dir),
            subchart_info={"pgvector": MockSubChartInfo()},
        )
        assert fp.subchart_hashes == {"pgvector": "hash123"}

    def test_has_timestamp(self, tmp_path):
        chart_dir = tmp_path / "chart"
        chart_dir.mkdir()
        (chart_dir / "Chart.yaml").write_text("name: test\n")

        fp = compute_fingerprint(str(chart_dir))
        assert fp.timestamp != ""
        assert "T" in fp.timestamp


class TestHashDirectory:
    def test_deterministic(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "a.yaml").write_text("a: 1\n")
        (d / "b.yaml").write_text("b: 2\n")
        assert hash_directory(d) == hash_directory(d)

    def test_different_content(self, tmp_path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "a.yaml").write_text("a: 1\n")

        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "a.yaml").write_text("a: 2\n")

        assert hash_directory(d1) != hash_directory(d2)

    def test_nonexistent_dir(self, tmp_path):
        assert hash_directory(tmp_path / "nope") == ""


# ── Diff Tests ───────────────────────────────────────────────────────


class TestDiffProfile:
    def test_identical_returns_low(self):
        profile = _make_full_profile()
        fp = profile.source_fingerprint

        diff = diff_profile(profile, fp)
        assert diff.change_level == "low"
        assert "No significant changes" in diff.summary

    def test_chart_yaml_changed_returns_low(self):
        profile = _make_full_profile()
        new_fp = SourceFingerprint(
            chart_yaml_hash="CHANGED",
            values_yaml_hash=profile.source_fingerprint.values_yaml_hash,
            subchart_hashes=profile.source_fingerprint.subchart_hashes,
        )
        diff = diff_profile(profile, new_fp)
        assert diff.change_level == "low"
        assert "version bump" in diff.summary

    def test_new_subchart_returns_medium(self):
        profile = _make_full_profile()
        new_fp = SourceFingerprint(
            chart_yaml_hash=profile.source_fingerprint.chart_yaml_hash,
            values_yaml_hash=profile.source_fingerprint.values_yaml_hash,
            subchart_hashes={
                **profile.source_fingerprint.subchart_hashes,
                "mcp-servers": "ccc",
            },
        )
        diff = diff_profile(profile, new_fp)
        assert diff.change_level == "medium"
        assert "mcp-servers" in diff.new_subcharts

    def test_new_secrets_returns_high(self):
        profile = _make_full_profile()
        fp = profile.source_fingerprint

        new_secret = SecretDecision(
            name="ssl_mode", group="pgvector",
            classification="static-config",
            vault_key="ssl_mode", source_path="pgvector.secret.ssl_mode",
        )
        diff = diff_profile(profile, fp, new_secrets=[
            *profile.secret_decisions, new_secret,
        ])
        assert diff.change_level == "high"
        assert "pgvector.ssl_mode" in diff.new_secrets

    def test_changed_subchart_returns_high(self):
        profile = _make_full_profile()
        new_fp = SourceFingerprint(
            chart_yaml_hash=profile.source_fingerprint.chart_yaml_hash,
            values_yaml_hash=profile.source_fingerprint.values_yaml_hash,
            subchart_hashes={
                "pgvector": "CHANGED",
                "llm-service": "bbb",
            },
        )
        diff = diff_profile(profile, new_fp)
        assert diff.change_level == "high"
        assert "pgvector" in diff.changed_subcharts

    def test_removed_secrets_tracked(self):
        profile = _make_full_profile()
        fp = profile.source_fingerprint

        # Only keep one of the three secrets
        remaining = [profile.secret_decisions[0]]
        diff = diff_profile(profile, fp, new_secrets=remaining)
        assert len(diff.removed_secrets) == 2

    def test_new_resource_types_returns_medium(self):
        profile = _make_full_profile()
        fp = profile.source_fingerprint

        diff = diff_profile(
            profile, fp,
            new_resource_types=[
                ("route.openshift.io", "Route"),
                ("kubeflow.org", "Notebook"),
                ("new.api.group", "NewCRD"),
            ],
        )
        assert diff.change_level == "medium"
        assert "NewCRD" in diff.new_resource_types

    def test_empty_profile_diff(self):
        profile = PatternProfile()
        fp = SourceFingerprint()
        diff = diff_profile(profile, fp)
        assert diff.change_level == "low"
