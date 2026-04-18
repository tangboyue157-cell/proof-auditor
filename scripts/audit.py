"""Proof Auditor — End-to-end audit pipeline.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/audit.py <proof_file> [--mode off|auto|human|hybrid]

Examples:
    # Full audit with automatic back-translation check
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt --mode auto

    # Skip back-translation (faster, less safe)
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt --mode off

    # Human reviews the translation
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt --mode human
"""

import argparse
import json
import re
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient
from core.back_translator import BackTranslationMode, BackTranslationResult, run_back_translation
from core.diagnostician import run_full_audit
from core.lean_lsp import LeanLSP

ROOT_DIR = Path(__file__).parent.parent
TRANSLATOR_PROMPT = (ROOT_DIR / "agents" / "translator.md").read_text()
WORKSPACE_DIR = ROOT_DIR / "ProofAuditor" / "Workspace"

# B-loop configuration
FIDELITY_THRESHOLD = 0.7   # Below this → trigger retranslation
MAX_RETRANSLATION = 2       # Maximum retranslation attempts


def extract_lean_code(resp: str) -> str:
    """Extract Lean code from AI response."""
    pattern = re.compile(r"```(?:lean4?)\s*\n(.*?)\n```", re.DOTALL)
    matches = pattern.findall(resp)
    if matches:
        return matches[0]
    return resp


def translate_proof(
    client: AIClient,
    proof_text: str,
    correction_feedback: Optional[str] = None,
) -> str:
    """Run Round 1: Translate proof to Lean 4.

    Args:
        client: AI client.
        proof_text: Original proof text.
        correction_feedback: If provided, this is a B-loop retry with specific
            feedback about what was wrong in the previous translation.

    Returns:
        Lean 4 code as string.
    """
    if correction_feedback:
        # B-loop retry: targeted retranslation prompt
        user_prompt = f"""{TRANSLATOR_PROMPT}

## ⚠️ RETRANSLATION REQUEST

Your previous translation was found to be UNFAITHFUL to the original proof.
The back-translation check detected the following discrepancies:

{correction_feedback}

## CRITICAL INSTRUCTIONS for this retry:
1. You MUST translate the original proof LITERALLY, even if the math is wrong.
2. If the original says "there exists an integer k such that a = 2k+1 AND b = 2k+1",
   you MUST use ONE variable k, NOT two separate variables.
3. If the original proof has a logical error, your Lean code should have the SAME error.
4. The sorry gaps are there to absorb errors — that is their purpose.
5. Do NOT "fix" or "improve" anything. Translate word-for-word as much as possible.

Here is the original proof to translate. Output ONLY the Lean 4 code inside ```lean ... ``` blocks.
Use `sorry` for every proof step and add comments mapping to original steps.
Include `import Mathlib` at the top.

---
{proof_text}
"""
    else:
        # First attempt: standard translation
        user_prompt = f"""{TRANSLATOR_PROMPT}

Here is the proof to translate. Output ONLY the Lean 4 code inside ```lean ... ``` blocks.
Use `sorry` for every proof step and add comments mapping to original steps.
Include `import Mathlib` at the top.

---
{proof_text}
"""

    client.system_prompt = TRANSLATOR_PROMPT
    resp = client.chat(user_prompt)
    return extract_lean_code(resp.content)


def print_bt_result(bt_result: BackTranslationResult) -> None:
    """Print back-translation result to terminal."""
    if bt_result.overall_match:
        print(f"   ✅ Translation fidelity: {bt_result.fidelity_score:.0%}")
        if bt_result.comparisons:
            mismatches = [c for c in bt_result.comparisons if not c.match]
            if mismatches:
                print(f"   ⚠️  {len(mismatches)} step(s) with discrepancies:")
                for m in mismatches:
                    print(f"      Step {m.step_id}: {m.discrepancy[:100]}")
            else:
                print(f"   ✅ All {len(bt_result.comparisons)} steps match")
    else:
        print(f"   🔴 Translation mismatch detected! Fidelity: {bt_result.fidelity_score:.0%}")
        for c in bt_result.comparisons:
            if not c.match:
                print(f"      ❌ Step {c.step_id}: {c.discrepancy[:120]}")

    if bt_result.requires_human:
        print(f"   👤 {bt_result.human_message}")


