"""Main orchestration loop for Proof Auditor.

Coordinates the multi-round audit process:
  Round 1: Translate proof → Lean (Translator Agent)
  Round 2: Compile + collect diagnostics (Lean LSP)
  Round 3: Classify each sorry (Diagnostician Agent)
  Round 4: Verify suspected errors (Verifier Agent)
  Round 5: Generate audit report
"""

import argparse
import json
import sys
from pathlib import Path

from core.classifier import AuditReport


def run_audit(input_path: str, output_path: str | None = None) -> AuditReport:
    """Run a complete proof audit.

    Args:
        input_path: Path to the proof file (LaTeX or plain text).
        output_path: Optional path to write the audit report JSON.

    Returns:
        The complete AuditReport.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"=== Proof Auditor v0.1.0 ===")
    print(f"Input: {input_file}")
    print()

    # Round 1: Translate
    print("[Round 1] Translating proof to Lean 4...")
    # TODO: Call Translator Agent
    print("  → Not yet implemented")
    print()

    # Round 2: Compile
    print("[Round 2] Compiling and collecting diagnostics...")
    # TODO: Call Lean LSP
    print("  → Not yet implemented")
    print()

    # Round 3: Classify
    print("[Round 3] Classifying sorry gaps...")
    # TODO: Call Diagnostician Agent
    print("  → Not yet implemented")
    print()

    # Round 4: Verify
    print("[Round 4] Verifying suspected errors...")
    # TODO: Call Verifier Agent
    print("  → Not yet implemented")
    print()

    # Round 5: Report
    print("[Round 5] Generating audit report...")
    report = AuditReport(
        proof_title=input_file.stem,
        total_sorrys=0,
        classifications=[],
        verdict="NOT_YET_IMPLEMENTED",
        summary="Proof Auditor is not yet fully implemented.",
    )

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # TODO: Proper serialization
        out.write_text(json.dumps({"verdict": report.verdict}, indent=2))
        print(f"  → Report written to {out}")

    print()
    print(f"Verdict: {report.verdict}")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Proof Auditor — AI-powered proof verification via sorry diagnosis"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the proof file (LaTeX or plain text)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to write the audit report (JSON)",
    )
    args = parser.parse_args()

    run_audit(args.input, args.output)


if __name__ == "__main__":
    main()
