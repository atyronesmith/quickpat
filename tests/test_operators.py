"""Tests for quickpat.operators."""

from quickpat.operators import resolve_co_dependencies


class TestCoDependencies:
    def test_gpu_adds_nfd(self):
        result = resolve_co_dependencies({"nvidia-gpu"})
        assert "nfd" in result
        assert "nvidia-gpu" in result

    def test_openshift_ai_adds_servicemesh_and_serverless(self):
        result = resolve_co_dependencies({"openshift-ai"})
        assert "servicemesh" in result
        assert "serverless" in result

    def test_transitive_resolution(self):
        # openshift-ai -> servicemesh, serverless
        # nvidia-gpu -> nfd
        result = resolve_co_dependencies({"openshift-ai", "nvidia-gpu"})
        assert set(result) == {
            "openshift-ai", "servicemesh", "serverless",
            "nvidia-gpu", "nfd",
        }

    def test_no_deps(self):
        result = resolve_co_dependencies({"amq-streams"})
        assert result == ["amq-streams"]

    def test_empty(self):
        result = resolve_co_dependencies(set())
        assert result == []

    def test_returns_sorted(self):
        result = resolve_co_dependencies({"nvidia-gpu", "openshift-ai"})
        assert result == sorted(result)
