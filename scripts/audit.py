"""Proof Auditor — End-to-end audit pipeline.

Usage:
    PYTHONPATH=. .venv/bin/python scripts/audit.py <proof_file> [--mode off|auto|human|hybrid]

Examples:
    # Full audit with automatic back-translation check
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt --mode auto

    # Audit a specific theorem from a LaTeX file
    PYTHONPATH=. .venv/bin/python scripts/audit.py paper.tex --theorem thm:main

    # Human reviews the translation
    PYTHONPATH=. .venv/bin/python scripts/audit.py benchmark/phase0/buggy_proof.txt --mode human
"""

import argparse
import json
import re
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient, get_cost_tracker, reset_cost_tracker
from core.back_translator import BackTranslationMode, BackTranslationResult, run_back_translation
from core.diagnostician import run_full_audit
from core.latex_parser import extract_proof_block, parse_latex_file
from core.lean_lsp import LeanLSP
from core.translator_parser import parse_translator_output

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


def _print_graph_children(
    graph, node_id: str, prefix: str,
    type_map: dict, emoji_map: dict,
) -> None:
    """Recursively print graph children as indented tree."""
    children = graph.blocks(node_id)
    for i, child_id in enumerate(children):
        is_last = i == len(children) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = prefix + ("    " if is_last else "│   ")

        cls = type_map.get(child_id)
        if cls:
            emoji = emoji_map.get(cls.classification.value, "❓")
            label = f"{emoji} [{cls.classification.value}]"
        else:
            label = "⬜ [?]"

        impact = graph.nodes[child_id].impact_score if child_id in graph.nodes else 0
        impact_str = f" (impact: {impact})" if impact > 0 else ""
        print(f"{prefix}{connector}{label} {child_id}{impact_str}")
        _print_graph_children(graph, child_id, child_prefix, type_map, emoji_map)