def build_correction_feedback(bt_result: BackTranslationResult) -> str:
    """Build targeted correction feedback from back-translation mismatches.

    This tells the Translator exactly WHAT was wrong, so it can fix
    specifically those steps without blindly retrying.
    """
    lines = []
    for c in bt_result.comparisons:
        if not c.match:
            lines.append(
                f"- STEP {c.step_id}: MISMATCH (confidence: {c.confidence:.0%})\n"
                f"  Original says: {c.original_text}\n"
                f"  But your Lean code says: {c.back_translated_text}\n"
                f"  Discrepancy: {c.discrepancy}\n"
            )
    if not lines:
        lines.append(
            f"- Overall fidelity score: {bt_result.fidelity_score:.0%} "
            "(below threshold). Please translate more literally."
        )
    return "\n".join(lines)


def run_audit(proof_file: str, mode: str = "auto") -> None:
    """Run full audit pipeline with B-loop retranslation support.

    Pipeline:
      R1    Translation (natural language → Lean 4)
      R1.5  Back-Translation verification + B-loop (up to 2 retries)
      R2    LSP Analysis (compile, extract sorry goals, try tactics)
      R3    AI Classification (Diagnostician Agent)
      R4    Verification (Verifier Agent for Type A suspects)
      R5    Report Generation
    """
    proof_path = Path(proof_file)
    proof_text = proof_path.read_text()
    proof_name = proof_path.stem
    bt_mode = BackTranslationMode(mode)

    print(f"{'='*60}")
    print(f"  PROOF AUDITOR — Full Audit Pipeline")
    print(f"  Input: {proof_file}")
    print(f"  Back-Translation Mode: {bt_mode.value}")
    print(f"{'='*60}")

    # Initialize AI client
    client = AIClient(provider="openai", model="gpt-5.4")

    # ==========================================
    # Round 1 + 1.5: Translation with B-loop
    # ==========================================
    lean_code = None
    bt_result = None
    translation_attempt = 0
    persistent_low_fidelity = False  # True if fidelity stays low after all retries

    while translation_attempt <= MAX_RETRANSLATION:
        # ── Round 1: Translate ──
        if translation_attempt == 0:
            print(f"\n🔄 Round 1: Translating proof to Lean 4...")
            lean_code = translate_proof(client, proof_text)
        else:
            print(f"\n🔄 Round 1 (B-loop retry {translation_attempt}/{MAX_RETRANSLATION}):"
                  f" Retranslating with targeted feedback...")
            correction = build_correction_feedback(bt_result)
            lean_code = translate_proof(client, proof_text, correction_feedback=correction)

        # Save Lean file
        lean_file = WORKSPACE_DIR / f"{proof_name}.lean"
        lean_file.parent.mkdir(parents=True, exist_ok=True)
        lean_file.write_text(lean_code)
        lean_rel = str(lean_file.relative_to(ROOT_DIR))
        print(f"   ✅ Translated to {lean_rel}")
        print(f"   Lines: {len(lean_code.splitlines())}")

        # ── Round 1.5: Back-Translation Check ──
        if bt_mode == BackTranslationMode.OFF:
            print(f"\n⏭️  Round 1.5: Back-Translation skipped (mode=off)")
            break  # No fidelity check → no B-loop

        print(f"\n🔁 Round 1.5: Back-Translation Verification ({bt_mode.value})...")
        bt_result = run_back_translation(
            client=client,
            original_proof=proof_text,
            lean_code=lean_code,
            mode=bt_mode,
        )

        if bt_result:
            print_bt_result(bt_result)

        # ── B-loop decision ──
        if bt_result and bt_result.fidelity_score >= FIDELITY_THRESHOLD:
            # Fidelity acceptable → exit loop, proceed to Round 2
            print(f"   ✅ Fidelity {bt_result.fidelity_score:.0%} ≥ {FIDELITY_THRESHOLD:.0%} → proceeding")
            break

        translation_attempt += 1

        if translation_attempt <= MAX_RETRANSLATION:
            print(f"\n   ⚠️  Fidelity {bt_result.fidelity_score:.0%} < {FIDELITY_THRESHOLD:.0%}"
                  f" → triggering B-loop (retry {translation_attempt}/{MAX_RETRANSLATION})")
        else:
            # Exhausted retries
            persistent_low_fidelity = True
            print(f"\n   🔶 Fidelity still {bt_result.fidelity_score:.0%} after "
                  f"{MAX_RETRANSLATION} retries.")
            print(f"   🔶 This likely indicates the ORIGINAL PROOF itself has issues")
            print(f"      (not just a translation problem). Continuing with diagnosis...")

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
            goal_preview = sg.goal.replace("\n", " ")[:60]
            print(f"   📌 Line {sg.line}: {goal_preview}...")

    # ==========================================
    # Rounds 3-5: AI Classification + Verification + Report
    # ==========================================
    print(f"\n🧠 Rounds 3-5: AI Classification & Verification...")

    fidelity = bt_result.fidelity_score if bt_result else None
    report = run_full_audit(
        client=client,
        original_proof=proof_text,
        lean_code=lean_code,
        sorry_diagnoses=sorry_diagnoses,
        proof_title=proof_name,
        fidelity_score=fidelity,
    )

    # ==========================================
    # Output Report
    # ==========================================
    print(f"\n{'='*60}")
    print(f"  AUDIT REPORT: {report.proof_title}")
    print(f"{'='*60}")
    print(f"  Verdict: {report.verdict}")
    print(f"  Total sorry gaps: {report.total_sorrys}")
    if bt_result:
        print(f"  Translation fidelity: {bt_result.fidelity_score:.0%}")
    print(f"  Translation attempts: {translation_attempt + 1}")
    if persistent_low_fidelity:
        print(f"  ⚠️  Persistent low fidelity → possible original proof errors")
    print()

    # Group: root causes first, then blocked descendants
    roots = [c for c in report.classifications if c.classification.value != "G"]
    blocked = [c for c in report.classifications if c.classification.value == "G"]

    emoji_map = {
        "A1": "🔴", "A2": "🟠", "B": "🟡", "C": "🟤",
        "D": "🟢", "E": "⚪", "F": "🔵", "G": "⬜",
    }

    if roots:
        print("  ── Root Cause Analysis ──")
        for cls in roots:
            emoji = emoji_map.get(cls.classification.value, "❓")
            salvage = " 🔧salvageable" if cls.salvageable else ""
            print(f"  {emoji} [{cls.classification.value}] Line {cls.sorry.line} "
                  f"(confidence: {cls.confidence:.0%}){salvage}")
            goal_preview = cls.sorry.lean_goal.replace("\n", " ")[:80]
            print(f"     Goal: {goal_preview}")
            print(f"     Reason: {cls.reasoning[:120]}")

            if cls.counterexample:
                print(f"     🎯 Counterexample: {cls.counterexample[:120]}")
            if cls.alternative_proof:
                print(f"     🔧 Alt proof: {cls.alternative_proof[:100]}")
            print()

    if blocked:
        print(f"  ── Blocked Descendants ({len(blocked)}) ──")
        for cls in blocked:
            print(f"  ⬜ [G] Line {cls.sorry.line} ← blocked by {cls.sorry.blocked_by}")
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
        "back_translation": {
            "mode": bt_mode.value,
            "fidelity_score": bt_result.fidelity_score if bt_result else None,
            "overall_match": bt_result.overall_match if bt_result else None,
            "translation_attempts": translation_attempt + 1,
            "persistent_low_fidelity": persistent_low_fidelity,
            "flagged_steps": [
                {
                    "step_id": c.step_id,
                    "discrepancy": c.discrepancy,
                    "confidence": c.confidence,
                }
                for c in (bt_result.comparisons if bt_result else [])
                if not c.match
            ],
        },
        "root_causes": report.root_causes,
        "blocked_descendants": report.blocked_count,
        "classifications": [
            {
                "sorry_id": c.sorry.sorry_id,
                "line": c.sorry.line,
                "goal": c.sorry.lean_goal,
                "type": c.classification.value,
                "confidence": c.confidence,
                "reasoning": c.reasoning,
                "blocked_by": c.sorry.blocked_by,
                "salvageable": c.salvageable,
                "counterexample": c.counterexample,
            }
            for c in report.classifications
        ],
    }
    report_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"\n📄 Report saved: {report_json}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Proof Auditor — Audit mathematical proofs for logical errors"
    )
    parser.add_argument("proof_file", help="Path to the proof file (.txt)")
    parser.add_argument(
        "--mode",
        choices=["off", "auto", "human", "hybrid"],
        default="auto",
        help="Back-translation verification mode (default: auto)",
    )
    args = parser.parse_args()

    run_audit(args.proof_file, mode=args.mode)
