"""Publication readiness checks for AI Quickstart repositories.

Validates a quickstart source repo against criteria derived from
the ai-quickstart-pub publication checklist.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .analyzer import QuickstartAnalyzer
from .registry import check_dependency_freshness, detect_local_forks, fetch_chart_index


@dataclass
class ReadinessIssue:
    """A single readiness finding."""
    category: str  # e.g. "documentation", "helm", "dependencies"
    severity: str  # "error" or "warning"
    message: str


@dataclass
class ReadinessResult:
    """Full readiness check outcome."""
    ready: bool
    issues: list = field(default_factory=list)
    name: str = ""
    charts_found: int = 0


def check_readiness(quickstart_path: str) -> ReadinessResult:
    """Run all readiness checks on a quickstart source directory."""
    root = Path(quickstart_path).resolve()
    issues = []

    issues.extend(_check_documentation(root))
    issues.extend(_check_license(root))
    issues.extend(_check_helm_charts(root))
    issues.extend(_check_values_defaults(root))
    issues.extend(_check_dependencies(root))
    issues.extend(_check_repo_hygiene(root))

    name = root.name
    charts_found = 0
    try:
        analysis = QuickstartAnalyzer(str(root)).analyze()
        name = analysis.name
        charts_found = len(analysis.charts)
    except FileNotFoundError:
        pass

    return ReadinessResult(
        ready=not any(i.severity == "error" for i in issues),
        issues=issues,
        name=name,
        charts_found=charts_found,
    )


# ── Documentation ────────────────────────────────────────────────────


def _check_documentation(root: Path) -> list:
    issues = []

    readme_names = ["README.md", "README.rst", "README.txt", "README"]
    if not any((root / n).exists() for n in readme_names):
        issues.append(ReadinessIssue(
            "documentation", "error", "Missing README.md"
        ))
    else:
        # Check README has meaningful content
        for n in readme_names:
            p = root / n
            if p.exists():
                content = p.read_text()
                if len(content.strip()) < 100:
                    issues.append(ReadinessIssue(
                        "documentation", "warning",
                        "README is very short (< 100 chars) — add description, prerequisites, usage"
                    ))
                break

    return issues


# ── License ──────────────────────────────────────────────────────────


def _check_license(root: Path) -> list:
    issues = []
    license_names = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"]
    if not any((root / n).exists() for n in license_names):
        issues.append(ReadinessIssue(
            "license", "warning", "Missing LICENSE file"
        ))
    return issues


# ── Helm Charts ──────────────────────────────────────────────────────


def _check_helm_charts(root: Path) -> list:
    issues = []

    try:
        analyzer = QuickstartAnalyzer(str(root))
        analysis = analyzer.analyze()
    except FileNotFoundError as e:
        issues.append(ReadinessIssue("helm", "error", str(e)))
        return issues

    for ci in analysis.charts:
        chart_path = Path(ci.chart_path)

        # Check Chart.yaml has required fields
        chart_yaml = chart_path / "Chart.yaml"
        if chart_yaml.exists():
            with open(chart_yaml) as f:
                chart_data = yaml.safe_load(f) or {}

            if not chart_data.get("description"):
                issues.append(ReadinessIssue(
                    "helm", "warning",
                    f"Chart '{ci.name}' has no description in Chart.yaml"
                ))

            api_version = chart_data.get("apiVersion", "")
            if api_version != "v2":
                issues.append(ReadinessIssue(
                    "helm", "warning",
                    f"Chart '{ci.name}' uses apiVersion '{api_version}' — v2 recommended"
                ))

        # Check values.yaml exists
        if not (chart_path / "values.yaml").exists():
            issues.append(ReadinessIssue(
                "helm", "warning",
                f"Chart '{ci.name}' has no values.yaml"
            ))

        # Check templates directory exists
        if not (chart_path / "templates").is_dir():
            issues.append(ReadinessIssue(
                "helm", "warning",
                f"Chart '{ci.name}' has no templates/ directory"
            ))

    return issues


# ── Values defaults ──────────────────────────────────────────────────


def _check_values_defaults(root: Path) -> list:
    issues = []

    try:
        analyzer = QuickstartAnalyzer(str(root))
        analysis = analyzer.analyze()
    except FileNotFoundError:
        return issues

    for ci in analysis.charts:
        chart_path = Path(ci.chart_path)
        values_path = chart_path / "values.yaml"
        if not values_path.exists():
            continue

        with open(values_path) as f:
            values = yaml.safe_load(f) or {}

        # Check for hardcoded image tags (common anti-pattern)
        _walk_for_hardcoded_images(values, ci.name, "", issues)

    return issues


def _walk_for_hardcoded_images(obj, chart_name, path, issues):
    if isinstance(obj, dict):
        for key, value in obj.items():
            current = f"{path}.{key}" if path else key
            if key == "image" and isinstance(value, str) and ":" in value:
                tag = value.rsplit(":", 1)[-1]
                if tag not in ("latest",) and not tag.startswith("{{"):
                    issues.append(ReadinessIssue(
                        "values", "warning",
                        f"Chart '{chart_name}' has hardcoded image tag at {current}: {value}"
                    ))
            _walk_for_hardcoded_images(value, chart_name, current, issues)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _walk_for_hardcoded_images(item, chart_name, f"{path}[{i}]", issues)


# ── Dependencies ─────────────────────────────────────────────────────


def _check_dependencies(root: Path) -> list:
    issues = []

    try:
        analyzer = QuickstartAnalyzer(str(root))
        analysis = analyzer.analyze()
    except FileNotFoundError:
        return issues

    if not analysis.dependencies:
        return issues

    try:
        chart_index = fetch_chart_index()
    except RuntimeError:
        return issues

    # Stale dependencies
    stale = check_dependency_freshness(analysis.dependencies, chart_index)
    for name, pinned, latest in stale:
        issues.append(ReadinessIssue(
            "dependencies", "warning",
            f"Dependency '{name}' is pinned to {pinned}, latest is {latest}"
        ))

    # Local forks
    forks = detect_local_forks(analysis.charts, chart_index)
    for name, path, latest in forks:
        issues.append(ReadinessIssue(
            "dependencies", "warning",
            f"Chart '{name}' is a local fork of shared chart (latest: {latest})"
        ))

    return issues


# ── Repo hygiene ─────────────────────────────────────────────────────


def _check_repo_hygiene(root: Path) -> list:
    issues = []

    # Check .gitignore exists
    if not (root / ".gitignore").exists():
        issues.append(ReadinessIssue(
            "hygiene", "warning", "Missing .gitignore"
        ))

    # Check for common sensitive files that shouldn't be committed
    sensitive_files = [
        "values-secret.yaml", "values-secret.yaml.template",
        ".env", "credentials.json", "kubeconfig",
    ]
    for f in sensitive_files:
        if (root / f).exists():
            issues.append(ReadinessIssue(
                "hygiene", "warning",
                f"Sensitive file '{f}' found in repo root — should be gitignored"
            ))

    # Check for deploy directory structure
    deploy_dirs = [
        root / "deploy" / "helm",
        root / "deploy" / "cluster" / "helm",
        root / "helm",
        root / "chart",
    ]
    has_structured = any(d.is_dir() for d in deploy_dirs)
    has_root_chart = (root / "Chart.yaml").exists()
    if not has_structured and not has_root_chart:
        issues.append(ReadinessIssue(
            "structure", "error",
            "No standard chart directory found (deploy/helm/, helm/, chart/)"
        ))

    return issues
