"""Diagnostician module v4 — [0,1] verification score model.

Key changes from v2/v3:
  1. 5-type classification (A-E) replacing 8-type (A1-G)
  2. Verification score s ∈ [0,1] for every classification
  3. Tactic success → Type B (Verified, s=1.0), not old Type D
  4. Blocked descendants → Type E (Indeterminate)
  5. Translation errors handled at pipeline quality gate, not here
  6. Verifier targets Type A (counterexample) and Type C (reasoning check)
"""

import json
import re
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient
from core.classifier import (
    AuditReport,
    Fidelity,
    InternalAxes,
    JustificationStatus,
    MechanizationStatus,
    ProvenanceStatus,
    SorryClassification,
    SorryGap,
    SorryType,
    Verdict,
    generate_report,
)

AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _load_prompt(name: str) -> str:
    path = AGENTS_DIR / f"{name}.md"
    return path.read_text() if path.exists() else ""


# ── Sorry Dependency Analysis ───────────────────────────────


def build_dependency_graph(
    lean_code: str,
    sorry_diagnoses: list[dict],
) -> list[dict]:
    """Analyze sorry dependencies from Lean code structure.

    A sorry at line N is "blocked_by" a sorry at line M if:
      - Line N's goal references a hypothesis introduced by a have/let
        that is proved by sorry at line M
      - i.e., the sorry at M introduces an assumption that N depends on

    This is approximated by scanning the Lean code for `have` / `obtain`
    blocks that contain sorry, and checking if later sorrys use those names.
    """
    lines = lean_code.splitlines()
    sorry_lines = {d["line"]: d for d in sorry_diagnoses}

    # Find all hypotheses introduced by sorry-containing blocks
    sorry_hypotheses: dict[int, list[str]] = {}  # sorry_line -> [hypothesis_names]
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Match: have h : ... := by\n    sorry
        have_match = re.match(r'have\s+(\w+)\s*:', stripped)
        obtain_match = re.match(r'obtain\s+⟨([^⟩]+)⟩', stripped)

        if have_match:
            hyp_name = have_match.group(1)
            # Check if a sorry follows within the next few lines
            for j in range(i, min(i + 5, len(lines) + 1)):
                if j in sorry_lines:
                    sorry_hypotheses.setdefault(j, []).append(hyp_name)
                    break

        if obtain_match:
            hyp_names = [h.strip() for h in obtain_match.group(1).split(",")]
            for j in range(i, min(i + 5, len(lines) + 1)):
                if j in sorry_lines:
                    sorry_hypotheses.setdefault(j, []).extend(hyp_names)
                    break

    # Now check which sorry goals reference hypotheses from earlier sorrys
    for diag in sorry_diagnoses:
        line = diag["line"]
        goal = diag.get("goal", "")
        blocked_by = []

        for sorry_line, hyp_names in sorry_hypotheses.items():
            if sorry_line >= line:
                continue  # Only look at earlier sorrys
            for hyp in hyp_names:
                if hyp in goal:
                    blocked_by.append(f"sorry_L{sorry_line}")

        diag["blocked_by"] = blocked_by
        diag["is_blocked"] = len(blocked_by) > 0

    return sorry_diagnoses


# ── AI Classification (Round 3) ─────────────────────────────


