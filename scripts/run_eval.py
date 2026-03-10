#!/usr/bin/env python3
"""Convenience wrapper for running the eval test harness."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent / "tests" / "eval"
RESULTS_DIR = EVAL_DIR / "results"
BASELINE_PATH = EVAL_DIR / "baseline.jsonl"


def run_eval(args):
    cmd = [sys.executable, "-m", "pytest", str(EVAL_DIR / "test_eval.py"), "-v"]

    if args.quickstart:
        cmd.extend(["--quickstart", args.quickstart])
    if args.provider:
        cmd.extend(["--provider", args.provider])
    if args.no_cache:
        cmd.append("--no-cache")
    cmd.extend(["--eval-results-dir", str(RESULTS_DIR)])

    # Clear previous results
    results_file = RESULTS_DIR / "eval_results.jsonl"
    if results_file.exists():
        results_file.unlink()

    result = subprocess.run(cmd)

    # Print summary
    if results_file.exists():
        print_summary(results_file)

    if args.update_baseline:
        if results_file.exists():
            shutil.copy2(results_file, BASELINE_PATH)
            print(f"\nBaseline updated: {BASELINE_PATH}")
        else:
            print("\nNo results to use as baseline.", file=sys.stderr)

    return result.returncode


def print_summary(results_path):
    records = []
    with open(results_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    if not records:
        return

    # Collect unique quickstarts and providers
    quickstarts = sorted(set(r["quickstart"] for r in records))
    providers = sorted(set(r["provider"] for r in records))

    # Build lookup
    lookup = {}
    for r in records:
        lookup[(r["quickstart"], r["provider"])] = r

    # Header
    provider_widths = [max(len(p), 4) for p in providers]
    qs_width = max(len(q) for q in quickstarts)
    header = f"{'Quickstart':<{qs_width}}"
    for p, w in zip(providers, provider_widths):
        header += f"  {p:^{w}}"
    print(f"\n{'=' * len(header)}")
    print("Evaluation Summary")
    print(f"{'=' * len(header)}")
    print(header)
    print("-" * len(header))

    pass_count = 0
    fail_count = 0
    for qs in quickstarts:
        row = f"{qs:<{qs_width}}"
        for p, w in zip(providers, provider_widths):
            r = lookup.get((qs, p))
            if r is None:
                cell = "-"
            elif r["success"] and r["valid"]:
                cell = "PASS"
                pass_count += 1
            else:
                cell = "FAIL"
                fail_count += 1
            row += f"  {cell:^{w}}"
        print(row)

    print("-" * len(header))
    print(f"Total: {pass_count} passed, {fail_count} failed")


def main():
    parser = argparse.ArgumentParser(description="Run QuickPat evaluation tests")
    parser.add_argument("--quickstart", help="Filter to a single quickstart (substring)")
    parser.add_argument("--provider", help="Filter to a single provider")
    parser.add_argument("--no-cache", action="store_true", help="Force re-clone repos")
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="Copy results to baseline.jsonl after run",
    )
    args = parser.parse_args()
    sys.exit(run_eval(args))


if __name__ == "__main__":
    main()
