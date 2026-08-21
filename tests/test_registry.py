"""Tests for the registry module."""

from unittest.mock import patch

import pytest
import urllib.request

from quickpat.registry import (
     fetch_registry,
     fetch_chart_index,
     resolve_name,
     _parse_gitmodules,
     check_dependency_freshness,
     detect_local_forks,
)
from quickpat.analyzer import ChartDependency, ChartInfo


GITMODULES = """\
[submodule "quickstart/llm-cpu-serving"]
        path = quickstarts/llm-cpu-serving
        url = https://github.com/rh-ai-quickstart/llm-cpu-serving
[submodule "quickstart/vector-database"]
        path = quickstarts/vector-database
        url = https://github.com/rh-ai-quickstart/vector-database
"""


# ── _parse_gitmodules ───────────────────────────────────────────────


class TestParseGitmodules:
    def test_two_modules(self):
        entries = _parse_gitmodules(GITMODULES)
        assert {e["name"] for e in entries} == {"llm-cpu-serving", "vector-database"}
        by_name = {e["name"]: e for e in entries}
        assert by_name["llm-cpu-serving"]["url"].endswith("/llm-cpu-serving")
        assert by_name["llm-cpu-serving"]["path"] == "quickstarts/llm-cpu-serving"

    def test_empty(self):
        assert _parse_gitmodules("") == []

    def test_module_without_url_is_included(self):
        content = '[submodule "quickstart/lonely"]\n        path = quickstarts/lonely\n'
        entries = _parse_gitmodules(content)
        assert len(entries) == 1
        assert entries[0]["name"] == "lonely"
        assert "url" not in entries[0]


# ── resolve_name ────────────────────────────────────────────────────


class TestResolveName:
    REGISTRY = [
        {"name": "llm-cpu-serving", "url": "https://x/llm-cpu-serving"},
        {"name": "vector-database", "url": "https://x/vector-database"},
    ]

    def test_exact_match(self):
        assert resolve_name("llm-cpu-serving", self.REGISTRY) == "https://x/llm-cpu-serving"

    def test_case_insensitive(self):
        assert resolve_name("LLM-CPU-SERVING", self.REGISTRY) == "https://x/llm-cpu-serving"

    def test_unique_substring(self):
        assert resolve_name("vector", self.REGISTRY) == "https://x/vector-database"

    def test_ambiguous_substring(self):
        reg = [
             {"name": "foox", "url": "https://x/foox"},
             {"name": "foobarx", "url": "https://x/foobarx"},
         ]
        with pytest.raises(ValueError, match="Ambiguous"):
            resolve_name("foo", reg)

    def test_unknown(self):
        with pytest.raises(ValueError, match="Unknown quickstart"):
            resolve_name("does-not-exist", self.REGISTRY)

    def test_entry_without_url_is_skipped(self):
        reg = [
             {"name": "lonely", "path": "quickstarts/lonely"},
             {"name": "vector-database", "url": "https://x/vector-database"},
         ]
        with pytest.raises(ValueError, match="Unknown quickstart"):
            resolve_name("lonely", reg)
        assert resolve_name("vector-database", reg) == "https://x/vector-database"


# ── fetch_registry / fetch_chart_index ──────────────────────────────


