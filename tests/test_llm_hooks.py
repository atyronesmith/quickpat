"""Tests for LLM hooks with structured output."""

import pytest

from quickpat.pipeline import (
    _llm_check_operators,
    _llm_review_secrets,
    skill_analyze,
)
from quickpat.providers.base import LLMResponse
from quickpat.validator import _llm_review, _parse_structured_review, _parse_text_review, Issue
from tests.conftest import write_chart, write_values


class _MockStructuredProvider:
    """Mock provider that returns structured output when schema is passed."""
    def __init__(self, response_dict):
        self._response = response_dict

    def complete(self, system, prompt, **kwargs):
        if kwargs.get("response_schema"):
            return LLMResponse(
                content=str(self._response), model="mock", provider="mock",
                parsed=self._response,
            )
        return LLMResponse(
            content=str(self._response), model="mock", provider="mock",
        )


class _MockTextProvider:
    """Mock provider that always returns text (no structured output support)."""
    def __init__(self, response_text):
        self._text = response_text

    def complete(self, system, prompt, **kwargs):
        return LLMResponse(
            content=self._text, model="mock", provider="mock",
        )


def _mock_structured_llm(response_dict):
    return _MockStructuredProvider(response_dict)


def _mock_text_llm(response_text):
    return _MockTextProvider(response_text)


class TestOperatorCheckStructured:
    def test_returns_valid_operators(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_structured_llm({"operators": ["openshift-ai", "nvidia-gpu"]})
        result = _llm_check_operators(llm, analysis)
        assert "openshift-ai" in result
        assert "nvidia-gpu" in result

    def test_filters_invalid_operators(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_structured_llm({"operators": ["openshift-ai", "fake-operator"]})
        result = _llm_check_operators(llm, analysis)
        assert "openshift-ai" in result
        assert "fake-operator" not in result

    def test_empty_operators(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_structured_llm({"operators": []})
        result = _llm_check_operators(llm, analysis)
        assert result == []

    def test_text_fallback(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_text_llm("openshift-ai, nvidia-gpu")
        result = _llm_check_operators(llm, analysis)
        assert "openshift-ai" in result
        assert "nvidia-gpu" in result

    def test_text_fallback_none(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_text_llm("none")
        result = _llm_check_operators(llm, analysis)
        assert result == []


class TestSecretReviewStructured:
    def test_returns_summary_with_false_positives(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"password": "x", "key": "y"})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_structured_llm({
            "false_positives": ["key"],
            "summary": "key is too generic",
        })
        result = _llm_review_secrets(llm, analysis)
        assert "key" in result
        assert "generic" in result

    def test_no_false_positives(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"password": "x"})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_structured_llm({
            "false_positives": [],
            "summary": "All secrets look valid",
        })
        result = _llm_review_secrets(llm, analysis)
        assert "valid" in result.lower()

    def test_text_fallback(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"password": "x"})
        analysis = skill_analyze(str(tmp_path))

        llm = _mock_text_llm("All secrets look legitimate.")
        result = _llm_review_secrets(llm, analysis)
        assert "legitimate" in result


class TestValidationReviewStructured:
    def test_valid_pattern(self, tmp_path):
        result = _parse_structured_review({"valid": True, "issues": []})
        assert result == []

    def test_issues_parsed(self):
        data = {
            "valid": False,
            "issues": [
                {"file": "values-global.yaml", "severity": "error",
                 "message": "main nested under global"},
                {"file": "Makefile", "severity": "warning",
                 "message": "uses legacy include"},
            ],
        }
        result = _parse_structured_review(data)
        assert len(result) == 2
        assert result[0].severity == "error"
        assert result[0].file == "values-global.yaml"
        assert "[LLM]" in result[0].message
        assert result[1].severity == "warning"

    def test_text_fallback_valid(self):
        result = _parse_text_review("VALID")
        assert result == []

    def test_text_fallback_issues(self):
        text = "ISSUE|values-global.yaml|error|main nested under global\nISSUE|Makefile|warning|legacy include"
        result = _parse_text_review(text)
        assert len(result) == 2
        assert result[0].file == "values-global.yaml"
        assert result[0].severity == "error"

    def test_llm_review_with_structured_mock(self, tmp_path):
        """End-to-end: _llm_review with a structured mock LLM."""
        # Create a minimal pattern directory
        (tmp_path / "values-global.yaml").write_text("global: {}\n")
        (tmp_path / "Makefile").write_text("include Makefile-common\n")

        llm = _mock_structured_llm({
            "valid": False,
            "issues": [
                {"file": "values-global.yaml", "severity": "error",
                 "message": "missing main key"},
            ],
        })
        result = _llm_review(tmp_path, llm)
        assert len(result) == 1
        assert result[0].file == "values-global.yaml"

    def test_llm_review_with_text_mock(self, tmp_path):
        """End-to-end: _llm_review falls back to text parsing."""
        (tmp_path / "values-global.yaml").write_text("global: {}\n")

        llm = _mock_text_llm("ISSUE|values-global.yaml|error|missing main key")
        result = _llm_review(tmp_path, llm)
        assert len(result) == 1
        assert result[0].file == "values-global.yaml"


class _BadProvider:
    """Mock provider that always raises."""
    def complete(self, system, prompt, **kwargs):
        raise ConnectionError("API down")


class TestLLMExceptionHandling:
    def test_operator_check_handles_exception(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"replicas": 3})
        analysis = skill_analyze(str(tmp_path))

        result = _llm_check_operators(_BadProvider(), analysis)
        assert result == []

    def test_secret_review_handles_exception(self, tmp_path):
        chart = tmp_path / "helm"
        write_chart(chart, "test")
        write_values(chart, {"password": "x"})
        analysis = skill_analyze(str(tmp_path))

        result = _llm_review_secrets(_BadProvider(), analysis)
        assert result == ""

    def test_validation_review_handles_exception(self, tmp_path):
        (tmp_path / "values-global.yaml").write_text("global: {}\n")

        result = _llm_review(tmp_path, _BadProvider())
        assert len(result) == 1
        assert "failed" in result[0].message.lower()
