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
from core.classifier import SorryType
from core.diagnostician import run_full_audit
from core.forward_translator import forward_translate, ForwardTranslationResult
from core.latex_parser import extract_proof_block, parse_latex_file
from core.mathlib_reference import build_reference_context
from core.pdf_extractor import extract_from_pdf, list_pdf_theorems
from core.reference_extractor import extract_reference_context
from core.lean_lsp import LeanLSP
from core.translator_parser import parse_translator_output

ROOT_DIR = Path(__file__).parent.parent
WORKSPACE_DIR = ROOT_DIR / "ProofAuditor" / "Workspace"

# B-loop configuration
FIDELITY_THRESHOLD = 0.7   # Below this → trigger retranslation
MAX_RETRANSLATION = 3       # Maximum retranslation attempts


def extract_lean_code(resp: str) -> str:
    """Extract Lean code from AI response."""
    pattern = re.compile(r"```(?:lean4?)\s*\n(.*?)\n```", re.DOTALL)
    matches = pattern.findall(resp)
    if matches:
        return matches[0]
    return resp


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


def run_audit(
    proof_file: str,
    mode: str = "auto",
    theorem: Optional[str] = None,
    backend: str = "pymupdf",
    pages: Optional[str] = None,
    refs: Optional[list[str]] = None,
) -> None:
    """Run full audit pipeline with B-loop retranslation support.

    Pipeline:
      R0    Input parsing (LaTeX / PDF / plain text)
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

    # ── Handle PDF files ──
    if proof_path.suffix.lower() == ".pdf":
        print(f"📄 PDF file detected: {proof_file}")
        pdf_result = extract_from_pdf(
            proof_file, backend=backend, theorem=theorem, pages=pages,
        )
        proof_text = pdf_result.get_proof_text(theorem_name=theorem)
        if proof_text is None:
            list_pdf_theorems(proof_file)
            print(f"\n  Use --theorem <name> to select one, or the full text will be used.")
            return
        proof_name = theorem or proof_path.stem
        if pdf_result.has_blocks:
            print(f"  Using theorem: {theorem or '(first found)'}")
        else:
            print(f"  Using raw extracted text ({len(proof_text)} chars)")
        if pdf_result.extraction_warnings:
            for w in pdf_result.extraction_warnings:
                print(f"  ⚠️  {w}")

    # ── Handle LaTeX files ──
    elif proof_path.suffix == ".tex":
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

    # ── R0.5: Extract reference documents ──
    reference_context = ""
    if refs:
        print(f"\n📚 Extracting reference documents ({len(refs)} files)...")
        reference_context = extract_reference_context(refs)
        if reference_context:
            print(f"  ✅ Reference context: {len(reference_context)} chars")
        else:
            print(f"  ⚠️  No usable content extracted from references")

    print(f"{'='*60}")
    print(f"  PROOF AUDITOR — Full Audit Pipeline")
    print(f"  Input: {proof_file}")
    print(f"  Back-Translation Mode: {bt_mode.value}")
    if refs:
        print(f"  References: {len(refs)} documents")
    print(f"{'='*60}")

    import os
    provider = os.environ.get("PA_UI_PROVIDER", "openai")
    model_name = os.environ.get("PA_UI_MODEL", "")
    
    kwargs = {"provider": provider}
    if model_name:
        kwargs["model"] = model_name
    elif provider == "openai":
        kwargs["model"] = "gpt-5.4" # legacy fallback
        
    client = AIClient(**kwargs)

    # ==========================================
    # Round 1 + 1.5: Translation with B-loop
    # ==========================================
    lean_code = None
    bt_result = None
    translation_attempt = 0
    persistent_low_fidelity = False  # True if fidelity stays low after all retries

    # ── Round 1: Structured Forward Translation ──
    print(f"\n📝 Round 1: Structured Forward Translation...")
    tracker.set_round("R1_forward_translate")
    ft_result = forward_translate(
        client=client,
        original_proof=proof_text,
        theorem_name=proof_name,
    )
    lean_code = ft_result.lean_code

    # Print translation diagnostics
    if ft_result.mathlib_context:
        from core.mathlib_reference import detect_domains
        domains = detect_domains(proof_text)
        print(f"   Mathlib domains: {', '.join(domains[:3]) if domains else '(none)'}")
    print(f"   Plan: {len(ft_result.plan.proof_steps)} steps, "
          f"{len(ft_result.plan.binders)} binders, "
          f"{len(ft_result.plan.ambiguities)} ambiguities")
    if ft_result.plan.introduced_assumptions:
        print(f"   ⚠️  Introduced assumptions: {ft_result.plan.introduced_assumptions}")
    for issue in ft_result.issues:
        prefix = {"info": "ℹ️", "warning": "⚠️", "fatal": "🔴"}.get(issue.severity, "•")
        print(f"   {prefix} [{issue.code}] {issue.message}")
    if ft_result.fatal_issues:
        print(f"   🔴 Fatal issues detected — translation may be incomplete")
    print(f"   ✅ Lean code: {len(lean_code.splitlines())} lines (deterministic render)")

    while translation_attempt <= MAX_RETRANSLATION:

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

        # ── Round 1+ : Static Structure Analysis ──
        from core.proof_structure import analyze_proof_structure

        proof_structure = analyze_proof_structure(lean_code)
        print(f"\n📐 Round 1+: Static Proof Structure Analysis")
        print(f"   Theorem: {proof_structure.theorem_name}")
        print(f"   Strategy: {proof_structure.proof_strategy}")
        print(f"   Steps: {len(proof_structure.steps)} ({proof_structure.sorry_count} with sorry)")
        print(f"   Static edges: {len(proof_structure.edges)}")
        if proof_structure.root_steps:
            print(f"   Root steps: {', '.join(proof_structure.root_steps[:5])}")
        if proof_structure.critical_chain:
            chain = ' → '.join(proof_structure.critical_chain[:6])
            print(f"   Critical chain: {chain}")

        # ── Round 1.5: Back-Translation Check ──
        if bt_mode == BackTranslationMode.OFF:
            print(f"\n⏭️  Round 1.5: Back-Translation skipped (mode=off)")
            break  # No fidelity check → no B-loop

        if bt_mode == BackTranslationMode.WEB:
            # Web mode: AI back-translates AND compares (for reference score),
            # then pauses for human decision via web UI.
            tracker.set_round(f"R1.5_backtranslation_{translation_attempt}")
            print(f"\n🔁 Round 1.5: Back-Translation + AI Comparison (Human Review)...")

            # Step 0: Extract proof skeleton for structured output
            from core.back_translator import back_translate, compare_auto, extract_proof_skeleton
            skeleton = extract_proof_skeleton(lean_code)
            if skeleton:
                print(f"   📋 Proof skeleton: {len(skeleton)} steps extracted")

            # Step 1: Back-translate with skeleton guidance
            bt_text = back_translate(client, lean_code, skeleton=skeleton)

            # Step 2: AI comparison for reference score
            auto_result = compare_auto(client, proof_text, bt_text)
            bt_result = auto_result
            # Override: store the back-translated text explicitly
            bt_result.back_translation = bt_text

            print_bt_result(bt_result)

            # Step 3: Emit signal for web UI with comparison data
            review_data = {
                "original_proof": proof_text,
                "back_translated_text": bt_text,
                "fidelity_score": bt_result.fidelity_score,
                "overall_match": bt_result.overall_match,
                "flagged_steps": [
                    {"step_id": c.step_id, "discrepancy": c.discrepancy}
                    for c in bt_result.comparisons if not c.match
                ],
                "attempt": translation_attempt + 1,
            }
            import json as _json
            print(f"__HUMAN_REVIEW__:{_json.dumps(review_data, ensure_ascii=False)}")
            print(f"\n⏸️  Waiting for human decision via web UI...")
            print(f"   AI reference fidelity: {bt_result.fidelity_score:.0%}")
            print(f"   Options: [Approve] [Retry Translation] [Abort]")

            # Step 4: Poll for decision file
            import time
            audit_id = os.environ.get("PA_AUDIT_ID", "")
            decision_file = WORKSPACE_DIR / f".decision_{audit_id}.json"
            # Clean up any stale decision file
            if decision_file.exists():
                decision_file.unlink()

            decision = None
            while True:
                if decision_file.exists():
                    try:
                        decision = json.loads(decision_file.read_text())
                        decision_file.unlink()  # Clean up
                        break
                    except Exception:
                        pass
                time.sleep(1)

            action = decision.get("action", "approve")
            feedback = decision.get("feedback", "")

            if action == "approve":
                print(f"\n   ✅ Human approved translation — proceeding to Round 2")
                bt_result.requires_human = True
                bt_result.human_message = "Human approved via web UI."
                break
            elif action == "retry":
                print(f"\n   🔄 Human requested retranslation")
                if feedback:
                    print(f"   📝 Feedback: {feedback}")
                translation_attempt += 1
                if translation_attempt > MAX_RETRANSLATION:
                    persistent_low_fidelity = True
                    print(f"\n   🔶 Max retries reached. Continuing with current translation...")
                    break
                continue  # Re-enter while loop for B-loop retry
            elif action == "abort":
                print(f"\n   ⛔ Human aborted audit")
                raise SystemExit(1)

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
    # Round 2.1: Plan-Goal Alignment
    # ==========================================
    from core.plan_goal_alignment import align_plan_with_goals

    alignment_result = None
    if ft_result and ft_result.plan.proof_steps:
        tracker.set_round("R2.1_plan_goal_alignment")
        print(f"\n🎯 Round 2.1: Plan-Goal Alignment...")
        alignment_result = align_plan_with_goals(
            plan=ft_result.plan,
            sorry_goals=analysis.sorry_goals,
            lean_code=lean_code,
        )
        print(f"   Overall alignment: {alignment_result.overall_score:.0%}")
        print(f"   Structural match: {alignment_result.structural_match}")
        for a in alignment_result.alignments:
            icon = {"exact": "✅", "alpha_equiv": "✅", "minor_diff": "⚠️",
                    "major_diff": "🔴", "unaligned": "❓"}.get(a.alignment_type, "•")
            print(f"   {icon} {a.step_id}: {a.alignment_type} ({a.alignment_score:.0%})")
        if alignment_result.unmatched_plan_steps:
            print(f"   ⚠️  Unmatched plan steps: {', '.join(alignment_result.unmatched_plan_steps)}")
        if alignment_result.unmatched_goals:
            print(f"   ⚠️  Unmatched LSP goals: {', '.join(alignment_result.unmatched_goals)}")
    else:
        print(f"\n⏭️  Round 2.1: Plan-Goal Alignment skipped (no plan steps)")

    # ==========================================
    # Round 2.5: Proof Graph Construction
    # ==========================================
    from core.proof_graph import build_proof_graph

    print(f"\n🕸️  Round 2.5: Building Proof Dependency Graph...")
    print(f"   (seeded with {len(proof_structure.edges)} static edges from R1+)")
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

    # Update sorry_diagnoses with graph + structure info
    for diag in sorry_diagnoses:
        sid = f"sorry_L{diag['line']}"
        diag["blocked_by"] = proof_graph.blocked_by(sid)
        diag["is_blocked"] = len(diag["blocked_by"]) > 0

        # Enrich with R1+ structural context
        matching_step = None
        for s in proof_structure.steps:
            if s.line == diag["line"] or (s.has_sorry and abs(s.line - diag["line"]) <= 2):
                matching_step = s
                break
        if matching_step:
            diag["structure"] = {
                "step_name": matching_step.name,
                "depth": matching_step.depth,
                "claimed_reason": matching_step.claimed_reason,
                "references": matching_step.references,
                "is_root": matching_step.name in proof_structure.root_steps,
                "is_leaf": matching_step.name in proof_structure.leaf_steps,
                "upstream_count": len(proof_structure.upstream_of(matching_step.name)),
                "downstream_count": len(proof_structure.downstream_of(matching_step.name)),
            }
        if sid in proof_graph.nodes:
            diag["impact_score"] = proof_graph.nodes[sid].impact_score
            diag["depth"] = proof_graph.nodes[sid].depth

        # Enrich with R2.1 plan-goal alignment
        if alignment_result:
            for a in alignment_result.alignments:
                if a.sorry_line and abs(a.sorry_line - diag["line"]) <= 2:
                    diag["plan_goal_alignment"] = {
                        "step_id": a.step_id,
                        "plan_claim": a.plan_claim,
                        "lean_goal": a.lean_goal,
                        "alignment_score": a.alignment_score,
                        "alignment_type": a.alignment_type,
                        "issues": a.issues,
                    }
                    break

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
        reference_context=reference_context,
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
        "A": "🔴", "B": "🟢", "C": "🟠", "D": "🔵", "E": "⚪",
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
    roots = [c for c in report.classifications if c.classification != SorryType.E_INDETERMINATE or True]
    blocked = []  # E_INDETERMINATE with blocked_by are the blocked descendants
    for c in report.classifications:
        if c.sorry.blocked_by and c.classification == SorryType.E_INDETERMINATE:
            blocked.append(c)
            roots = [r for r in roots if r.sorry.sorry_id != c.sorry.sorry_id]

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
            print(f"  ⚪ [E] Line {cls.sorry.line} ← blocked by {cls.sorry.blocked_by}")
        print()

    print(f"{'='*60}")
    print(f"  {report.summary}")
    print(f"{'='*60}")

    # ==========================================
    # Round 6: Adjudicator — Independent Final Review
    # ==========================================
    from core.narrator import adjudicate

    tracker.set_round("R6_adjudication")
    print(f"\n⚖️  Round 6: Independent Adjudication...")
    print(f"   (Adjudicator reviews ALL evidence independently)")

    adjudication = adjudicate(
        client=client,
        original_proof=proof_text,
        lean_code=lean_code,
        report=report,
        fidelity_score=bt_result.fidelity_score if bt_result else None,
        proof_structure_summary=proof_structure.summary() if proof_structure else None,
    )

    # Display overrides
    if adjudication.has_overrides:
        print(f"\n   ⚠️  OVERRIDES DETECTED:")
        for o in adjudication.overrides:
            if o.override:
                print(f"   🔄 {o.sorry_id}: {o.original_type} → {o.final_type}")
                print(f"      {o.review_note}")
    else:
        print(f"   ✅ All classifications confirmed by Adjudicator")

    # Show final verdict (may differ from Diagnostician's)
    diag_verdict = report.verdict
    adj_verdict = adjudication.final_verdict
    if adj_verdict != diag_verdict:
        print(f"\n   🔄 Verdict changed: {diag_verdict} → {adj_verdict}")
    print(f"   Final verdict: {adj_verdict} (confidence: {adjudication.confidence:.0%})")

    # Display narrative
    print(f"\n{'─'*60}")
    print(f"  📖 FINAL REPORT (for the mathematician)")
    print(f"{'─'*60}")
    print()
    print(adjudication.narrative)
    print()
    print(f"{'─'*60}")

    # Save report as JSON
    report_dir = ROOT_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_json = report_dir / f"audit_{proof_name}.json"

    report_data = {
        "proof_title": report.proof_title,
        "diagnostician_verdict": report.verdict,
        "final_verdict": adjudication.final_verdict,
        "adjudicator_confidence": adjudication.confidence,
        "total_sorrys": report.total_sorrys,
        "adjudication": adjudication.to_dict(),
        "back_translation": {
            "mode": bt_mode.value,
            "fidelity_score": bt_result.fidelity_score if bt_result else None,
            "overall_match": bt_result.overall_match if bt_result else None,
            "translation_attempts": translation_attempt + 1,
            "persistent_low_fidelity": persistent_low_fidelity,
            "back_translated_text": bt_result.back_translation if bt_result else None,
            "original_proof": proof_text if bt_result else None,
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
                "verification_score": c.verification_score,
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
        "proof_structure": proof_structure.to_dict(),
        "plan_goal_alignment": {
            "overall_score": alignment_result.overall_score if alignment_result else None,
            "structural_match": alignment_result.structural_match if alignment_result else None,
            "step_count_plan": alignment_result.step_count_plan if alignment_result else 0,
            "step_count_goals": alignment_result.step_count_goals if alignment_result else 0,
            "alignments": [
                {
                    "step_id": a.step_id,
                    "plan_claim": a.plan_claim,
                    "lean_goal": a.lean_goal,
                    "alignment_score": a.alignment_score,
                    "alignment_type": a.alignment_type,
                    "issues": a.issues,
                }
                for a in (alignment_result.alignments if alignment_result else [])
            ],
            "unmatched_plan_steps": alignment_result.unmatched_plan_steps if alignment_result else [],
            "unmatched_goals": alignment_result.unmatched_goals if alignment_result else [],
        } if alignment_result else None,
    }
    report_json.write_text(json.dumps(report_data, indent=2, ensure_ascii=False))
    print(f"\n📄 Report saved: {report_json}")

    # Print cost summary
    print(f"\n{tracker.summary()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Proof Auditor — Audit mathematical proofs for logical errors"
    )
    parser.add_argument("proof_file", help="Path to the proof file (.txt, .tex, or .pdf)")
    parser.add_argument(
        "--mode",
        choices=["off", "auto", "human", "hybrid", "web"],
        default="auto",
        help="Back-translation verification mode (default: auto)",
    )
    parser.add_argument(
        "--theorem",
        default=None,
        help="Theorem to audit (label for .tex, name/number for .pdf).",
    )
    parser.add_argument(
        "--backend",
        choices=["pymupdf", "ai-enhance", "vision"],
        default="pymupdf",
        help="PDF extraction backend (default: pymupdf, zero API cost). "
             "ai-enhance uses AI to restore LaTeX. "
             "vision renders pages as images for scanned PDFs (highest cost).",
    )
    parser.add_argument(
        "--pages",
        default=None,
        help="Page range for PDF extraction (e.g., '1-5,8'). Only for .pdf files.",
    )
    parser.add_argument(
        "--refs",
        nargs='*',
        default=None,
        help="Reference PDF files cited by the proof (e.g., --refs ref1.pdf ref2.pdf). "
             "Theorems are extracted via AI and provided to the Diagnostician and Verifier.",
    )
    args = parser.parse_args()

    run_audit(
        args.proof_file,
        mode=args.mode,
        theorem=args.theorem,
        backend=args.backend,
        pages=args.pages,
        refs=args.refs,
    )
