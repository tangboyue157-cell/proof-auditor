"""Diagnostician module v2 — with dependency analysis and corrected Verifier logic.

Key improvements over v1:
  1. Sorry dependency graph: blocked_by / blocks tracking
  2. A1/A2 distinction: false_claim vs invalid_justification
  3. Verifier fix: alternative proof → salvageable, NOT downgrade A2
  4. Blocked descendants: upstream contamination awareness
  5. Fidelity-first: D/C/E only after fidelity=exact confirmed
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
) -> SorryClassification:
    """Classify a single sorry using the Diagnostician Agent.

    v2 changes:
      - Blocked descendants get G classification directly
      - Fidelity check before D/C/E
      - A split into A1/A2
    """
    # ── Fast path: blocked descendant ──
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
            classification=SorryType.G_BLOCKED_DESCENDANT,
            confidence=0.9,
            reasoning=(
                f"This sorry depends on upstream sorry(s) {sorry_data['blocked_by']}. "
                "It cannot be independently classified until root causes are resolved."
            ),
            internal_axes=axes,
        )

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

## Tactic Results
{tactic_summary}

## Classification Types (use these exact codes):
- A1: Goal is provably FALSE (you can construct a counterexample)
- A2: Goal may be true, but the original proof's REASONING for this step is invalid
- B: The AI mistranslated the mathematics (Lean goal doesn't match original step)
- C: Correct math, but Mathlib lacks the needed lemma
- D: The lemma exists in Mathlib but wasn't found / tactic can solve it
- E: Correct but mechanically hard to express in Lean
- F: The original text is ambiguous or underspecified

IMPORTANT RULES:
1. If tactics solve the goal, check: does the tactic's method match the original proof's claimed reasoning? If not, this may still be A2.
2. A1 requires a concrete counterexample. A2 requires showing the claimed reasoning doesn't work.
3. If you suspect translation error, classify as B, not A.

Respond:
{{
  "classification": "A1",
  "confidence": 0.9,
  "reasoning": "explanation",
  "claimed_reason_valid": false,
  "counterexample": "a=3, b=5 shows ..."
}}
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_json_response(resp.content)
    except Exception as e:
        result = {"classification": "E", "confidence": 0.3, "reasoning": f"AI failed: {e}"}

    # Build gap and classification
    gap = SorryGap(
        sorry_id=f"sorry_L{sorry_data['line']}",
        file=sorry_data.get("file", ""),
        line=sorry_data["line"],
        lean_goal=sorry_data.get("goal", ""),
        blocked_by=sorry_data.get("blocked_by", []),
    )

    raw_type = result.get("classification", "E")
    # Handle legacy "A" label
    if raw_type == "A":
        raw_type = "A1"

    type_map = {
        "A1": SorryType.A1_FALSE_CLAIM,
        "A2": SorryType.A2_INVALID_JUSTIFICATION,
        "B": SorryType.B_TRANSLATION_ERROR,
        "C": SorryType.C_MATHLIB_GAP,
        "D": SorryType.D_API_MISS,
        "E": SorryType.E_FORMALIZATION_HARD,
        "F": SorryType.F_SOURCE_AMBIGUITY,
        "G": SorryType.G_BLOCKED_DESCENDANT,
    }
    classification = type_map.get(raw_type, SorryType.E_FORMALIZATION_HARD)

    # Build internal axes
    axes = InternalAxes(provenance=ProvenanceStatus.ROOT)

    if fidelity_score is not None and fidelity_score < 0.7:
        axes.fidelity = Fidelity.SUSPECT
    if classification == SorryType.B_TRANSLATION_ERROR:
        axes.fidelity = Fidelity.SUSPECT
    if classification == SorryType.F_SOURCE_AMBIGUITY:
        axes.fidelity = Fidelity.AMBIGUOUS_SOURCE

    if classification == SorryType.A1_FALSE_CLAIM:
        axes.justification = JustificationStatus.INVALID_FALSE
    elif classification == SorryType.A2_INVALID_JUSTIFICATION:
        axes.justification = JustificationStatus.INVALID_NONSEQUITUR
    elif classification == SorryType.D_API_MISS:
        axes.mechanization = MechanizationStatus.API_FOUND

    return SorryClassification(
        sorry=gap,
        classification=classification,
        confidence=float(result.get("confidence", 0.5)),
        reasoning=result.get("reasoning", ""),
        evidence={
            "tactic_results": sorry_data.get("tactic_results", []),
            "goal": sorry_data.get("goal", ""),
        },
        internal_axes=axes,
        counterexample=result.get("counterexample"),
    )


# ── Verifier (Round 4) — FIXED LOGIC ────────────────────────


def verify_type_a(
    client: AIClient,
    sorry_data: dict,
    original_proof: str,
    current_type: SorryType,
) -> dict:
    """Round 4: Verifier Agent — counterexample search + justification check.

    CRITICAL FIX: Alternative proof → "salvageable" flag, NOT downgrade.
    - A1 (false claim): downgrade ONLY if counterexample search fails AND
      same-method proof succeeds
    - A2 (invalid justification): NEVER downgrade just because alternative
      proof exists. Must verify the ORIGINAL claimed reasoning.
    """
    system_prompt = _load_prompt("verifier")

    user_prompt = f"""## Task: Verify a potential logical error

