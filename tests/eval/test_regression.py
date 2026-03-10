"""Regression detection: compare current results against a baseline."""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.eval

BASELINE_PATH = Path(__file__).resolve().parent / "baseline.jsonl"


def _load_jsonl(path):
    """Load a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _make_key(record):
    return (record["quickstart"], record["provider"])


@pytest.fixture
def baseline():
    if not BASELINE_PATH.exists():
        pytest.skip("No baseline file — run with --update-baseline first")
    return _load_jsonl(BASELINE_PATH)


@pytest.fixture
def current_results(request):
    results_dir = request.config.getoption(
        "--eval-results-dir",
        str(Path(__file__).resolve().parent / "results"),
    )
    results_path = Path(results_dir) / "eval_results.jsonl"
    if not results_path.exists():
        pytest.skip("No current results — run test_eval.py first")
    return _load_jsonl(results_path)


def test_no_regressions(baseline, current_results):
    """Every quickstart x provider that passed in baseline must still pass."""
    # Build lookup of baseline passes
    baseline_passes = set()
    for rec in baseline:
        if rec.get("success") and rec.get("valid"):
            baseline_passes.add(_make_key(rec))

    if not baseline_passes:
        pytest.skip("No passing entries in baseline")

    # Build lookup of current results
    current_lookup = {}
    for rec in current_results:
        key = _make_key(rec)
        # Keep the latest result for each key
        current_lookup[key] = rec

    # Check for regressions
    regressions = []
    for key in baseline_passes:
        current = current_lookup.get(key)
        if current is None:
            continue  # not tested this run, skip
        if not current.get("success") or not current.get("valid"):
            regressions.append({
                "quickstart": key[0],
                "provider": key[1],
                "errors": current.get("errors", 0),
                "issues": current.get("issues", []),
            })

    assert not regressions, (
        f"Regressions detected in {len(regressions)} combo(s):\n"
        + "\n".join(
            f"  {r['quickstart']} + {r['provider']}: {r['errors']} errors"
            for r in regressions
        )
    )
