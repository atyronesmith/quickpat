"""Tests for quickpat.analyzer."""

import pytest
import yaml

from quickpat.analyzer import QuickstartAnalyzer
from tests.conftest import write_chart, write_values, write_template


class TestSingleChart:
    def test_finds_chart_under_deploy_helm(self, single_chart_quickstart):
        analyzer = QuickstartAnalyzer(str(single_chart_quickstart))
        analysis = analyzer.analyze()
        assert analysis.name == "myapp"
        assert analysis.version == "1.0.0"
        assert len(analysis.charts) == 1

    def test_finds_chart_under_helm(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "helm"
        write_chart(chart, "helmapp")
        analysis = QuickstartAnalyzer(str(qs)).analyze()
        assert analysis.name == "helmapp"

    def test_finds_chart_under_chart(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "chart"
        write_chart(chart, "chartapp")
        analysis = QuickstartAnalyzer(str(qs)).analyze()
        assert analysis.name == "chartapp"

    def test_finds_chart_at_root(self, tmp_path):
        write_chart(tmp_path, "rootapp")
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        assert analysis.name == "rootapp"

    def test_no_chart_raises(self, tmp_path):
        qs = tmp_path / "empty"
        qs.mkdir()
        with pytest.raises(FileNotFoundError, match="No Chart.yaml found"):
            QuickstartAnalyzer(str(qs)).analyze()

    def test_parses_dependencies(self, single_chart_quickstart):
        analysis = QuickstartAnalyzer(str(single_chart_quickstart)).analyze()
        assert len(analysis.dependencies) == 1
        assert analysis.dependencies[0].name == "pgvector"
        assert analysis.dependencies[0].version == "0.5.0"

    def test_description(self, single_chart_quickstart):
        analysis = QuickstartAnalyzer(str(single_chart_quickstart)).analyze()
        assert analysis.description == "My test app"


class TestMultiChart:
    def test_finds_all_charts(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        assert len(analysis.charts) == 3
        names = {ci.name for ci in analysis.charts}
        assert names == {"app", "db", "ui"}

    def test_name_is_repo_dir(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        assert analysis.name == "multi-qs"

    def test_aggregates_dependencies(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        assert len(analysis.dependencies) == 1
        assert analysis.dependencies[0].name == "llm-service"

    def test_oai_labels_only_on_inference_chart(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        labels_map = {ci.name: ci.needs_oai_labels for ci in analysis.charts}
        assert labels_map["app"] is True  # has llm-service dep
        assert labels_map["db"] is False
        assert labels_map["ui"] is False


class TestSubdirectoryGrouping:
    def test_grouped_charts_get_group(self, grouped_chart_quickstart):
        analysis = QuickstartAnalyzer(str(grouped_chart_quickstart)).analyze()
        groups = {ci.name: ci.group for ci in analysis.charts}
        assert groups["collector"] == "observability"
        assert groups["tempo"] == "observability"
        assert groups["model"] == "inference"
        assert groups["ui"] == ""

    def test_numbered_prefix_stripped(self, numbered_group_quickstart):
        analysis = QuickstartAnalyzer(str(numbered_group_quickstart)).analyze()
        groups = {ci.name: ci.group for ci in analysis.charts}
        assert groups["my-operator"] == "operators"
        assert groups["api"] == "services"
        assert groups["worker"] == "services"

    def test_flat_charts_have_no_group(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        for ci in analysis.charts:
            assert ci.group == ""


class TestSecretDetection:
    def test_detects_password(self, single_chart_quickstart):
        analysis = QuickstartAnalyzer(str(single_chart_quickstart)).analyze()
        secret_names = [s.name for s in analysis.detected_secrets]
        assert "password" in secret_names

    def test_filters_secretName(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"secretName": "my-secret", "token": "abc"})
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        names = [s.name for s in analysis.detected_secrets]
        assert "secretName" not in names
        assert "token" in names

    def test_filters_secretKeyRef(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"secretKeyRef": {"name": "x", "key": "y"}})
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        names = [s.name for s in analysis.detected_secrets]
        assert "secretKeyRef" not in names

    def test_filters_useToken(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"useServiceAccountToken": True, "api_key": "xxx"})
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        names = [s.name for s in analysis.detected_secrets]
        assert "useServiceAccountToken" not in names
        assert "api_key" in names

    def test_filters_generic_key(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"key": "value", "secrets": {"a": 1}})
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        names = [s.name for s in analysis.detected_secrets]
        assert "key" not in names
        assert "secrets" not in names

    def test_detects_real_secrets(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {
            "password": "x",
            "hf_token": "x",
            "api_key": "x",
            "TAVILY_SEARCH_API_KEY": "x",
            "credential": "x",
        })
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        names = {s.name for s in analysis.detected_secrets}
        assert names == {"password", "hf_token", "api_key", "TAVILY_SEARCH_API_KEY", "credential"}

    def test_secret_paths_include_chart_name(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        paths = [s.path for s in analysis.detected_secrets]
        # Secrets should be prefixed with chart name
        assert any(p.startswith("app.") for p in paths)
        assert any(p.startswith("db.") for p in paths)


class TestOperatorDetection:
    def test_detects_gpu_from_template(self, gpu_chart_quickstart):
        analysis = QuickstartAnalyzer(str(gpu_chart_quickstart)).analyze()
        assert "nvidia-gpu" in analysis.detected_operators
        assert "nfd" in analysis.detected_operators  # co-dependency

    def test_detects_openshift_ai_from_dependency(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        assert "openshift-ai" in analysis.detected_operators
        assert "servicemesh" in analysis.detected_operators  # co-dep
        assert "serverless" in analysis.detected_operators  # co-dep

    def test_no_operators_for_plain_chart(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "plain")
        write_values(chart, {"replicas": 3})
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        assert analysis.detected_operators == []


class TestFeatureDetection:
    def test_vector_db_from_dependency(self, single_chart_quickstart):
        analysis = QuickstartAnalyzer(str(single_chart_quickstart)).analyze()
        assert analysis.has_vector_db is True

    def test_gpu_from_template(self, gpu_chart_quickstart):
        analysis = QuickstartAnalyzer(str(gpu_chart_quickstart)).analyze()
        assert analysis.has_gpu_requirement is True
        assert analysis.has_llm_service is True  # vllm in template

    def test_llm_service_from_dependency(self, multi_chart_quickstart):
        analysis = QuickstartAnalyzer(str(multi_chart_quickstart)).analyze()
        assert analysis.has_llm_service is True

    def test_object_storage_from_text(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_template(chart, "deploy.yaml", "image: minio/minio:latest")
        analysis = QuickstartAnalyzer(str(tmp_path)).analyze()
        assert analysis.has_object_storage is True