### Goal State
{sorry_data.get('goal', 'N/A')}

### Original Proof Text
{original_proof}

### Current Classification: {current_type.value}

### Instructions
You must perform THREE separate checks:

1. **Counterexample Search**: Try to find concrete values that make the goal FALSE.
2. **Same-Method Proof**: Try to prove the goal using the EXACT reasoning the original proof claims.
3. **Alternative Proof**: Try to prove the goal by ANY other method.

CRITICAL RULE: Finding an alternative proof does NOT mean the original reasoning is correct.
Example: If the original says "by Fubini" but you prove it "by Tonelli", the original reasoning
is STILL wrong — the goal is merely salvageable.

Respond with ONLY a JSON object:
{{
  "counterexample_found": true,
  "counterexample": "Let a=3, b=5...",
  "same_method_works": false,
  "same_method_detail": "The original claims a single k, which requires a=b",
  "alternative_proof_found": true,
  "alternative_method": "Using separate witnesses k1, k2",
  "confidence_a1": 0.95,
  "confidence_a2": 0.85,
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
            "confidence_a1": 0.5,
            "confidence_a2": 0.5,
            "reasoning": f"Verification failed: {e}",
        }


def apply_verification(
    cls: SorryClassification,
    verify_result: dict,
) -> None:
    """Apply verification results to a classification.

    FIXED LOGIC:
      - Counterexample found → A1 confirmed, high confidence
      - No counterexample, same_method fails → A2 (reasoning wrong, goal maybe salvageable)
      - No counterexample, same_method works → downgrade to D/E
      - Alternative proof found → set salvageable=True (but keep A1/A2)
    """
    cls.evidence["verification"] = verify_result

    has_cx = verify_result.get("counterexample_found", False)
    same_method = verify_result.get("same_method_works", False)
    alt_proof = verify_result.get("alternative_proof_found", False)

    if has_cx:
        # A1 confirmed: goal is straight-up false
        cls.classification = SorryType.A1_FALSE_CLAIM
        cls.confidence = max(cls.confidence, float(verify_result.get("confidence_a1", 0.9)))
        cls.counterexample = verify_result.get("counterexample", "")
        cls.reasoning += f"\n\n[Verifier] Counterexample: {cls.counterexample}"
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.INVALID_FALSE

    elif not same_method:
        # A2: original reasoning doesn't work, but goal might be salvageable
        cls.classification = SorryType.A2_INVALID_JUSTIFICATION
        cls.confidence = max(cls.confidence, float(verify_result.get("confidence_a2", 0.7)))
        detail = verify_result.get("same_method_detail", "")
        cls.reasoning += f"\n\n[Verifier] Original reasoning invalid: {detail}"
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.INVALID_NONSEQUITUR

    else:
        # Same method works → this wasn't A after all, likely D or E
        cls.classification = SorryType.D_API_MISS
        cls.confidence = 0.7
        cls.reasoning += "\n\n[Verifier] Same-method proof succeeded. Reclassified as API miss."
        if cls.internal_axes:
            cls.internal_axes.justification = JustificationStatus.VALID
            cls.internal_axes.mechanization = MechanizationStatus.API_MISSING

    # Mark salvageability (independent of A1/A2 classification!)
    if alt_proof:
        cls.salvageable = True
        cls.alternative_proof = verify_result.get("alternative_method", "")
        cls.reasoning += (
            f"\n[Verifier] Goal is salvageable via: {cls.alternative_proof}"
            "\n⚠️  Note: salvageability does NOT validate the original reasoning."
        )


# ── Full Audit Pipeline (Rounds 3-5) ────────────────────────


def run_full_audit(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    sorry_diagnoses: list[dict],
    proof_title: str = "Untitled Proof",
    fidelity_score: Optional[float] = None,
) -> AuditReport:
    """Run the complete audit pipeline (Rounds 3-5).

    v2: dependency analysis, A1/A2 split, corrected Verifier logic.
    """
    # Build dependency graph
    sorry_diagnoses = build_dependency_graph(lean_code, sorry_diagnoses)

    classifications = []

    # Round 3: Classify each sorry
    for sorry_data in sorry_diagnoses:
        classification = classify_sorry_with_ai(
            client, sorry_data, original_proof, lean_code, fidelity_score
        )
        classifications.append(classification)

    # Round 4: Verify Type A suspects (only root causes!)
    for cls in classifications:
        if cls.classification.is_type_a:
            sorry_data_for_verify = {
                "goal": cls.sorry.lean_goal,
                "line": cls.sorry.line,
            }
            verify_result = verify_type_a(
                client, sorry_data_for_verify, original_proof, cls.classification
            )
            apply_verification(cls, verify_result)

    # Round 5: Generate report
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

    return {"classification": "E", "confidence": 0.3, "reasoning": text}
