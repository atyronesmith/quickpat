"""Tests for quickpat.generator."""

import yaml

from quickpat.analyzer import QuickstartAnalyzer
from quickpat.generator import PatternGenerator
from tests.conftest import write_chart, write_values


def _generate(qs_path, tmp_path, **config_overrides):
    """Helper: analyze a quickstart and generate a pattern."""
    analysis = QuickstartAnalyzer(str(qs_path)).analyze()
    out = str(tmp_path / "output")
    config = {
        "pattern_name": "test-pattern",
        "app_name": analysis.name,
        "app_namespace": analysis.name,
        "operators": list(analysis.detected_operators),
        "chart_strategy": "local",
        "use_vault": bool(analysis.detected_secrets),
        "output_dir": out,
        "clustergroup_version": "0.9.*",
    }
    config.update(config_overrides)
    gen = PatternGenerator(analysis, config)
    gen.generate()
    return out, analysis, config


class TestSingleChartGeneration:
    def test_generates_required_files(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        p = Path(out)
        assert (p / "values-global.yaml").exists()
        assert (p / "values-hub.yaml").exists()
        assert (p / "Makefile").exists()
        assert (p / "Makefile-common").exists()
        assert (p / "pattern.sh").exists()
        assert (p / "pattern-metadata.yaml").exists()
        assert (p / "ansible.cfg").exists()

    def test_copies_chart_locally(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        assert (Path(out) / "charts" / "all" / "myapp" / "Chart.yaml").exists()

    def test_values_global_structure(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            # Skip the --- line
            data = yaml.safe_load(f)
        assert "global" in data
        assert "main" in data
        assert data["main"]["multiSourceConfig"]["enabled"] is True

    def test_values_hub_has_application(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "myapp" in apps
        assert apps["myapp"]["path"] == "charts/all/myapp"

    def test_overrides_created(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        overrides = Path(out) / "overrides"
        assert overrides.is_dir()
        for platform in ("AWS", "Azure", "GCP", "IBMCloud", "None"):
            assert (overrides / f"values-{platform}.yaml").exists()

    def test_pattern_sh_executable(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        import stat
        p = Path(out) / "pattern.sh"
        assert p.stat().st_mode & stat.S_IXUSR


class TestMultiChartGeneration:
    def test_creates_all_applications(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "app" in apps
        assert "db" in apps
        assert "ui" in apps

    def test_copies_all_charts(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path)
        from pathlib import Path
        charts = Path(out) / "charts" / "all"
        assert (charts / "app" / "Chart.yaml").exists()
        assert (charts / "db" / "Chart.yaml").exists()
        assert (charts / "ui" / "Chart.yaml").exists()

    def test_oai_labels_only_on_inference_namespace(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        # Find which namespaces have opendatahub labels
        labeled = []
        for ns in namespaces:
            if isinstance(ns, dict):
                for name, conf in ns.items():
                    if isinstance(conf, dict) and "labels" in conf:
                        if "opendatahub.io/dashboard" in conf.get("labels", {}):
                            labeled.append(name)
        assert "app" in labeled
        assert "db" not in labeled
        assert "ui" not in labeled


class TestSecretDedup:
    def test_deduplicates_secret_fields(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "helm"
        write_chart(chart, "test")
        write_values(chart, {
            "svc1": {"secret": {"password": "x"}},
            "svc2": {"secret": {"password": "y"}},
        })
        out, _, _ = _generate(qs, tmp_path / "out")
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        field_names = [f["name"] for f in data["secrets"][0]["fields"]]
        # All names must be unique
        assert len(field_names) == len(set(field_names))

    def test_secret_version_is_2(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        assert data["version"] == "2.0"


class TestVaultDisabled:
    def test_no_secrets_file_without_vault(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        out, _, _ = _generate(qs, tmp_path / "out", use_vault=False)
        from pathlib import Path
        assert not (Path(out) / "values-secret.yaml.template").exists()

    def test_no_vault_apps_without_vault(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        out, _, _ = _generate(qs, tmp_path / "out", use_vault=False)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "vault" not in apps
        assert "golang-external-secrets" not in apps