def classify_sorry_with_ai(
    client: AIClient,
    sorry_data: dict,
    original_proof: str,
    lean_code: str,
    fidelity_score: Optional[float] = None,
    reference_context: str = "",
) -> SorryClassification:
    """Classify a single sorry using the Diagnostician Agent v4.

    v4 changes:
      - 5-type A-E classification with verification score
      - Tactic success → Type B (Verified, s=1.0)
      - Blocked descendants → Type E (Indeterminate)
      - Translation errors handled at quality gate, not here
    """
    # ── Fast path: blocked descendant → Type E ──
    if sorry_data.get("is_blocked"):
        gap = SorryGap(
            sorry_id=f"sorry_L{sorry_data['line']}",
            file=sorry_data.get("file", ""),
            line=sorry_data["line"],
            lean_goal=sorry_data.get("goal", ""),
            blocked_by=sorry_data.get("blocked_by", []),
        )
        axes = InternalAxes(provenance=ProvenanceStatus.BLOCKED_DESCENDANT)
        return SorryClassification(
            sorry=gap,
            classification=SorryType.E_INDETERMINATE,
            confidence=0.9,
            verification_score=0.5,
            reasoning=(
                f"This sorry depends on upstream sorry(s) {sorry_data['blocked_by']}. "
                "It cannot be independently classified until root causes are resolved."
            ),
            internal_axes=axes,
        )

    # ── Fast path: tactic solved it → Type B (Verified, s=1.0) ──
    tactic_results = sorry_data.get("tactic_results", [])
    solved_tactics = [tr for tr in tactic_results if tr.get("success")]
    if solved_tactics:
        gap = SorryGap(
            sorry_id=f"sorry_L{sorry_data['line']}",
            file=sorry_data.get("file", ""),
            line=sorry_data["line"],
            lean_goal=sorry_data.get("goal", ""),
        )
        solved_by = ", ".join(tr["tactic"] for tr in solved_tactics)
        axes = InternalAxes(mechanization=MechanizationStatus.API_FOUND)
        return SorryClassification(
            sorry=gap,
            classification=SorryType.B_VERIFIED,
            confidence=1.0,
            verification_score=1.0,
            reasoning=f"Auto-resolved by tactic(s): {solved_by}",
            evidence={"tactic_results": tactic_results},
            internal_axes=axes,
        )

    # ── Build structural context string for prompt ──
    struct = sorry_data.get("structure", {})
    structure_block = ""
    if struct:
        is_root = struct.get("is_root", False)
        is_leaf = struct.get("is_leaf", False)
        position = "ROOT" if is_root else ("LEAF" if is_leaf else "INTERMEDIATE")
        depth = struct.get("depth", 0)
        upstream = struct.get("upstream_count", 0)
        downstream = struct.get("downstream_count", 0)
        claimed = struct.get("claimed_reason", "")
        step_name = struct.get("step_name", "")

        structure_block = f"""
## Structural Context (from R1+ static analysis)
- Step name: {step_name}
- Position in proof tree: **{position}**
- Nesting depth: {depth}
- Upstream dependencies: {upstream} steps
- Downstream dependents: {downstream} steps blocked by this sorry
- Claimed reasoning: "{claimed}"
"""
    else:
        structure_block = "\n## Structural Context\nNo structural data available.\n"

    # ── AI classification ──
    system_prompt = _load_prompt("diagnostician")

    tactic_summary = ""
    for tr in sorry_data.get("tactic_results", []):
        status = "✅ SOLVED" if tr["success"] else "❌ failed"
        tactic_summary += f"  {tr['tactic']}: {status}"
        if tr.get("result"):
            tactic_summary += f" — {tr['result']}"
        tactic_summary += "\n"

    user_prompt = f"""Classify this sorry gap. Respond with ONLY a JSON object.

## Original Proof
{original_proof}

## Lean Translation
{lean_code}

## Sorry at Line {sorry_data['line']}
Goal State:
{sorry_data.get('goal', 'N/A')}
{structure_block}
## Tactic Results
{tactic_summary if tactic_summary else "No tactics attempted or all failed."}

## Classification Types (use these exact codes):
- A: Goal is mechanically REFUTED (you can construct a counterexample that Lean can verify)
- C: AI suspects the reasoning is WRONG, but cannot mechanically verify (verification_score low)
- D: Correct math, but can't mechanize — library gap or formalization hard (verification_score high)
- E: Indeterminate — ambiguous source, insufficient info (verification_score mid)

NOTE: Type B (Verified) is assigned automatically when tactics solve the goal. Do NOT assign B.

IMPORTANT RULES:
1. Type A requires a concrete counterexample or structural impossibility proof.
2. Type C: the claimed reasoning appears invalid, even if the goal might be true.
3. Type D: the math is correct but Lean/Mathlib can't handle it.
4. Type E: you genuinely can't tell — ambiguous text, blocked dependency, or conflicting evidence.
5. Pay attention to Structural Context — root sorrys are more likely to be genuine errors.
6. If Reference Materials are provided, cross-check any cited theorems.

{reference_context}

Respond:
{{
  "classification": "C",
  "verification_score": 0.2,
  "confidence": 0.85,
  "reasoning": "explanation",
  "claimed_reason_valid": false,
  "counterexample": null
}}
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_json_response(resp.content)
    except Exception as e:
        result = {"classification": "E", "verification_score": 0.5,
                  "confidence": 0.3, "reasoning": f"AI failed: {e}"}

    # Build gap and classification
    gap = SorryGap(
        sorry_id=f"sorry_L{sorry_data['line']}",
        file=sorry_data.get("file", ""),
        line=sorry_data["line"],
        lean_goal=sorry_data.get("goal", ""),
        blocked_by=sorry_data.get("blocked_by", []),
    )

    raw_type = result.get("classification", "E")
    # Handle legacy labels
    if raw_type in ("A1", "A2"):
        raw_type = "A" if raw_type == "A1" else "C"
    if raw_type in ("F", "G"):
        raw_type = "E"

    type_map = {
        "A": SorryType.A_REFUTED,
        "B": SorryType.B_VERIFIED,
        "C": SorryType.C_SUSPECT_ERROR,
        "D": SorryType.D_LIKELY_CORRECT,
        "E": SorryType.E_INDETERMINATE,
    }
    classification = type_map.get(raw_type, SorryType.E_INDETERMINATE)
    confidence = float(result.get("confidence", 0.5))
    verification_score = float(result.get("verification_score",
                                          classification.default_score))

    # ── Post-classification hardening rules ──
    if struct:
        is_root = struct.get("is_root", False)
        downstream = struct.get("downstream_count", 0)

        # Rule 1: Root sorry classified as A → boost confidence
        if is_root and classification == SorryType.A_REFUTED:
            confidence = min(confidence + 0.05, 1.0)
            result["reasoning"] = result.get("reasoning", "") + (
                " [Structure: root sorry — error is foundational, confidence boosted.]"
            )

        # Rule 2: High downstream impact → flag in reasoning
        if downstream >= 2 and classification in (SorryType.A_REFUTED, SorryType.C_SUSPECT_ERROR):
            confidence = min(confidence + 0.05, 1.0)
            result["reasoning"] = result.get("reasoning", "") + (
                f" [Structure: {downstream} downstream steps blocked — high impact.]"
            )

    # Build internal axes
    axes = InternalAxes(provenance=ProvenanceStatus.ROOT)

    if fidelity_score is not None and fidelity_score < 0.7:
        axes.fidelity = Fidelity.SUSPECT
    if classification == SorryType.A_REFUTED:
        axes.justification = JustificationStatus.INVALID_FALSE
    elif classification == SorryType.C_SUSPECT_ERROR:
        axes.justification = JustificationStatus.INVALID_NONSEQUITUR

    return SorryClassification(
        sorry=gap,
        classification=classification,
        confidence=confidence,
        verification_score=verification_score,
        reasoning=result.get("reasoning", ""),
        evidence={
            "tactic_results": sorry_data.get("tactic_results", []),
            "goal": sorry_data.get("goal", ""),
            "structure": struct,
        },
        internal_axes=axes,
        counterexample=result.get("counterexample"),
    )


# ── Verifier (Round 4) ──────────────────────────────────────


def verify_suspect(
    client: AIClient,
    sorry_data: dict,
    original_proof: str,
    current_type: SorryType,
    reference_context: str = "",
) -> dict:
    """Round 4: Verifier Agent — counterexample search + justification check.

    Targets:
      - Type A: attempt mechanical verification of counterexample
      - Type C: verify whether original reasoning is actually invalid
    """
    system_prompt = _load_prompt("verifier")

    user_prompt = f"""## Task: Verify a potential logical error

