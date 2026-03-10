"""Evaluation tests: transform each quickstart with each available provider."""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.eval


def test_transform(
    quickstart_name, quickstart_path, provider_name, model_name,
    llm_callable, tmp_path, eval_results_dir,
):
    from transform_quickstart import transform

    output_dir = str(tmp_path / f"{quickstart_name}-{provider_name}")
    start = time.monotonic()

    result = transform(
        quickstart_path=quickstart_path,
        output_dir=output_dir,
        llm=llm_callable,
    )

    duration = time.monotonic() - start

    # Count issues by severity
    errors = 0
    warnings = 0
    unfixed_issues = []
    if result.validation:
        for issue in result.validation.issues:
            if issue.severity == "error":
                errors += 1
            else:
                warnings += 1
            if not issue.fix_applied:
                unfixed_issues.append({
                    "file": issue.file,
                    "severity": issue.severity,
                    "message": issue.message,
                })

    # Build JSONL record
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "quickstart": quickstart_name,
        "provider": provider_name,
        "model": model_name,
        "success": result.success,
        "valid": result.validation.valid if result.validation else False,
        "errors": errors,
        "warnings": warnings,
        "fixes_applied": result.validation.fixes_applied if result.validation else 0,
        "fix_iterations": result.validation.iterations if result.validation else 0,
        "duration_seconds": round(duration, 2),
        "issues": unfixed_issues,
        "llm_decisions": result.llm_decisions,
    }

    # Write to JSONL
    results_path = Path(eval_results_dir) / "eval_results.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a") as f:
        f.write(json.dumps(record) + "\n")

    # Assertions
    assert result.success, (
        f"Transform failed for {quickstart_name} with {provider_name}: "
        f"{result.warnings}"
    )
    assert result.validation is not None, "No validation result"
    assert result.validation.valid, (
        f"Validation failed for {quickstart_name} with {provider_name}: "
        f"{[i for i in unfixed_issues if i['severity'] == 'error']}"
    )

    # No unfixed errors
    unfixed_errors = [i for i in unfixed_issues if i["severity"] == "error"]
    assert not unfixed_errors, (
        f"Unfixed errors for {quickstart_name} with {provider_name}: "
        f"{unfixed_errors}"
    )
