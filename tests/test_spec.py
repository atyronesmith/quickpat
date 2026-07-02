"""Tests for quickpat.spec and quickpat new end-to-end."""

import pytest
import yaml
from pathlib import Path

from quickpat.spec import load_spec, validate_spec, build_from_spec, SpecError
from quickpat.pipeline import create_from_spec


def _write_spec(path, spec_dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(spec_dict))
    return str(path)


def _make_local_chart(tmp_path, name):
    chart_dir = tmp_path / "charts" / name
    chart_dir.mkdir(parents=True)
    (chart_dir / "Chart.yaml").write_text(yaml.dump({
        "apiVersion": "v2", "name": name, "version": "1.0.0",
        "description": f"Test {name} chart",
    }))
    return str(chart_dir)


class TestLoadSpec:
    def test_loads_valid_yaml(self, tmp_path):
        spec_file = _write_spec(tmp_path / "spec.yaml", {"name": "test"})
        data = load_spec(spec_file)
        assert data["name"] == "test"

    def test_missing_file_raises(self):
        with pytest.raises(SpecError, match="not found"):
            load_spec("/nonexistent/spec.yaml")

    def test_non_mapping_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("- just a list")
        with pytest.raises(SpecError, match="must be a YAML mapping"):
            load_spec(str(p))


class TestValidateSpec:
    def test_valid_minimal(self):
        errors = validate_spec({"name": "test"})
        assert errors == []

    def test_missing_name(self):
        errors = validate_spec({})
        assert any("name" in e for e in errors)

    def test_invalid_tier(self):
        errors = validate_spec({"name": "t", "tier": "gold"})
        assert any("tier" in e for e in errors)

    def test_valid_tiers(self):
        for tier in ("sandbox", "tested", "maintained"):
            errors = validate_spec({"name": "t", "tier": tier})
            assert not any("tier" in e for e in errors)

    def test_chart_without_path_or_repo(self):
        errors = validate_spec({"name": "t", "charts": [{"name": "x"}]})
        assert any("path" in e or "repo" in e for e in errors)

    def test_chart_with_path(self):
        errors = validate_spec({"name": "t", "charts": [{"name": "x", "path": "./c"}]})
        assert not any("path" in e for e in errors)

    def test_chart_with_repo(self):
        errors = validate_spec({"name": "t", "charts": [{"name": "x", "repo": "https://r"}]})
        assert not any("repo" in e for e in errors)

    def test_unknown_operator(self):
        errors = validate_spec({"name": "t", "operators": ["fake-op"]})
        assert any("fake-op" in e for e in errors)

    def test_valid_operators(self):
        errors = validate_spec({"name": "t", "operators": ["openshift-ai"]})
        assert not any("operator" in e.lower() for e in errors)

    def test_empty_charts_list(self):
        errors = validate_spec({"name": "t", "charts": []})
        assert any("non-empty" in e for e in errors)

    def test_secret_invalid_action(self):
        errors = validate_spec({
            "name": "t",
            "secrets": [{"name": "s", "onMissingValue": "delete"}],
        })
        assert any("onMissingValue" in e for e in errors)

    def test_ignore_differences_valid(self):
        errors = validate_spec({
            "name": "t",
            "ignoreDifferences": [
                {"group": "route.openshift.io", "kind": "Route",
                 "jsonPointers": ["/spec/host"]},
            ],
        })
        assert len(errors) == 0

    def test_ignore_differences_missing_kind(self):
        errors = validate_spec({
            "name": "t",
            "ignoreDifferences": [{"jsonPointers": ["/spec/host"]}],
        })
        assert any("missing 'kind'" in e for e in errors)

    def test_ignore_differences_missing_pointers(self):
        errors = validate_spec({
            "name": "t",
            "ignoreDifferences": [{"kind": "Route"}],
        })
        assert any("missing 'jsonPointers'" in e for e in errors)