class TestFetchRegistry:
    @patch("quickpat.registry.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        resp = mock_urlopen.return_value.__enter__.return_value
        resp.read.return_value = GITMODULES.encode()
        entries = fetch_registry(url="http://example/.gitmodules")
        assert {e["name"] for e in entries} == {"llm-cpu-serving", "vector-database"}

    @patch("quickpat.registry.urllib.request.urlopen")
    def test_oserror_raises_runtimeerror(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.request.URLError("connection refused")
        with pytest.raises(RuntimeError, match="Failed to fetch registry"):
            fetch_registry(url="http://example/.gitmodules")

    @patch("quickpat.registry.urllib.request.urlopen")
    def test_valueerror_raises_runtimeerror(self, mock_urlopen):
        mock_urlopen.side_effect = ValueError("bad response")
        with pytest.raises(RuntimeError, match="Failed to fetch registry"):
            fetch_registry(url="http://example/.gitmodules")

    @patch("quickpat.registry.urllib.request.urlopen")
    def test_chart_index_success(self, mock_urlopen):
        import yaml as _yaml
        resp = mock_urlopen.return_value.__enter__.return_value
        resp.read.return_value = _yaml.safe_dump({
            "entries": {"pgvector": [{"version": "0.5.0"}, {"version": "0.4.0"}]}
        }).encode()
        latest = fetch_chart_index(url="http://example/index.yaml")
        assert latest["pgvector"] == "0.5.0"

    @patch("quickpat.registry.urllib.request.urlopen")
    def test_chart_index_oserror(self, mock_urlopen):
        mock_urlopen.side_effect = OSError("timeout")
        with pytest.raises(RuntimeError, match="Failed to fetch chart index"):
            fetch_chart_index(url="http://example/index.yaml")

    @patch("quickpat.registry.urllib.request.urlopen")
    def test_chart_index_yaml_error(self, mock_urlopen):
        resp = mock_urlopen.return_value.__enter__.return_value
        resp.read.return_value=b"not: [valid: yaml"
        with pytest.raises(RuntimeError, match="Failed to fetch chart index"):
            fetch_chart_index(url="http://example/index.yaml")


# ── check_dependency_freshness ──────────────────────────────────────


class TestCheckDependencyFreshness:
    SHARED = "https://rh-ai-quickstart.github.io/ai-architecture-charts"
    INDEX = {"pgvector": "0.5.0", "llm-service": "0.6.0"}

    def test_stale_detected(self):
        deps = [ChartDependency("pgvector", "0.4.0", repository=self.SHARED)]
        stale = check_dependency_freshness(deps, self.INDEX)
        assert stale == [("pgvector", "0.4.0", "0.5.0")]

    def test_current_not_stale(self):
        deps = [ChartDependency("pgvector", "0.5.0", repository=self.SHARED)]
        assert check_dependency_freshness(deps, self.INDEX) == []

    def test_non_shared_repo_skipped(self):
        deps = [ChartDependency("pgvector", "0.1.0", repository="https://other/repo")]
        assert check_dependency_freshness(deps, self.INDEX) == []

    def test_duplicate_dedup(self):
        deps = [
            ChartDependency("pgvector", "0.4.0", repository=self.SHARED),
            ChartDependency("pgvector", "0.4.0", repository=self.SHARED),
        ]
        stale = check_dependency_freshness(deps, self.INDEX)
        assert stale == [("pgvector", "0.4.0", "0.5.0")]

    def test_fetch_error_returns_empty(self):
        deps = [ChartDependency("pgvector", "0.1.0", repository=self.SHARED)]
        with patch("quickpat.registry.fetch_chart_index", side_effect=RuntimeError("nope")):
            assert check_dependency_freshness(deps) == []


# ── detect_local_forks ──────────────────────────────────────────────


class TestDetectLocalForks:
    SHARED = "https://rh-ai-quickstart.github.io/ai-architecture-charts"

    def test_local_fork_detected(self):
        charts = [ChartInfo(name="pgvector", version="0.5.0", chart_path="deploy/helm/pgvector")]
        forks = detect_local_forks(charts, {"pgvector": "0.5.0"})
        assert forks == [("pgvector", "deploy/helm/pgvector", "0.5.0")]

    def test_shared_dependency_not_a_fork(self):
        charts = [ChartInfo(
            name="pgvector",
            chart_path="deploy/helm/app",
            dependencies=[ChartDependency("pgvector", "0.5.0", repository=self.SHARED)],
        )]
        assert detect_local_forks(charts, {"pgvector": "0.5.0"}) == []

    def test_unknown_name_not_a_fork(self):
        charts = [ChartInfo(name="not-shared", chart_path="deploy/helm/app")]
        assert detect_local_forks(charts, {"pgvector": "0.5.0"}) == []
