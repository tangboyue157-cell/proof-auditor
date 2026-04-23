"""Main orchestration loop for Proof Auditor v4.

Coordinates the multi-round audit process:
  Round 0:   Parse input (LaTeX / PDF / plain text)
  Round 1:   Translate proof → Lean (Translator Agent)
  Round 1.5: Translation Quality Gate (Back-Translation check)
  Round 2:   Compile + collect diagnostics (Lean LSP)
  Round 3:   Classify each sorry (Diagnostician Agent)
  Round 4:   Verify suspected errors (Verifier Agent)
  Round 5:   Generate audit report

v4 changes:
  - Round 1.5 is now a quality gate, not a classification source
  - If fidelity < 0.7 after max retries → TRANSLATION_FAILED
  - 5-type A-E classification with [0,1] verification score
"""

import argparse
import json
import sys
from pathlib import Path

from core.classifier import AuditReport, Verdict


# Translation quality gate settings
FIDELITY_THRESHOLD = 0.7
MAX_TRANSLATION_RETRIES = 3


def run_audit(input_path: str, output_path: str | None = None) -> AuditReport:
    """Run a complete proof audit.

    Args:
        input_path: Path to the proof file (LaTeX, PDF, or plain text).
        output_path: Optional path to write the audit report JSON.

    Returns:
        The complete AuditReport.
    """
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    print(f"=== Proof Auditor v0.4.0 ===")
    print(f"Input: {input_file}")
    print()

    # Round 0: Parse input
    suffix = input_file.suffix.lower()
    if suffix == ".pdf":
        print("[Round 0] Extracting proof from PDF...")
        from core.pdf_extractor import extract_from_pdf
        pdf_result = extract_from_pdf(str(input_file))
        proof_text = pdf_result.get_proof_text()
        if not proof_text:
            print("  → No proof text extracted from PDF")
            sys.exit(1)
        print(f"  → Extracted {len(pdf_result.blocks)} blocks, "
              f"{len(proof_text)} chars")
    elif suffix == ".tex":
        print("[Round 0] Parsing LaTeX file...")
        from core.latex_parser import extract_proof_block
        proof_text = extract_proof_block(str(input_file))
        if not proof_text:
            print("  → No proof found in LaTeX file")
            sys.exit(1)
    else:
        proof_text = input_file.read_text()
    print()

    # Round 1: Translate
    print("[Round 1] Translating proof to Lean 4...")
    # TODO: Call Translator Agent
    print("  → Not yet implemented")
    print()

    # Round 1.5: Translation Quality Gate
    print("[Round 1.5] Translation Quality Gate...")
    fidelity_ok = False
    translation_attempts = 0

    for attempt in range(1, MAX_TRANSLATION_RETRIES + 1):
        translation_attempts = attempt
        print(f"  Attempt {attempt}/{MAX_TRANSLATION_RETRIES}...")
        # TODO: Run back-translation and fidelity check
        # fidelity_score = check_fidelity(proof_text, lean_code)
        fidelity_score = 1.0  # Placeholder until translation is implemented
        
        if fidelity_score >= FIDELITY_THRESHOLD:
            print(f"  → Fidelity {fidelity_score:.0%} ≥ {FIDELITY_THRESHOLD:.0%} — PASSED ✅")
            fidelity_ok = True
            break
        else:
            print(f"  → Fidelity {fidelity_score:.0%} < {FIDELITY_THRESHOLD:.0%} — FAILED")
            if attempt < MAX_TRANSLATION_RETRIES:
                print(f"  → Re-translating...")
                # TODO: Re-translate with feedback
    
    if not fidelity_ok:
        print(f"\n  ❌ Translation quality gate FAILED after {MAX_TRANSLATION_RETRIES} attempts.")
        print(f"  → Pipeline terminated. Verdict: TRANSLATION_FAILED")
        report = AuditReport(
            proof_title=input_file.stem,
            total_sorrys=0,
            classifications=[],
            verdict=Verdict.TRANSLATION_FAILED.value,
            summary="Translation fidelity check failed after maximum retries. "
                    "The audit pipeline was terminated because the Lean translation "
                    "does not faithfully represent the original proof.",
        )
        if output_path:
            _write_report(report, output_path)
        return report
    print()

    # Round 2: Compile
    print("[Round 2] Compiling and collecting diagnostics...")
    # TODO: Call Lean LSP
    print("  → Not yet implemented")
    print()

    # Round 3: Classify
    print("[Round 3] Classifying sorry gaps (5-type A-E)...")
    # TODO: Call Diagnostician Agent
    print("  → Not yet implemented")
    print()

    # Round 4: Verify
    print("[Round 4] Verifying suspected errors (Type A + C)...")
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
        _write_report(report, output_path)

    print()
    print(f"Verdict: {report.verdict}")
    return report


def _write_report(report: AuditReport, output_path: str) -> None:
    """Write audit report to JSON file."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    # TODO: Proper serialization with all fields
    out.write_text(json.dumps({"verdict": report.verdict}, indent=2))
    print(f"  → Report written to {out}")


def main():
    parser = argparse.ArgumentParser(
        description="Proof Auditor — AI-powered proof verification via sorry diagnosis"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the proof file (LaTeX, PDF, or plain text)",
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
