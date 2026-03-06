"""Shared fixtures for quickpat tests."""

import pytest
import yaml


def write_chart(path, name, version="0.1.0", description="", dependencies=None):
    """Create a minimal Chart.yaml."""
    path.mkdir(parents=True, exist_ok=True)
    chart = {
        "apiVersion": "v2",
        "name": name,
        "version": version,
        "type": "application",
    }
    if description:
        chart["description"] = description
    if dependencies:
        chart["dependencies"] = dependencies
    (path / "Chart.yaml").write_text(yaml.dump(chart))
    return path


def write_values(path, values):
    """Create a values.yaml."""
    (path / "values.yaml").write_text(yaml.dump(values))


def write_template(path, filename, content):
    """Create a template file."""
    tpl_dir = path / "templates"
    tpl_dir.mkdir(exist_ok=True)
    (tpl_dir / filename).write_text(content)


@pytest.fixture
def single_chart_quickstart(tmp_path):
    """A minimal single-chart quickstart under deploy/helm/."""
    qs = tmp_path / "my-quickstart"
    chart_dir = qs / "deploy" / "helm" / "myapp"
    write_chart(chart_dir, "myapp", "1.0.0", "My test app", dependencies=[
        {"name": "pgvector", "version": "0.5.0",
         "repository": "https://rh-ai-quickstart.github.io/ai-architecture-charts"},
    ])
    write_values(chart_dir, {
        "myapp": {
            "password": "changeme",
            "config": {"replicas": 3},
        }
    })
    return qs


@pytest.fixture
def multi_chart_quickstart(tmp_path):
    """A multi-chart quickstart with 3 charts under deploy/helm/."""
    qs = tmp_path / "multi-qs"
    helm_dir = qs / "deploy" / "helm"

    # App chart with inference dependency
    app = helm_dir / "app"
    write_chart(app, "app", "1.0.0", "Main app", dependencies=[
        {"name": "llm-service", "version": "0.5.9",
         "repository": "https://rh-ai-quickstart.github.io/ai-architecture-charts"},
    ])
    write_values(app, {"app": {"api_key": "xxx", "replicas": 1}})

    # DB chart
    db = helm_dir / "db"
    write_chart(db, "db", "0.2.0", "Database")
    write_values(db, {"db": {"password": "secret123"}})

    # UI chart
    ui = helm_dir / "ui"
    write_chart(ui, "ui", "0.3.0", "Frontend")
    write_values(ui, {"ui": {"port": 8080}})

    return qs


@pytest.fixture
def gpu_chart_quickstart(tmp_path):
    """A quickstart with GPU indicators in templates."""
    qs = tmp_path / "gpu-qs"
    chart_dir = qs / "helm"
    write_chart(chart_dir, "gpu-app", "1.0.0")
    write_values(chart_dir, {"gpu": {"enabled": True}})
    write_template(chart_dir, "deployment.yaml",
                   "nvidia.com/gpu: 1\nimage: vllm/vllm-openai")
    return qs
