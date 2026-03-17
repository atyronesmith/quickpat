"""Tests for quickpat.readiness."""

from pathlib import Path

import yaml

from quickpat.readiness import check_readiness, ReadinessResult
from tests.conftest import write_chart, write_values, write_template


def _make_quickstart(tmp_path, name="myapp", readme=True, license=True,
                     gitignore=True, description="Test app", templates=True):
    """Create a minimal quickstart repo for readiness testing."""
    qs = tmp_path / "qs"
    chart = qs / "helm"
    write_chart(chart, name, description=description)
    write_values(chart, {"replicas": 3})
    if templates:
        write_template(chart, "deploy.yaml", "kind: Deployment")
    if readme:
        (qs / "README.md").write_text(
            f"# {name}\n\nA quickstart for testing.\n\n"
            "## Prerequisites\n\n- OpenShift 4.x\n\n"
            "## Usage\n\n```bash\nhelm install\n```\n"
        )
    if license:
        (qs / "LICENSE").write_text("Apache License 2.0\n")
    if gitignore:
        (qs / ".gitignore").write_text("*.swp\n")
    return qs


class TestReadinessHappyPath:
    def test_clean_quickstart_is_ready(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        result = check_readiness(str(qs))
        assert result.ready
        assert result.charts_found == 1
        assert result.name == "myapp"

    def test_no_errors(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        result = check_readiness(str(qs))
        errors = [i for i in result.issues if i.severity == "error"]
        assert errors == []


class TestDocumentation:
    def test_missing_readme_is_error(self, tmp_path):
        qs = _make_quickstart(tmp_path, readme=False)
        result = check_readiness(str(qs))
        assert not result.ready
        msgs = [i.message for i in result.issues if i.severity == "error"]
        assert any("README" in m for m in msgs)

    def test_short_readme_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path, readme=False)
        (qs / "README.md").write_text("# Hi\n")
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("short" in m for m in msgs)


class TestLicense:
    def test_missing_license_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path, license=False)
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("LICENSE" in m for m in msgs)


class TestHelmCharts:
    def test_no_chart_is_error(self, tmp_path):
        qs = tmp_path / "empty-qs"
        qs.mkdir()
        (qs / "README.md").write_text("# Empty\n\n" + "x" * 100)
        result = check_readiness(str(qs))
        assert not result.ready
        msgs = [i.message for i in result.issues if i.severity == "error"]
        assert any("Chart.yaml" in m for m in msgs)

    def test_no_description_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path, description="")
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("description" in m for m in msgs)

    def test_no_templates_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path, templates=False)
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("templates" in m for m in msgs)

    def test_no_values_is_warning(self, tmp_path):
        qs = tmp_path / "qs"
        chart = qs / "helm"
        write_chart(chart, "noval")
        write_template(chart, "deploy.yaml", "kind: Deployment")
        # Don't write values.yaml
        (chart / "values.yaml").unlink(missing_ok=True)
        (qs / "README.md").write_text("# Test\n\n" + "x" * 100)
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("values.yaml" in m for m in msgs)


class TestValuesDefaults:
    def test_hardcoded_image_tag_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        chart = qs / "helm"
        write_values(chart, {
            "image": "quay.io/myapp:v1.2.3",
            "replicas": 1,
        })
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("hardcoded image tag" in m for m in msgs)

    def test_latest_tag_not_flagged(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        chart = qs / "helm"
        write_values(chart, {"image": "quay.io/myapp:latest"})
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert not any("hardcoded image tag" in m for m in msgs)

    def test_template_tag_not_flagged(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        chart = qs / "helm"
        write_values(chart, {"image": "quay.io/myapp:{{ .Values.tag }}"})
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert not any("hardcoded image tag" in m for m in msgs)


class TestRepoHygiene:
    def test_missing_gitignore_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path, gitignore=False)
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any(".gitignore" in m for m in msgs)

    def test_sensitive_file_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        (qs / ".env").write_text("SECRET=x\n")
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any(".env" in m for m in msgs)

    def test_values_secret_is_warning(self, tmp_path):
        qs = _make_quickstart(tmp_path)
        (qs / "values-secret.yaml").write_text("secrets: []\n")
        result = check_readiness(str(qs))
        msgs = [i.message for i in result.issues if i.severity == "warning"]
        assert any("values-secret.yaml" in m for m in msgs)
