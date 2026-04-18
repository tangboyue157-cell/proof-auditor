"""Proof Auditor — End-to-end audit pipeline.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/audit.py <proof_file>

Example:
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt
"""

import json
import sys
import re
from pathlib import Path

from core.ai_client import AIClient
from core.lean_lsp import LeanLSP
from core.diagnostician import run_full_audit

ROOT_DIR = Path(__file__).parent.parent
TRANSLATOR_PROMPT = (ROOT_DIR / "agents" / "translator.md").read_text()
WORKSPACE_DIR = ROOT_DIR / "ProofAuditor" / "Workspace"


def extract_lean_code(resp: str) -> str:
    """Extract Lean code from AI response."""
    pattern = re.compile(r"```(?:lean4?)\s*\n(.*?)\n```", re.DOTALL)
    matches = pattern.findall(resp)
    if matches:
        return matches[0]
    return resp


def run_audit(proof_file: str) -> None:
    """Run full 5-round audit on a proof file."""
    proof_path = Path(proof_file)
    proof_text = proof_path.read_text()
    proof_name = proof_path.stem

    print(f"{'='*60}")
    print(f"  PROOF AUDITOR — Full Audit Pipeline")
    print(f"  Input: {proof_file}")
    print(f"{'='*60}")

    # Initialize AI client
    client = AIClient(provider="openai", model="gpt-5.4")

    # ==========================================
    # Round 1: Translation
    # ==========================================
    print(f"\n🔄 Round 1: Translating proof to Lean 4...")

    translate_prompt = f"""{TRANSLATOR_PROMPT}

Here is the proof to translate. Output ONLY the Lean 4 code inside ```lean ... ``` blocks.
Use `sorry` for every proof step and add comments mapping to original steps.
Include `import Mathlib` at the top.

---
{proof_text}
"""
    client.system_prompt = TRANSLATOR_PROMPT
    resp = client.chat(translate_prompt)
    lean_code = extract_lean_code(resp.content)

    # Save Lean file
    lean_file = WORKSPACE_DIR / f"{proof_name}.lean"
    lean_file.parent.mkdir(parents=True, exist_ok=True)
    lean_file.write_text(lean_code)
    lean_rel = str(lean_file.relative_to(ROOT_DIR))

    print(f"   ✅ Translated to {lean_rel}")
    print(f"   Lines: {len(lean_code.splitlines())}")

    # ==========================================
    # Round 2: LSP Analysis
    # ==========================================
    print(f"\n🔍 Round 2: Analyzing with Lean LSP...")

    with LeanLSP() as lsp:
        analysis = lsp.analyze_file(lean_rel)

        print(f"   Compiles: {analysis.compiles}")
        print(f"   Errors: {len(analysis.errors)}")
        print(f"   Sorry goals: {len(analysis.sorry_goals)}")

        # Get full diagnosis for each sorry
        sorry_diagnoses = []
        for sg in analysis.sorry_goals:
            diagnosis = lsp.diagnose_sorry(lean_rel, sg.line, sg.column)
            sorry_diagnoses.append(diagnosis)
            print(f"   📌 Line {sg.line}: {sg.goal[:80]}...")

    # ==========================================
    # Rounds 3-5: AI Classification + Verification + Report
    # ==========================================
    print(f"\n🧠 Rounds 3-5: AI Classification & Verification...")

    report = run_full_audit(
        client=client,
        original_proof=proof_text,
        lean_code=lean_code,
        sorry_diagnoses=sorry_diagnoses,
        proof_title=proof_name,
    )

    # ==========================================
    # Output Report
    # ==========================================
    print(f"\n{'='*60}")
    print(f"  AUDIT REPORT: {report.proof_title}")
    print(f"{'='*60}")
    print(f"  Verdict: {report.verdict}")
    print(f"  Total sorry gaps: {report.total_sorrys}")
    print()

    for cls in report.classifications:
        emoji = {"A": "🔴", "B": "🟡", "C": "🟠", "D": "🟢", "E": "⚪"}.get(
            cls.classification.value, "❓"
        )
        print(f"  {emoji} [{cls.classification.value}] Line {cls.sorry.line} "
              f"(confidence: {cls.confidence:.0%})")
        print(f"     Goal: {cls.sorry.lean_goal[:80]}")
        print(f"     Reason: {cls.reasoning[:120]}")

        # Show counterexample if found
        verification = cls.evidence.get("verification")
        if verification and verification.get("counterexample_found"):
            print(f"     🎯 Counterexample: {verification['counterexample'][:120]}")
        print()

    print(f"{'='*60}")
    print(f"  {report.summary}")
    print(f"{'='*60}")

    # Save report as JSON
    report_dir = ROOT_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_json = report_dir / f"audit_{proof_name}.json"

    report_data = {
        "proof_title": report.proof_title,
        "verdict": report.verdict,
        "total_sorrys": report.total_sorrys,
        "classifications": [
            {
                "sorry_id": c.sorry.sorry_id,
                "line": c.sorry.line,
                "goal": c.sorry.lean_goal,
                "type": c.classification.value,
                "confidence": c.confidence,
                "reasoning": c.reasoning,
            }
            for c in report.classifications
        ],
    }
    report_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"\n📄 Report saved: {report_json}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: PYTHONPATH=. .venv/bin/python scripts/audit.py <proof_file>")
        print("Example: PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt")
        sys.exit(1)

    run_audit(sys.argv[1])