### Goal State
{sorry_data.get('goal', 'N/A')}

### Original Proof Text
{original_proof}

### Current Classification: Type {current_type.value}

### Instructions
You must perform THREE separate checks:

1. **Counterexample Search**: Try to find concrete values that make the goal FALSE.
2. **Same-Method Proof**: Try to prove the goal using the EXACT reasoning the original proof claims.
3. **Alternative Proof**: Try to prove the goal by ANY other method.

CRITICAL RULE: Finding an alternative proof does NOT mean the original reasoning is correct.

{reference_context}

Respond with ONLY a JSON object:
{{
  "counterexample_found": true,
  "counterexample": "Let a=3, b=5...",
  "same_method_works": false,
  "same_method_detail": "The original claims a single k, which requires a=b",
  "alternative_proof_found": true,
  "alternative_method": "Using separate witnesses k1, k2",
  "confidence_refuted": 0.95,
  "confidence_suspect": 0.85,
  "verification_score": 0.05,
  "reasoning": "..."
}}
"""

    try:
        resp = client.chat(user_prompt)
        return _parse_json_response(resp.content)
    except Exception as e:
        return {
            "counterexample_found": False,
            "same_method_works": False,
            "alternative_proof_found": False,
            "confidence_refuted": 0.5,
            "confidence_suspect": 0.5,
            "verification_score": 0.5,
            "reasoning": f"Verification failed: {e}",
        }


def apply_verification(
    cls: SorryClassification,
    verify_result: dict,
) -> None:
    """Apply verification results to a classification.

    Logic:
      - Counterexample found → A (Refuted), s=0
      - No counterexample, same_method fails → C (Suspect Error), s low
      - No counterexample, same_method works → B (Verified), s=1.0
      - Alternative proof found → salvageable=True (but keeps A/C)
    """
    cls.evidence["verification"] = verify_result

    has_cx = verify_result.get("counterexample_found", False)
    same_method = verify_result.get("same_method_works", False)
    alt_proof = verify_result.get("alternative_proof_found", False)

    if has_cx:
        # A: Refuted — goal is false
        cls.classification = SorryType.A_REFUTED
        cls.verification_score = 0.0
        cls.confidence = max(cls.confidence, float(verify_result.get("confidence_refuted", 0.9)))
        cls.counterexample = verify_result.get("counterexample", "")
        cls.reasoning += f"\n\n[Verifier] Counterexample: {cls.counterexample}"
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.INVALID_FALSE

    elif not same_method:
        # C: Suspect Error — reasoning doesn't work, but goal may be salvageable
        cls.classification = SorryType.C_SUSPECT_ERROR
        cls.verification_score = float(verify_result.get("verification_score", 0.15))
        cls.confidence = max(cls.confidence, float(verify_result.get("confidence_suspect", 0.7)))
        detail = verify_result.get("same_method_detail", "")
        cls.reasoning += f"\n\n[Verifier] Original reasoning invalid: {detail}"
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.INVALID_NONSEQUITUR

    else:
        # Same method works → B: Verified
        cls.classification = SorryType.B_VERIFIED
        cls.verification_score = 1.0
        cls.confidence = 0.9
        cls.reasoning += "\n\n[Verifier] Same-method proof succeeded. Reclassified as Verified."
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.VALID
            cls.internal_axes.mechanization = MechanizationStatus.API_FOUND

    # Mark salvageability (independent of classification!)
    if alt_proof:
        cls.salvageable = True
        cls.alternative_proof = verify_result.get("alternative_method", "")
        cls.reasoning += (
            f"\n[Verifier] Goal is salvageable via: {cls.alternative_proof}"
            "\n⚠️  Note: salvageability does NOT validate the original reasoning."
        )


# ── Full Audit Pipeline (Rounds 3-5) ────────────────────────


def compute_risk_score(
    cls: SorryClassification,
    all_classifications: list[SorryClassification],
) -> float:
    """Compute a risk score for a sorry classification.

    risk = uncertainty × descendant_factor × criticality
    """
    uncertainty = 1.0 - cls.confidence

    # Descendant count: prefer structural data, fall back to blocked_by scan
    struct = cls.evidence.get("structure", {}) if cls.evidence else {}
    if struct and "downstream_count" in struct:
        descendant_count = struct["downstream_count"]
    else:
        sorry_id = cls.sorry.sorry_id
        descendant_count = sum(
            1 for other in all_classifications
            if sorry_id in (other.sorry.blocked_by or [])
        )

    # Criticality: root sorrys and shallow depth = higher criticality
    if struct and struct.get("is_root"):
        criticality = 1.0  # Root sorrys are always critical
    else:
        max_line = max((c.sorry.line for c in all_classifications), default=1)
        criticality = 1.0 - (cls.sorry.line / max(max_line, 1)) * 0.5

    risk = uncertainty * (1 + descendant_count) * criticality
    return round(risk, 3)


def run_full_audit(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    sorry_diagnoses: list[dict],
    proof_title: str = "Untitled Proof",
    fidelity_score: Optional[float] = None,
    reference_context: str = "",
) -> AuditReport:
    """Run the complete audit pipeline (Rounds 3-5).

    v4 features:
      - 5-type A-E classification with verification score
      - Tactic success → Type B (auto-resolved, s=1.0)
      - Blocked descendants → Type E (Indeterminate)
      - Verifier targets A and C types
      - Cost tracking per round
    """
    from core.ai_client import get_cost_tracker
    tracker = get_cost_tracker()

    # Build dependency graph ONLY if not already computed by pipeline
    has_dependency_data = any(
        "blocked_by" in d for d in sorry_diagnoses
    )
    if not has_dependency_data:
        sorry_diagnoses = build_dependency_graph(lean_code, sorry_diagnoses)

    classifications = []

    # ── Round 3: Classify each sorry ──
    tracker.set_round("R3_classification")
    for sorry_data in sorry_diagnoses:
        classification = classify_sorry_with_ai(
            client, sorry_data, original_proof, lean_code, fidelity_score,
            reference_context=reference_context,
        )
        classifications.append(classification)

    # ── Round 3b: Propagate Type B resolutions to unblock descendants ──
    # When an upstream sorry is auto-resolved (Type B), it no longer blocks
    # downstream sorrys. Re-classify any descendant that becomes unblocked.
    resolved_ids = {
        cls.sorry.sorry_id
        for cls in classifications
        if cls.classification == SorryType.B_VERIFIED
    }
    if resolved_ids:
        reclassify_indices = []
        for i, cls in enumerate(classifications):
            if cls.classification != SorryType.E_INDETERMINATE:
                continue
            remaining_blockers = [
                b for b in (cls.sorry.blocked_by or [])
                if b not in resolved_ids
            ]
            if len(remaining_blockers) < len(cls.sorry.blocked_by or []):
                # At least one blocker was resolved
                cls.sorry.blocked_by = remaining_blockers
                if not remaining_blockers:
                    # Fully unblocked — needs reclassification
                    reclassify_indices.append(i)

        if reclassify_indices:
            tracker.set_round("R3b_reclassification")
            for idx in reclassify_indices:
                sorry_data = sorry_diagnoses[idx]
                # Clear blocking flags so classify_sorry_with_ai won't fast-path to E
                sorry_data["is_blocked"] = False
                sorry_data["blocked_by"] = []
                new_cls = classify_sorry_with_ai(
                    client, sorry_data, original_proof, lean_code, fidelity_score,
                    reference_context=reference_context,
                )
                classifications[idx] = new_cls

        # Repeat until no more propagation (handles transitive chains)
        changed = True
        while changed:
            changed = False
            resolved_ids = {
                cls.sorry.sorry_id
                for cls in classifications
                if cls.classification == SorryType.B_VERIFIED
            }
            reclassify_indices = []
            for i, cls in enumerate(classifications):
                if cls.classification != SorryType.E_INDETERMINATE:
                    continue
                remaining_blockers = [
                    b for b in (cls.sorry.blocked_by or [])
                    if b not in resolved_ids
                ]
                if len(remaining_blockers) < len(cls.sorry.blocked_by or []):
                    cls.sorry.blocked_by = remaining_blockers
                    if not remaining_blockers:
                        reclassify_indices.append(i)

            for idx in reclassify_indices:
                changed = True
                sorry_data = sorry_diagnoses[idx]
                sorry_data["is_blocked"] = False
                sorry_data["blocked_by"] = []
                new_cls = classify_sorry_with_ai(
                    client, sorry_data, original_proof, lean_code, fidelity_score,
                    reference_context=reference_context,
                )
                classifications[idx] = new_cls

    # ── Compute risk scores ──
    for cls in classifications:
        cls.risk_score = compute_risk_score(cls, classifications)

    # ── Round 4: Verify Type A and C suspects + high-risk nodes ──
    tracker.set_round("R4_verification")

    RISK_THRESHOLD = 0.3
    to_verify = []
    for cls in classifications:
        if cls.classification in (SorryType.A_REFUTED, SorryType.C_SUSPECT_ERROR):
            to_verify.append(cls)
        elif cls.risk_score > RISK_THRESHOLD and cls.classification != SorryType.E_INDETERMINATE:
            to_verify.append(cls)

    for cls in to_verify:
        sorry_data_for_verify = {
            "goal": cls.sorry.lean_goal,
            "line": cls.sorry.line,
        }
        verify_result = verify_suspect(
            client, sorry_data_for_verify, original_proof, cls.classification,
            reference_context=reference_context,
        )
        apply_verification(cls, verify_result)

    # ── Round 5: Generate report ──
    tracker.set_round("R5_report")
    report = generate_report(proof_title, classifications)
    return report


def _parse_json_response(text: str) -> dict:
    """Parse JSON from AI response."""
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError:
            pass

    return {"classification": "E", "verification_score": 0.5,
            "confidence": 0.3, "reasoning": text}
