"""Shared fixtures for quickpat tests."""

import pytest
import yaml

from quickpat import config


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset global config state between tests."""
    config._config = None
    yield
    config._config = None


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
def grouped_chart_quickstart(tmp_path):
    """A multi-chart quickstart with subdirectory grouping."""
    qs = tmp_path / "grouped-qs"
    helm_dir = qs / "deploy" / "helm"

    # Two charts grouped under "observability/"
    obs_a = helm_dir / "observability" / "collector"
    write_chart(obs_a, "collector", "1.0.0", "OTEL collector")
    write_values(obs_a, {"collector": {"port": 4317}})

    obs_b = helm_dir / "observability" / "tempo"
    write_chart(obs_b, "tempo", "1.0.0", "Tempo tracing")
    write_values(obs_b, {"tempo": {"retention": "48h"}})

    # One chart under "inference/" with an LLM dependency
    inf = helm_dir / "inference" / "model"
    write_chart(inf, "model", "1.0.0", "Model serving", dependencies=[
        {"name": "llm-service", "version": "0.5.9",
         "repository": "https://rh-ai-quickstart.github.io/ai-architecture-charts"},
    ])
    write_values(inf, {"model": {"replicas": 1}})

    # One flat chart (no subdirectory)
    ui = helm_dir / "ui"
    write_chart(ui, "ui", "0.3.0", "Frontend")
    write_values(ui, {"ui": {"port": 8080}})

    return qs


@pytest.fixture
def numbered_group_quickstart(tmp_path):
    """A quickstart with numbered subdirectory prefixes like lls-observability."""
    qs = tmp_path / "numbered-qs"
    helm_dir = qs / "helm"

    a = helm_dir / "01-operators" / "my-operator"
    write_chart(a, "my-operator", "1.0.0")
    write_values(a, {"op": {"enabled": True}})

    b = helm_dir / "02-services" / "api"
    write_chart(b, "api", "1.0.0")
    write_values(b, {"api": {"port": 8080}})

    c = helm_dir / "02-services" / "worker"
    write_chart(c, "worker", "1.0.0")
    write_values(c, {"worker": {"replicas": 2}})

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