class TestBuildFromSpec:
    def test_minimal_spec(self, tmp_path):
        chart_dir = _make_local_chart(tmp_path, "myapp")
        spec = {
            "name": "test-pattern",
            "charts": [{"name": "myapp", "path": chart_dir}],
        }
        analysis, config = build_from_spec(spec)
        assert analysis.name == "test-pattern"
        assert len(analysis.charts) == 1
        assert analysis.charts[0].name == "myapp"
        assert analysis.charts[0].strategy == "local"
        assert config["pattern_name"] == "test-pattern"
        assert config["tier"] == "sandbox"

    def test_external_chart(self):
        spec = {
            "name": "ext",
            "charts": [{"name": "remote", "repo": "https://charts.example.com", "version": "2.0.0"}],
        }
        analysis, config = build_from_spec(spec)
        ci = analysis.charts[0]
        assert ci.strategy == "external"
        assert ci.repo_url == "https://charts.example.com"
        assert ci.version == "2.0.0"

    def test_operators_resolved_with_co_deps(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
            "operators": ["openshift-ai"],
        }
        analysis, config = build_from_spec(spec)
        assert "openshift-ai" in analysis.detected_operators
        assert "servicemesh" in analysis.detected_operators
        assert "serverless" in analysis.detected_operators

    def test_secrets_and_config(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
            "secrets": [
                {"name": "hf_token", "onMissingValue": "prompt"},
                {"name": "db_pw", "onMissingValue": "generate"},
            ],
        }
        analysis, config = build_from_spec(spec)
        assert len(analysis.detected_secrets) == 2
        assert config["secret_config"] == {"db_pw": "generate"}

    def test_tier_passed_to_config(self):
        spec = {
            "name": "t",
            "tier": "tested",
            "charts": [{"name": "x", "repo": "https://r"}],
        }
        _, config = build_from_spec(spec)
        assert config["tier"] == "tested"

    def test_vault_config(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
            "vault": {"enabled": True},
        }
        _, config = build_from_spec(spec)
        assert config["use_vault"] is True

    def test_global_options(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
            "options": {"syncPolicy": "Manual", "installPlanApproval": "Manual"},
        }
        _, config = build_from_spec(spec)
        assert config["global_options"]["syncPolicy"] == "Manual"
        assert config["global_options"]["installPlanApproval"] == "Manual"

    def test_namespace_from_chart_entry(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r", "namespace": "custom-ns"}],
        }
        analysis, _ = build_from_spec(spec)
        assert analysis.charts[0].group == "custom-ns"

    def test_ignore_differences_in_config(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
            "ignoreDifferences": [
                {"group": "route.openshift.io", "kind": "Route",
                 "jsonPointers": ["/spec/host"]},
            ],
        }
        _, config = build_from_spec(spec)
        assert len(config["ignore_differences"]) == 1
        assert config["ignore_differences"][0]["kind"] == "Route"

    def test_no_ignore_differences_by_default(self):
        spec = {
            "name": "t",
            "charts": [{"name": "x", "repo": "https://r"}],
        }
        _, config = build_from_spec(spec)
        assert "ignore_differences" not in config

    def test_invalid_spec_raises(self):
        with pytest.raises(SpecError, match="Invalid spec"):
            build_from_spec({})

    def test_reads_chart_yaml_version(self, tmp_path):
        chart_dir = _make_local_chart(tmp_path, "versioned")
        spec = {
            "name": "t",
            "charts": [{"name": "versioned", "path": chart_dir}],
        }
        analysis, _ = build_from_spec(spec)
        assert analysis.charts[0].version == "1.0.0"
        assert analysis.charts[0].description == "Test versioned chart"


class TestCreateFromSpec:
    def test_end_to_end_local_chart(self, tmp_path):
        chart_dir = _make_local_chart(tmp_path, "myapp")
        spec_file = _write_spec(tmp_path / "spec.yaml", {
            "name": "e2e-test",
            "tier": "tested",
            "charts": [{"name": "myapp", "path": chart_dir}],
            "operators": ["nvidia-gpu"],
            "secrets": [{"name": "token", "onMissingValue": "prompt"}],
            "vault": {"enabled": True},
        })
        out = str(tmp_path / "output")
        result = create_from_spec(spec_file, output_dir=out)

        assert result.success
        p = Path(out)
        assert (p / "values-global.yaml").exists()
        assert (p / "values-prod.yaml").exists()
        assert (p / "pattern-metadata.yaml").exists()
        assert (p / "values-secret.yaml.template").exists()
        assert (p / "charts" / "myapp" / "Chart.yaml").exists()

        # Check tier in metadata
        with open(p / "pattern-metadata.yaml") as f:
            meta = yaml.safe_load(f)
        assert meta["tier"] == "tested"

        # Check operator subscription
        with open(p / "values-prod.yaml") as f:
            hub = yaml.safe_load(f)
        subs = hub["clusterGroup"]["subscriptions"]
        assert "nvidia" in subs

    def test_end_to_end_external_chart(self, tmp_path):
        spec_file = _write_spec(tmp_path / "spec.yaml", {
            "name": "ext-test",
            "charts": [{
                "name": "remote-app",
                "repo": "https://charts.example.com",
                "version": "3.0.0",
                "namespace": "app-ns",
            }],
        })
        out = str(tmp_path / "output")
        result = create_from_spec(spec_file, output_dir=out)

        assert result.success
        p = Path(out)
        with open(p / "values-prod.yaml") as f:
            hub = yaml.safe_load(f)
        apps = hub["clusterGroup"]["applications"]
        assert "remote-app" in apps
        assert apps["remote-app"]["repoURL"] == "https://charts.example.com"
        assert apps["remote-app"]["namespace"] == "app-ns"
        # No local chart copy
        assert not (p / "charts" / "remote-app").exists()

    def test_mixed_strategies(self, tmp_path):
        chart_dir = _make_local_chart(tmp_path, "local-svc")
        spec_file = _write_spec(tmp_path / "spec.yaml", {
            "name": "mixed-test",
            "charts": [
                {"name": "local-svc", "path": chart_dir},
                {"name": "ext-svc", "repo": "https://r.example.com", "version": "1.0.0"},
            ],
        })
        out = str(tmp_path / "output")
        result = create_from_spec(spec_file, output_dir=out)

        assert result.success
        p = Path(out)
        assert (p / "charts" / "local-svc" / "Chart.yaml").exists()
        assert not (p / "charts" / "ext-svc").exists()

        with open(p / "values-prod.yaml") as f:
            hub = yaml.safe_load(f)
        apps = hub["clusterGroup"]["applications"]
        assert "path" in apps["local-svc"]
        assert "repoURL" in apps["ext-svc"]

    def test_invalid_spec_returns_failure(self, tmp_path):
        spec_file = _write_spec(tmp_path / "bad.yaml", {"tier": "gold"})
        result = create_from_spec(spec_file)
        assert not result.success
        assert any("name" in w for w in result.warnings)

    def test_pattern_name_override(self, tmp_path):
        chart_dir = _make_local_chart(tmp_path, "app")
        spec_file = _write_spec(tmp_path / "spec.yaml", {
            "name": "original",
            "charts": [{"name": "app", "path": chart_dir}],
        })
        out = str(tmp_path / "output")
        result = create_from_spec(spec_file, output_dir=out, pattern_name="override-name")
        assert result.success
        assert result.config["pattern_name"] == "override-name"