def run_audit(proof_file: str, mode: str = "auto", theorem: Optional[str] = None) -> None:
    """Run full audit pipeline with B-loop retranslation support.

    Pipeline:
      R1    Translation (natural language → Lean 4)
      R1.5  Back-Translation verification + B-loop (up to 2 retries)
      R2    LSP Analysis (compile, extract sorry goals, try tactics)
      R2.5  Proof Graph Construction (static + AI dependency DAG)
      R3    AI Classification (Diagnostician Agent)
      R4    Verification (Verifier Agent for Type A suspects)
      R5    Report Generation
    """
    # Initialize cost tracker
    tracker = reset_cost_tracker()

    proof_path = Path(proof_file)

    # ── Handle LaTeX files ──
    if proof_path.suffix == ".tex":
        print(f"📄 LaTeX file detected: {proof_file}")
        proof_text = extract_proof_block(proof_file, label=theorem)
        if proof_text is None:
            # List available theorems
            result = parse_latex_file(proof_file)
            print(f"\n  Available theorems ({len(result.blocks)}):")
            for b in result.blocks:
                if b.env_type != "proof":
                    label = f" [\\label{{{b.label}}}]" if b.label else ""
                    name = f" ({b.name})" if b.name else ""
                    print(f"    • {b.env_type}{name}{label} (line {b.start_line})")
            print(f"\n  Use --theorem <label> to select one.")
            return
        proof_name = theorem or proof_path.stem
        print(f"  Extracted theorem: {theorem or '(first found)'}")
    else:
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
            tracker.set_round("R1_translation")
            lean_code = translate_proof(client, proof_text)
        else:
            print(f"\n🔄 Round 1 (B-loop retry {translation_attempt}/{MAX_RETRANSLATION}):"
                  f" Retranslating with targeted feedback...")
            tracker.set_round(f"R1_retry_{translation_attempt}")
            correction = build_correction_feedback(bt_result)
            lean_code = translate_proof(client, proof_text, correction_feedback=correction)

        # Parse translator metadata
        translator_meta = parse_translator_output(lean_code)
        if translator_meta.has_metadata:
            if translator_meta.introduced_assumptions:
                print(f"   ⚠️  Introduced assumptions: {len(translator_meta.introduced_assumptions)}")
                for ia in translator_meta.introduced_assumptions:
                    tag = "[INFRA]" if ia.is_infrastructure else "[MATH]"
                    print(f"      {tag} {ia.assumption}")
            if translator_meta.ambiguity_ledger:
                print(f"   📋 Ambiguity choices: {len(translator_meta.ambiguity_ledger)}")
                for ae in translator_meta.ambiguity_ledger:
                    print(f"      '{ae.term}' → {ae.choice}")

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

        tracker.set_round(f"R1.5_backtranslation_{translation_attempt}")
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
    tracker.set_round("R2_lsp_analysis")
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
    # Round 2.5: Proof Graph Construction
    # ==========================================
    from core.proof_graph import build_proof_graph

    print(f"\n🕸️  Round 2.5: Building Proof Dependency Graph...")
    proof_graph = build_proof_graph(
        client=client,
        lean_code=lean_code,
        sorry_diagnoses=sorry_diagnoses,
        original_proof=proof_text,
    )
    print(f"   Nodes: {len(proof_graph.nodes)}")
    print(f"   Edges: {len(proof_graph.edges)} "
          f"(static: {sum(1 for e in proof_graph.edges if e.source == 'static')}, "
          f"AI: {sum(1 for e in proof_graph.edges if e.source == 'ai')})")
    print(f"   Root nodes: {len(proof_graph.root_nodes)}")
    print(f"   Independent groups: {len(proof_graph.independent_groups)}")
    if proof_graph.critical_path:
        print(f"   Critical path: {' → '.join(proof_graph.critical_path)}")

    # Update sorry_diagnoses with graph info
    for diag in sorry_diagnoses:
        sid = f"sorry_L{diag['line']}"
        diag["blocked_by"] = proof_graph.blocked_by(sid)
        diag["is_blocked"] = len(diag["blocked_by"]) > 0
        if sid in proof_graph.nodes:
            diag["impact_score"] = proof_graph.nodes[sid].impact_score
            diag["depth"] = proof_graph.nodes[sid].depth

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

    # ── Proof Graph Tree Display ──
    emoji_map = {
        "A1": "🔴", "A2": "🟠", "B": "🟡", "C": "🟤",
        "D": "🟢", "E": "⚪", "F": "🔵", "G": "⬜",
    }
    type_map = {c.sorry.sorry_id: c for c in report.classifications}

    if proof_graph.edges:
        print("  ── Root Cause Tree ──")
        print()
        for root in proof_graph.root_nodes:
            cls = type_map.get(root.sorry_id)
            if cls:
                emoji = emoji_map.get(cls.classification.value, "❓")
                label = f"{emoji} [{cls.classification.value}]"
                conf = f" ({cls.confidence:.0%})"
            else:
                label = "❓"
                conf = ""
            print(f"  {label} {root.sorry_id}{conf} (impact: {root.impact_score})")
            _print_graph_children(proof_graph, root.sorry_id, "  │   ", type_map, emoji_map)
        print()

    # ── Flat classification detail ──
    roots = [c for c in report.classifications if c.classification.value != "G"]
    blocked = [c for c in report.classifications if c.classification.value == "G"]

    if roots:
        print("  ── Classification Detail ──")
        for cls in roots:
            emoji = emoji_map.get(cls.classification.value, "❓")
            salvage = " 🔧salvageable" if cls.salvageable else ""
            print(f"  {emoji} [{cls.classification.value}] Line {cls.sorry.line} "
                  f"(confidence: {cls.confidence:.0%}, risk: {cls.risk_score}){salvage}")
            goal_preview = cls.sorry.lean_goal.replace("\n", " ")[:80]
            print(f"     Goal: {goal_preview}")
            print(f"     Reason: {cls.reasoning[:120]}")

            if cls.counterexample:
                print(f"     🎯 Counterexample: {cls.counterexample[:120]}")
            if cls.salvageable and cls.alternative_proof:
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
                "risk_score": c.risk_score,
            }
            for c in report.classifications
        ],
        "cost": tracker.to_dict(),
        "proof_graph": proof_graph.to_dict(),
    }
    report_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"\n📄 Report saved: {report_json}")

    # Print cost summary
    print(f"\n{tracker.summary()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Proof Auditor — Audit mathematical proofs for logical errors"
    )
    parser.add_argument("proof_file", help="Path to the proof file (.txt or .tex)")
    parser.add_argument(
        "--mode",
        choices=["off", "auto", "human", "hybrid"],
        default="auto",
        help="Back-translation verification mode (default: auto)",
    )
    parser.add_argument(
        "--theorem",
        default=None,
        help="LaTeX label of theorem to audit (e.g., thm:main). Only for .tex files.",
    )
    args = parser.parse_args()

    run_audit(args.proof_file, mode=args.mode, theorem=args.theorem)
