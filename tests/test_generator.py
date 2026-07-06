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
        assert (p / "values-prod.yaml").exists()
        assert (p / "Makefile").exists()
        assert (p / "Makefile-common").exists()
        assert (p / "pattern.sh").exists()
        assert (p / "pattern-metadata.yaml").exists()
        assert (p / "ansible.cfg").exists()

    def test_copies_chart_locally(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        assert (Path(out) / "charts" / "myapp" / "Chart.yaml").exists()

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
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "myapp" in apps
        assert apps["myapp"]["path"] == "charts/myapp"

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
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "app" in apps
        assert "db" in apps
        assert "ui" in apps

    def test_copies_all_charts(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path)
        from pathlib import Path
        charts = Path(out) / "charts"
        assert (charts / "app" / "Chart.yaml").exists()
        assert (charts / "db" / "Chart.yaml").exists()
        assert (charts / "ui" / "Chart.yaml").exists()

    def test_oai_labels_only_on_inference_namespace(self, multi_chart_quickstart, tmp_path):
        out, _, _ = _generate(multi_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        assert isinstance(namespaces, dict)
        labeled = [
            name for name, conf in namespaces.items()
            if isinstance(conf, dict) and "opendatahub.io/dashboard" in conf.get("labels", {})
        ]
        assert "app" in labeled
        assert "db" not in labeled
        assert "ui" not in labeled


class TestGroupedNamespaces:
    def test_grouped_charts_share_namespace(self, grouped_chart_quickstart, tmp_path):
        out, _, _ = _generate(grouped_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
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
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        assert isinstance(namespaces, dict)
        assert "observability" in namespaces

    def test_oai_labels_on_grouped_namespace(self, grouped_chart_quickstart, tmp_path):
        """If any chart in a group needs OAI labels, the namespace gets them."""
        out, _, _ = _generate(grouped_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        namespaces = data["clusterGroup"]["namespaces"]
        assert isinstance(namespaces, dict)
        assert "inference" in namespaces
        assert "opendatahub.io/dashboard" in namespaces["inference"]["labels"]


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

    def test_metadata_has_provenance(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["metadata_version"] == "2.0"
        qp = data["quickpat"]
        assert "version" in qp
        assert "generated" in qp
        assert "strategy" in qp
        assert isinstance(qp["vault"], bool)

    def test_metadata_provenance_with_remote(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        qp = data["quickpat"]
        assert qp["strategy"] == "remote"
        assert qp["vault"] is True
        assert qp["source"]["repo"] == "https://github.com/rh-ai-quickstart/RAG"
        assert qp["source"]["branch"] == "main"
        assert "secret_groups" in qp
        assert "pgvector" in qp["secret_groups"]

    def test_metadata_provenance_includes_operators(self, tmp_path):
        out, _, _ = _remote_config(tmp_path, operators=["openshift-ai", "nvidia-gpu"])
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert "nvidia-gpu" in data["quickpat"]["operators"]
        assert "openshift-ai" in data["quickpat"]["operators"]

    def test_metadata_provenance_records_ignore_differences(self, tmp_path):
        ignore = [{"group": "route.openshift.io", "kind": "Route",
                   "jsonPointers": ["/spec/host"]}]
        out, _, _ = _remote_config(tmp_path, ignore_differences=ignore)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["quickpat"]["ignore_differences"] == ignore

    def test_global_single_argocd(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["global"]["singleArgoCD"] is True
        assert data["global"]["secretLoader"]["disabled"] is False
        assert "options" not in data["global"]

    def test_configurable_cluster_group_name(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path,
                              cluster_group_name="factory")
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["main"]["clusterGroupName"] == "factory"
        assert (Path(out) / "values-factory.yaml").exists()
        assert not (Path(out) / "values-prod.yaml").exists()

    def test_shared_value_files_in_values_prod(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        svf = data["clusterGroup"]["sharedValueFiles"]
        assert svf == ["/overrides/values-{{ $.Values.global.clusterPlatform }}.yaml"]

    def test_provenance_records_configured_strategy(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["quickpat"]["strategy"] == "local"

    def test_provenance_strategy_default_is_remote(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "pattern-metadata.yaml") as f:
            data = yaml.safe_load(f)
        assert data["quickpat"]["strategy"] == "remote"

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
        with open(Path(out) / "values-prod.yaml") as f:
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
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert apps["local-app"]["path"] == "charts/local-app"
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
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "vault" not in apps
        assert "openshift-external-secrets" not in apps


class TestTransformComputedTemplate:
    def test_strips_values_secret_prefix(self):
        t = PatternGenerator._transform_computed_template(
            '{{ printf "jdbc:postgresql://%s:%s/%s" .Values.secret.host .Values.secret.port .Values.secret.dbname }}',
            ["host", "port", "dbname"],
        )
        assert ".Values." not in t
        assert "{{ .host }}" in t
        assert "{{ .port }}" in t
        assert "{{ .dbname }}" in t

    def test_strips_b64enc_and_quote(self):
        t = PatternGenerator._transform_computed_template(
            '{{ printf "%s:%s" .Values.secret.user .Values.secret.password | b64enc | quote }}',
            ["user", "password"],
        )
        assert "b64enc" not in t
        assert "quote" not in t
        assert "{{ .user }}" in t

    def test_strips_include_calls(self):
        t = PatternGenerator._transform_computed_template(
            '{{ printf "jdbc:postgresql://%s.%s:%s/%s" .Values.secret.host (include "pgvector.namespace" .) .Values.secret.port .Values.secret.dbname }}',
            ["host", "port", "dbname"],
        )
        assert "include" not in t
        assert "{{ .host }}" in t
        assert "{{ .port }}" in t
        assert "{{ .dbname }}" in t
        assert "%s" not in t

    def test_strips_values_without_secret(self):
        t = PatternGenerator._transform_computed_template(
            '{{ printf "http://%s:%s" .Values.minio.host .Values.minio.port }}',
            ["host", "port"],
        )
        assert ".Values." not in t

    def test_passthrough_clean_template(self):
        t = PatternGenerator._transform_computed_template(
            'jdbc:postgresql://{{ .host }}:{{ .port }}/{{ .dbname }}',
            ["host", "port", "dbname"],
        )
        assert t == 'jdbc:postgresql://{{ .host }}:{{ .port }}/{{ .dbname }}'

    def test_eso_escape(self):
        assert PatternGenerator._eso_escape('{{ .user }}') == '{{ `{{ .user }}` }}'
        assert PatternGenerator._eso_escape(
            'jdbc:postgresql://{{ .host }}:{{ .port }}/{{ .dbname }}',
        ) == 'jdbc:postgresql://{{ `{{ .host }}` }}:{{ `{{ .port }}` }}/{{ `{{ .dbname }}` }}'
        assert PatternGenerator._eso_escape('literal') == 'literal'


def _remote_config(tmp_path, **overrides):
    """Build a config dict for remote strategy generation."""
    from quickpat.analyzer import QuickstartAnalysis, ChartInfo
    analysis = QuickstartAnalysis(
        name="rag-quickstart", version="1.0.0",
        description="RAG quickstart",
        charts=[ChartInfo(name="rag-quickstart", strategy="remote")],
    )
    out = str(tmp_path / "output")
    config = {
        "pattern_name": "rag-pattern",
        "app_name": "rag-quickstart",
        "app_namespace": "rag",
        "operators": [],
        "chart_strategy": "remote",
        "use_vault": True,
        "output_dir": out,
        "clustergroup_version": "0.9.*",
        "git_repo_url": "https://github.com/rh-ai-quickstart/RAG",
        "chart_path_in_repo": "deploy/helm/rag",
        "chart_branch": "main",
        "vault_prefix": "hub",
        "secret_target_names": {
            "pgvector": "pgvector",
            "llm-service": "huggingface-secret",
        },
        "secret_groups": {
            "pgvector": [
                {"name": "user", "classification": "static-config", "default_value": "postgres"},
                {"name": "password", "classification": "auto-generate"},
                {"name": "host", "classification": "static-config", "default_value": "pgvector"},
                {"name": "port", "classification": "static-config", "default_value": "5432"},
                {"name": "dbname", "classification": "static-config", "default_value": "rag_blueprint"},
                {"name": "jdbc-uri", "computed": True,
                 "template": "jdbc:postgresql://{{ .host }}:{{ .port }}/{{ .dbname }}"},
            ],
            "llm-service": [
                {"name": "hf_token", "classification": "vault-secret"},
            ],
        },
        "override_entries": [
            {"path": "pgvector.secret.create", "value": False},
            {"path": "llm-service.secret.enabled", "value": False},
        ],
        "extra_value_files": [
            '/overrides/rag-quickstart.yaml',
        ],
        "ignore_differences": [
            {"group": "route.openshift.io", "kind": "Route",
             "jsonPointers": ["/spec/host", "/spec/alternateBackends"]},
        ],
    }
    config.update(overrides)
    gen = PatternGenerator(analysis, config)
    gen.generate()
    return out, analysis, config


class TestRemoteStrategyGeneration:
    def test_app_has_repo_url_and_path(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        app = data["clusterGroup"]["applications"]["rag-quickstart"]
        assert app["repoURL"] == "https://github.com/rh-ai-quickstart/RAG"
        assert app["path"] == "deploy/helm/rag"
        assert app["chartVersion"] == "main"
        assert "chart" not in app

    def test_app_has_extra_value_files(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        app = data["clusterGroup"]["applications"]["rag-quickstart"]
        assert app["extraValueFiles"] == ["/overrides/rag-quickstart.yaml"]

    def test_app_has_ignore_differences(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        app = data["clusterGroup"]["applications"]["rag-quickstart"]
        assert len(app["ignoreDifferences"]) == 1
        assert app["ignoreDifferences"][0]["kind"] == "Route"

    def test_secrets_app_created(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        apps = data["clusterGroup"]["applications"]
        assert "rag-quickstart-secrets" in apps
        assert apps["rag-quickstart-secrets"]["path"] == "charts/rag-quickstart-secrets"
        assert apps["rag-quickstart-secrets"]["namespace"] == "rag"

    def test_secrets_chart_exists(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        chart_dir = Path(out) / "charts" / "rag-quickstart-secrets"
        assert (chart_dir / "Chart.yaml").exists()
        with open(chart_dir / "Chart.yaml") as f:
            data = yaml.safe_load(f)
        assert data["name"] == "rag-quickstart-secrets"

    def test_external_secret_crds_created(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        tmpl_dir = Path(out) / "charts" / "rag-quickstart-secrets" / "templates"
        assert (tmpl_dir / "pgvector-secret.yaml").exists()
        assert (tmpl_dir / "llm-service-secret.yaml").exists()

    def test_external_secret_uses_v1(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        assert data["apiVersion"] == "external-secrets.io/v1"
        assert data["kind"] == "ExternalSecret"

    def test_external_secret_target_name_matches_subchart(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["name"] == "pgvector"
        assert data["spec"]["target"]["name"] == "pgvector"

        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "llm-service-secret.yaml") as f:
            data = yaml.safe_load(f)
        assert data["metadata"]["name"] == "huggingface-secret"
        assert data["spec"]["target"]["name"] == "huggingface-secret"

    def test_external_secret_per_key_data(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        spec_data = data["spec"]["data"]
        keys = [d["secretKey"] for d in spec_data]
        assert "user" in keys
        assert "password" in keys
        assert "host" in keys
        for d in spec_data:
            assert d["remoteRef"]["key"] == "secret/data/hub/pgvector"

    def test_external_secret_computed_field_in_template(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        tmpl_data = data["spec"]["target"]["template"]["data"]
        assert "jdbc-uri" in tmpl_data
        assert "postgresql://" in tmpl_data["jdbc-uri"]
        # Non-computed fields use backtick-escaped template reference
        assert tmpl_data["user"] == "{{ `{{ .user }}` }}"

    def test_override_file_created(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        override_path = Path(out) / "overrides" / "rag-quickstart.yaml"
        assert override_path.exists()
        with open(override_path) as f:
            data = yaml.safe_load(f)
        assert data["pgvector"]["secret"]["create"] is False
        assert data["llm-service"]["secret"]["enabled"] is False

    def test_grouped_secret_template(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        assert data["version"] == "2.0"
        secrets = data["secrets"]
        names = [s["name"] for s in secrets]
        assert "pgvector" in names
        assert "llm-service" in names

    def test_grouped_secret_vault_prefix_hub(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        for s in data["secrets"]:
            assert s["vaultPrefixes"] == ["hub"]

    def test_grouped_secret_classifications(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        pg = next(s for s in data["secrets"] if s["name"] == "pgvector")
        field_map = {f["name"]: f for f in pg["fields"]}
        assert field_map["user"]["value"] == "postgres"
        assert field_map["password"]["onMissingValue"] == "generate"
        assert "vaultPolicy" in field_map["password"]
        # Computed fields excluded from values-secret
        assert "jdbc-uri" not in field_map

    def test_grouped_secret_prompt_classification(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-secret.yaml.template") as f:
            data = yaml.safe_load(f)
        llm = next(s for s in data["secrets"] if s["name"] == "llm-service")
        assert llm["fields"][0]["name"] == "hf_token"
        assert llm["fields"][0]["onMissingValue"] == "prompt"

    def test_no_local_chart_copy_for_remote(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        charts = Path(out) / "charts"
        for d in charts.iterdir():
            assert d.name.endswith("-secrets"), f"unexpected chart dir: {d.name}"

    def test_no_secrets_chart_without_vault(self, tmp_path):
        out, _, _ = _remote_config(tmp_path, use_vault=False)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        assert "rag-quickstart-secrets" not in data["clusterGroup"]["applications"]
        assert not (Path(out) / "charts" / "rag-quickstart-secrets").exists()

    def test_no_secrets_chart_with_empty_secret_groups(self, tmp_path):
        out, _, _ = _remote_config(tmp_path, secret_groups={})
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        assert "rag-quickstart-secrets" not in data["clusterGroup"]["applications"]
        assert not (Path(out) / "charts" / "rag-quickstart-secrets").exists()
        assert (Path(out) / "values-secret.yaml.template").exists()

    def test_computed_fields_not_in_spec_data(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        keys = [d["secretKey"] for d in data["spec"]["data"]]
        assert "jdbc-uri" not in keys

    def test_no_duplicate_data_entries(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        keys = [d["secretKey"] for d in data["spec"]["data"]]
        assert len(keys) == len(set(keys))

    def test_computed_template_no_helm_syntax(self, tmp_path):
        config_overrides = {
            "secret_groups": {
                "pgvector": [
                    {"name": "host", "classification": "static-config", "default_value": "pgvector"},
                    {"name": "port", "classification": "static-config", "default_value": "5432"},
                    {"name": "jdbc-uri", "computed": True,
                     "template": '{{ printf "jdbc:postgresql://%s:%s/db" .Values.secret.host .Values.secret.port | b64enc | quote }}',
                     "source_fields": ["host", "port"]},
                ],
            },
        }
        out, _, _ = _remote_config(tmp_path, **config_overrides)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "templates" / "pgvector-secret.yaml") as f:
            data = yaml.safe_load(f)
        tmpl = data["spec"]["target"]["template"]["data"]["jdbc-uri"]
        assert ".Values." not in tmpl
        assert "b64enc" not in tmpl
        assert "{{ `{{ .host }}` }}" in tmpl
        assert "{{ `{{ .port }}` }}" in tmpl


class TestScriptsGeneration:
    def test_scripts_not_generated_by_default(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        assert not (Path(out) / "scripts").exists()

    def test_scripts_directory_created(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        assert (Path(out) / "scripts").is_dir()

    def test_all_scripts_present(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        scripts = Path(out) / "scripts"
        for name in ("crc-setup.sh", "deploy.sh", "validate-deployment.sh",
                      "undeploy.sh", "status.sh"):
            assert (scripts / name).exists(), f"Missing {name}"

    def test_scripts_executable(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        import stat
        for name in ("crc-setup.sh", "deploy.sh", "validate-deployment.sh",
                      "undeploy.sh", "status.sh"):
            p = Path(out) / "scripts" / name
            assert p.stat().st_mode & stat.S_IXUSR, f"{name} not executable"

    def test_dsc_yaml_created(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        dsc = Path(out) / "scripts" / "dsc.yaml"
        assert dsc.exists()
        with open(dsc) as f:
            data = yaml.safe_load(f)
        assert data["kind"] == "DataScienceCluster"

    def test_readme_created(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        readme = Path(out) / "scripts" / "README.md"
        assert readme.exists()
        assert "CRC" in readme.read_text()

    def test_deploy_script_uses_eo_pipefail(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path, generate_crc_scripts=True)
        from pathlib import Path
        deploy = (Path(out) / "scripts" / "deploy.sh").read_text()
        assert "set -eo pipefail" in deploy
        assert "set -euo pipefail" not in deploy

    def test_pattern_sh_uses_euo_pipefail(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        pattern_sh = (Path(out) / "pattern.sh").read_text()
        assert "set -euo pipefail" in pattern_sh


class TestRemotePathFallback:
    def test_empty_path_becomes_dot(self, tmp_path):
        """Remote strategy with empty chart_path_in_repo should use '.' not ''."""
        out, _, _ = _remote_config(tmp_path, chart_path_in_repo="")
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        app = data["clusterGroup"]["applications"]["rag-quickstart"]
        assert app["path"] == "."

    def test_explicit_path_preserved(self, tmp_path):
        out, _, _ = _remote_config(tmp_path, chart_path_in_repo="deploy/helm/rag")
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        app = data["clusterGroup"]["applications"]["rag-quickstart"]
        assert app["path"] == "deploy/helm/rag"


class TestSkillMdConformance:
    """Verify generated output matches Patternizer SKILL.md conventions."""

    def test_namespaces_are_maps(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        assert isinstance(data["clusterGroup"]["namespaces"], dict)

    def test_local_chart_path_no_all(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-prod.yaml") as f:
            data = yaml.safe_load(f)
        for app in data["clusterGroup"]["applications"].values():
            if "path" in app:
                assert "/all/" not in app["path"]

    def test_secrets_chart_has_values_stubs(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "values.yaml") as f:
            data = yaml.safe_load(f)
        assert data is not None
        assert "secretStore" in data
        assert data["secretStore"]["name"] == "vault-backend"
        assert data["secretStore"]["kind"] == "ClusterSecretStore"

    def test_secrets_chart_has_per_group_stubs(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        with open(Path(out) / "charts" / "rag-quickstart-secrets" / "values.yaml") as f:
            data = yaml.safe_load(f)
        assert "pgvector" in data
        assert data["pgvector"]["key"] == "secret/data/hub/pgvector"
        assert data["pgvector"]["refreshInterval"] == "2m0s"

    def test_refresh_interval_2m0s(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        tmpl_dir = Path(out) / "charts" / "rag-quickstart-secrets" / "templates"
        for tmpl_file in tmpl_dir.glob("*.yaml"):
            with open(tmpl_file) as f:
                data = yaml.safe_load(f)
            if data and data.get("kind") == "ExternalSecret":
                assert data["spec"]["refreshInterval"] == "2m0s"

    def test_single_argocd_set(self, single_chart_quickstart, tmp_path):
        out, _, _ = _generate(single_chart_quickstart, tmp_path)
        from pathlib import Path
        with open(Path(out) / "values-global.yaml") as f:
            data = yaml.safe_load(f)
        assert data["global"]["singleArgoCD"] is True

    def test_eso_backtick_escaping_in_output(self, tmp_path):
        out, _, _ = _remote_config(tmp_path)
        from pathlib import Path
        tmpl_dir = Path(out) / "charts" / "rag-quickstart-secrets" / "templates"
        for tmpl_file in tmpl_dir.glob("*.yaml"):
            content = tmpl_file.read_text()
            if "ExternalSecret" in content:
                with open(tmpl_file) as f:
                    data = yaml.safe_load(f)
                tmpl_data = data.get("spec", {}).get("target", {}).get("template", {}).get("data", {})
                for key, val in tmpl_data.items():
                    if "{{" in str(val):
                        assert "`" in str(val), f"Unescaped ESO expression for {key}: {val}"
