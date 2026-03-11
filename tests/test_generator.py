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


class TestGroupedNamespaces:
    def test_grouped_charts_share_namespace(self, grouped_chart_quickstart, tmp_path):
        out, _, _ = _generate(grouped_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        # collector and tempo share "observability" namespace
        assert apps["collector"]["namespace"] == "observability"
        assert apps["tempo"]["namespace"] == "observability"
        # model is in "inference"
        assert apps["model"]["namespace"] == "inference"
        # ui is flat, uses its own name
        assert apps["ui"]["namespace"] == "ui"

    def test_grouped_namespace_appears_once(self, grouped_chart_quickstart, tmp_path):
        out, _, _ = _generate(grouped_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        # Extract namespace names (could be string or dict key)
        ns_names = []
        for ns in namespaces:
            if isinstance(ns, str):
                ns_names.append(ns)
            elif isinstance(ns, dict):
                ns_names.extend(ns.keys())
        # "observability" should appear exactly once
        assert ns_names.count("observability") == 1

    def test_oai_labels_on_grouped_namespace(self, grouped_chart_quickstart, tmp_path):
        """If any chart in a group needs OAI labels, the namespace gets them."""
        out, _, _ = _generate(grouped_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        # The "inference" namespace should have OAI labels (model has llm-service dep)
        for ns in namespaces:
            if isinstance(ns, dict) and "inference" in ns:
                assert "opendatahub.io/dashboard" in ns["inference"]["labels"]
                break
        else:
            pytest.fail("inference namespace not found with OAI labels")


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


class TestNewConfigKeys:
    def test_tier_in_metadata(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, tier="tested")
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["tier"] == "tested"

    def test_tier_defaults_to_sandbox(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["tier"] == "sandbox"

    def test_global_options_sync_policy(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path,
                              global_options={"syncPolicy": "Manual"})
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["global"]["options"]["syncPolicy"] == "Manual"

    def test_global_options_install_plan(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path,
                              global_options={"installPlanApproval": "Manual"})
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["global"]["options"]["installPlanApproval"] == "Manual"

    def test_global_options_default_automatic(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["global"]["options"]["syncPolicy"] == "Automatic"
        assert data["global"]["options"]["installPlanApproval"] == "Automatic"

    def test_secret_config_skip(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path,
                              secret_config={"password": "skip"})
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        field_names = [f["name"] for f in data["secrets"][0]["fields"]]
        assert "password" not in field_names

    def test_secret_config_generate(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path,
                              secret_config={"password": "generate"})
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        fields = data["secrets"][0]["fields"]
        pw = [f for f in fields if f["name"] == "password"][0]
        assert pw["onMissingValue"] == "generate"
        assert pw["vaultPolicy"] == "validatedPatternDefaultPolicy"

    def test_namespace_overrides(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path,
                              namespace_overrides={"db": "data-tier"})
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert apps["db"]["namespace"] == "data-tier"
        assert apps["app"]["namespace"] == "app"  # unchanged

    def test_per_chart_strategy(self, tmp_path):
        from quickpat.analyzer import QuickstartAnalysis, ChartInfo
        chart_src = tmp_path / "src" / "local-app"
        chart_src.mkdir(parents=True)
        (chart_src / "Chart.yaml").write_text("name: local-app\n")
        analysis = QuickstartAnalysis(
            name="mixed", version="1.0.0", description="test",
            charts=[
                ChartInfo(name="local-app", chart_path=str(chart_src), strategy="local"),
                ChartInfo(name="ext-app", version="2.0.0", strategy="external",
                          repo_url="https://charts.example.com"),
            ],
        )
        out = str(tmp_path / "output")
        config = {
            "pattern_name": "test", "app_name": "mixed", "app_namespace": "mixed",
            "operators": [], "chart_strategy": "external", "use_vault": False,
            "output_dir": out, "clustergroup_version": "0.9.*",
        }
        from quickpat.generator import PatternGenerator
        PatternGenerator(analysis, config).generate()
        from pathlib import Path
        with open(Path(out) / "values-hub.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert apps["local-app"]["path"] == "charts/all/local-app"
        assert "repoURL" not in apps["local-app"]
        assert apps["ext-app"]["repoURL"] == "https://charts.example.com"
        assert "path" not in apps["ext-app"]


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
