"""Regression benchmark runner for Proof Auditor.

Runs all benchmark proofs and compares results against expected_results.json.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/run_benchmark.py
    PYTHONPATH=. .venv/bin/python scripts/run_benchmark.py --quick   # skip back-translation
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
BENCHMARK_DIR = ROOT_DIR / "benchmark"
EXPECTED_FILE = BENCHMARK_DIR / "expected_results.json"
REPORTS_DIR = ROOT_DIR / "reports"


def load_expected() -> dict:
    """Load expected results."""
    if not EXPECTED_FILE.exists():
        print(f"❌ Expected results file not found: {EXPECTED_FILE}")
        sys.exit(1)
    return json.loads(EXPECTED_FILE.read_text())


def find_benchmark_proofs() -> list[Path]:
    """Find all .txt benchmark proof files."""
    proofs = []
    for phase_dir in sorted(BENCHMARK_DIR.iterdir()):
        if phase_dir.is_dir() and phase_dir.name.startswith("phase"):
            proofs.extend(sorted(phase_dir.glob("*.txt")))
    # Also check root benchmark dir
    proofs.extend(sorted(BENCHMARK_DIR.glob("*.txt")))
    return proofs


def check_result(proof_name: str, report_data: dict, expected: dict) -> dict:
    """Check a single result against expectations.

    Returns dict with {passed: bool, checks: list[str], failures: list[str]}.
    """
    checks = []
    failures = []
    exp = expected.get(proof_name)

    if exp is None:
        return {"passed": True, "checks": ["⚠️  No expected results defined"], "failures": []}

    # Check verdict
    if "expected_verdict" in exp:
        actual_verdict = report_data.get("verdict", "")
        if exp["expected_verdict"] in actual_verdict:
            checks.append(f"✅ Verdict: {actual_verdict}")
        else:
            failures.append(
                f"❌ Verdict: expected '{exp['expected_verdict']}', got '{actual_verdict}'"
            )

    # Check classification types
    if "expected_types" in exp:
        actual_types = {c["type"] for c in report_data.get("classifications", [])}

        for must_type in exp["expected_types"].get("must_contain", []):
            if must_type in actual_types:
                checks.append(f"✅ Contains type {must_type}")
            else:
                failures.append(f"❌ Missing expected type {must_type} (got {actual_types})")

        for must_not in exp["expected_types"].get("must_not_contain", []):
            if must_not in actual_types:
                failures.append(f"❌ Contains unexpected type {must_not}")
            else:
                checks.append(f"✅ Doesn't contain type {must_not}")

    passed = len(failures) == 0
    return {"passed": passed, "checks": checks, "failures": failures}


def run_benchmarks(quick: bool = False) -> None:
    """Run all benchmarks and report results."""
    expected = load_expected()
    proofs = find_benchmark_proofs()

    if not proofs:
        print("❌ No benchmark proofs found!")
        return

    print(f"{'='*60}")
    print(f"  PROOF AUDITOR — Regression Benchmark")
    print(f"  Proofs: {len(proofs)}")
    print(f"  Mode: {'quick (no back-translation)' if quick else 'full'}")
    print(f"{'='*60}\n")

    results = {}
    total_passed = 0
    total_failed = 0

    for proof_path in proofs:
        proof_name = proof_path.stem
        print(f"\n{'─'*40}")
        print(f"  Testing: {proof_name}")
        print(f"{'─'*40}")

        # Check if report exists
        report_path = REPORTS_DIR / f"audit_{proof_name}.json"
        if not report_path.exists():
            print(f"  ⚠️  No report found. Run audit first:")
            mode = "off" if quick else "auto"
            print(f"  PYTHONPATH=. .venv/bin/python scripts/audit.py {proof_path} --mode {mode}")
            results[proof_name] = {"passed": False, "checks": [], "failures": ["No report"]}
            total_failed += 1
            continue

        # Load and check
        report_data = json.loads(report_path.read_text())
        result = check_result(proof_name, report_data, expected)
        results[proof_name] = result

        for check in result["checks"]:
            print(f"    {check}")
        for fail in result["failures"]:
            print(f"    {fail}")

        if result["passed"]:
            print(f"  ✅ PASSED")
            total_passed += 1
        else:
            print(f"  ❌ FAILED")
            total_failed += 1

    # Summary
    print(f"\n{'='*60}")
    print(f"  BENCHMARK RESULTS")
    print(f"{'='*60}")
    print(f"  Total: {len(proofs)}")
    print(f"  Passed: {total_passed}")
    print(f"  Failed: {total_failed}")
    print(f"  Skipped: {len(proofs) - total_passed - total_failed}")

    if total_failed > 0:
        print(f"\n  ❌ REGRESSION DETECTED")
        sys.exit(1)
    else:
        print(f"\n  ✅ ALL BENCHMARKS PASSED")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Proof Auditor regression benchmarks")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: skip back-translation")
    args = parser.parse_args()
    run_benchmarks(quick=args.quick)
