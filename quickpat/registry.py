"""Fetch the AI Quickstart registry and shared chart index."""

import re
import urllib.request

import yaml

REGISTRY_URL = (
    "https://raw.githubusercontent.com/rh-ai-quickstart/"
    "ai-quickstart-pub/main/.gitmodules"
)

CHART_REPO_INDEX_URL = (
    "https://rh-ai-quickstart.github.io/ai-architecture-charts/index.yaml"
)

GITHUB_BASE = "https://github.com/rh-ai-quickstart"


def fetch_registry(url: str = REGISTRY_URL) -> list:
    """Fetch and parse the .gitmodules file from ai-quickstart-pub.

    Returns a list of dicts with 'name' and 'url' keys.
    """
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch registry: {e}")

    return _parse_gitmodules(content)


def _parse_gitmodules(content: str) -> list:
    """Parse .gitmodules content into a list of quickstart entries."""
    entries = []
    current = {}

    for line in content.splitlines():
        line = line.strip()

        match = re.match(r'\[submodule "quickstart/(.+)"\]', line)
        if match:
            if current:
                entries.append(current)
            current = {"name": match.group(1)}
            continue

        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if key == "url":
                current["url"] = val
            elif key == "path":
                current["path"] = val

    if current:
        entries.append(current)

    return entries


def resolve_name(name: str, registry: list = None) -> str:
    """Resolve a quickstart name to a clone URL.

    Tries exact match first, then case-insensitive, then substring.
    Returns the URL or raises ValueError.
    """
    if registry is None:
        registry = fetch_registry()

    # Exact match
    for entry in registry:
        if entry["name"] == name:
            return entry["url"]

    # Case-insensitive
    name_lower = name.lower()
    for entry in registry:
        if entry["name"].lower() == name_lower:
            return entry["url"]

    # Substring match
    matches = [e for e in registry if name_lower in e["name"].lower()]
    if len(matches) == 1:
        return matches[0]["url"]
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise ValueError(
            f"Ambiguous name '{name}' matches: {names}"
        )

    available = ", ".join(e["name"] for e in registry)
    raise ValueError(
        f"Unknown quickstart '{name}'. Available: {available}"
    )


# ── Shared chart index ───────────────────────────────────────────


def fetch_chart_index(url: str = CHART_REPO_INDEX_URL) -> dict:
    """Fetch the ai-architecture-charts Helm repo index.

    Returns a dict mapping chart name to latest version string.
    """
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            index = yaml.safe_load(resp.read())
    except Exception as e:
        raise RuntimeError(f"Failed to fetch chart index: {e}")

    latest = {}
    for name, versions in index.get("entries", {}).items():
        if versions:
            latest[name] = versions[0]["version"]
    return latest


def check_dependency_freshness(dependencies, chart_index=None):
    """Compare dependency versions against the shared chart repo.

    Args:
        dependencies: list of ChartDependency from analysis.
        chart_index: optional pre-fetched index (chart name -> latest version).

    Returns:
        list of (dep_name, pinned_version, latest_version) for stale deps.
    """
    if chart_index is None:
        try:
            chart_index = fetch_chart_index()
        except RuntimeError:
            return []

    stale = []
    seen = set()
    for dep in dependencies:
        if dep.repository and "ai-architecture-charts" in dep.repository:
            key = (dep.name, dep.version)
            if key in seen:
                continue
            seen.add(key)
            latest = chart_index.get(dep.name)
            if latest and dep.version != latest:
                stale.append((dep.name, dep.version, latest))
    return stale
