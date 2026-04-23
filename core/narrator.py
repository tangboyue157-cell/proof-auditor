"""
Adjudicator module — Final independent review of audit results.

The Adjudicator is completely isolated from the Diagnostician.
It receives ALL evidence and independently:
  1. Reviews each classification (can override)
  2. Renders the final verdict
  3. Produces a human-readable narrative

This is the last step in the pipeline and the FINAL authority.

Usage:
    from core.narrator import adjudicate

    result = adjudicate(client, original_proof, lean_code, report,
                        bt_result, proof_structure)
    print(result.narrative)
    print(f"Final verdict: {result.final_verdict}")
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient
from core.classifier import AuditReport, SorryType, Verdict

AGENTS_DIR = Path(__file__).parent.parent / "agents"


@dataclass
class AdjudicationOverride:
    """A single override decision by the Adjudicator."""
    sorry_id: str
    original_type: str
    final_type: str
    override: bool
    review_note: str = ""


@dataclass
class AdjudicationResult:
    """Complete adjudication result."""
    final_verdict: str       # VERIFIED_ERROR | VERIFIED_CORRECT | NEEDS_REVIEW
    confidence: float
    overrides: list = field(default_factory=list)  # list[AdjudicationOverride]
    narrative_diagnosis: str = ""
    narrative_fix: str = ""
    narrative_impact: str = ""

    @property
    def narrative(self) -> str:
        """Full narrative as a single string."""
        sections = []
        if self.narrative_diagnosis:
            sections.append(f"### Diagnosis\n{self.narrative_diagnosis}")
        if self.narrative_fix:
            sections.append(f"### Fix Suggestion\n{self.narrative_fix}")
        if self.narrative_impact:
            sections.append(f"### Impact Assessment\n{self.narrative_impact}")
        return "\n\n".join(sections)

    @property
    def has_overrides(self) -> bool:
        return any(o.override for o in self.overrides)

    def to_dict(self) -> dict:
        return {
            "final_verdict": self.final_verdict,
            "confidence": self.confidence,
            "has_overrides": self.has_overrides,
            "overrides": [
                {
                    "sorry_id": o.sorry_id,
                    "original_type": o.original_type,
                    "final_type": o.final_type,
                    "override": o.override,
                    "review_note": o.review_note,
                }
                for o in self.overrides
            ],
            "narrative": {
                "diagnosis": self.narrative_diagnosis,
                "fix_suggestion": self.narrative_fix,
                "impact_assessment": self.narrative_impact,
            },
        }


def _load_prompt() -> str:
    path = AGENTS_DIR / "narrator.md"
    return path.read_text() if path.exists() else ""


def _build_evidence_packet(
    original_proof: str,
    lean_code: str,
    report: AuditReport,
    fidelity_score: Optional[float],
    proof_structure_summary: Optional[str],
) -> str:
    """Assemble ALL evidence into a single packet for the Adjudicator."""
    sections = []

    # 1. Original proof
    sections.append(f"## Original Proof\n{original_proof}")

    # 2. Lean code (truncated for context)
    lean_lines = lean_code.splitlines()
    if len(lean_lines) > 100:
        lean_display = "\n".join(lean_lines[:100]) + f"\n... ({len(lean_lines) - 100} more lines)"
    else:
        lean_display = lean_code
    sections.append(f"## Lean Translation\n```lean\n{lean_display}\n```")

    # 3. Structure
    if proof_structure_summary:
        sections.append(f"## Proof Structure (R1+ Static Analysis)\n{proof_structure_summary}")

    # 4. Fidelity
    if fidelity_score is not None:
        fid_status = "HIGH" if fidelity_score >= 0.8 else ("MEDIUM" if fidelity_score >= 0.6 else "LOW")
        sections.append(
            f"## Translation Fidelity: {fidelity_score:.0%} ({fid_status})\n"
            f"{'⚠️ LOW fidelity — classifications may be unreliable.' if fidelity_score < 0.7 else '✅ Fidelity acceptable.'}"
        )

    # 5. Classifications (the Diagnostician's opinion)
    sections.append("## Diagnostician's Classifications (for your review)")
    for cls in report.classifications:
        t = cls.classification.value
        block = f"### {cls.sorry.sorry_id} — Type {t} (score: {cls.verification_score:.2f}, confidence: {cls.confidence:.0%})\n"
        block += f"- Verdict: {cls.classification.verdict.value}\n"
        block += f"- Goal: {cls.sorry.lean_goal[:200]}\n"
        block += f"- Reasoning: {cls.reasoning[:300]}\n"
        if cls.counterexample:
            block += f"- Counterexample: {cls.counterexample[:200]}\n"
        if cls.salvageable:
            alt = cls.alternative_proof or "yes"
            block += f"- Salvageable: {alt}\n"
        if cls.sorry.blocked_by:
            block += f"- Blocked by: {', '.join(cls.sorry.blocked_by)}\n"

        # Include structural context if available
        struct = cls.evidence.get("structure", {}) if cls.evidence else {}
        if struct:
            position = "ROOT" if struct.get("is_root") else ("LEAF" if struct.get("is_leaf") else "INTERMEDIATE")
            block += f"- Position: {position}, downstream: {struct.get('downstream_count', 0)}\n"
            block += f"- Verification score: {cls.verification_score:.2f}\n"
            if struct.get("claimed_reason"):
                block += f"- Claimed reasoning: \"{struct['claimed_reason']}\"\n"

        # Include tactic results
        tactic_data = cls.evidence.get("tactic_results", []) if cls.evidence else []
        solved = [t["tactic"] for t in tactic_data if t.get("success")]
        if solved:
            block += f"- Tactics that solved it: {', '.join(solved)}\n"

        sections.append(block)

    return "\n\n".join(sections)


def adjudicate(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    report: AuditReport,
    fidelity_score: Optional[float] = None,
    proof_structure_summary: Optional[str] = None,
) -> AdjudicationResult:
    """Run the Adjudicator: independent final review of all audit evidence.

    The Adjudicator is completely isolated from the Diagnostician.
    It receives ALL evidence and renders the final verdict.

    Args:
        client: AI client (fresh context, no shared history with Diagnostician).
        original_proof: Original mathematical proof text.
        lean_code: The Lean 4 translation.
        report: Complete audit report from R3-R5.
        fidelity_score: Translation fidelity (0-1).
        proof_structure_summary: R1+ structure summary string.

    Returns:
        AdjudicationResult with final verdict, overrides, and narrative.
    """
    system_prompt = _load_prompt()
    evidence = _build_evidence_packet(
        original_proof, lean_code, report,
        fidelity_score, proof_structure_summary,
    )

    user_prompt = f"""Review all evidence below and render your final judgment.

{evidence}

Respond with ONLY a JSON object following the format in your instructions.
Review each classification independently. Override if the evidence doesn't support it.
Then write the three narrative sections for the mathematician.
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_adjudication(resp.content, report)
    except Exception as e:
        # Fallback: accept Diagnostician's verdict, generate basic narrative
        result = _fallback_adjudication(report, fidelity_score, str(e))

    return result


def _parse_adjudication(text: str, report: AuditReport) -> AdjudicationResult:
    """Parse the Adjudicator's JSON response.

    Handles multiple response formats:
    - Pure JSON with narrative object
    - JSON with markdown narrative sections
    - Mixed markdown + JSON
    """
    raw_text = text  # Keep original for fallback parsing

    # Extract JSON
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    text = text.strip()
    data = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        brace_start = text.find("{")
        brace_end = text.rfind("}") + 1
        if brace_start >= 0 and brace_end > brace_start:
            try:
                data = json.loads(text[brace_start:brace_end])
            except json.JSONDecodeError:
                pass

    if data is None:
        # Try parsing the raw text for JSON
        brace_start = raw_text.find("{")
        brace_end = raw_text.rfind("}") + 1
        if brace_start >= 0 and brace_end > brace_start:
            try:
                data = json.loads(raw_text[brace_start:brace_end])
            except json.JSONDecodeError:
                pass

    if data is None:
        # Last resort: try to extract narrative from markdown sections
        return _parse_markdown_narrative(raw_text, report)

    # Build overrides
    overrides = []
    for o in data.get("overrides", []):
        overrides.append(AdjudicationOverride(
            sorry_id=o.get("sorry_id", ""),
            original_type=o.get("original_type", ""),
            final_type=o.get("final_type", ""),
            override=o.get("override", False),
            review_note=o.get("review_note", ""),
        ))

    # Parse narrative (handle multiple field name variants)
    narrative = data.get("narrative", {})
    if isinstance(narrative, str):
        # AI returned narrative as a single string
        return AdjudicationResult(
            final_verdict=data.get("final_verdict", report.verdict),
            confidence=float(data.get("confidence", 0.5)),
            overrides=overrides,
            narrative_diagnosis=narrative,
            narrative_fix="",
            narrative_impact="",
        )

    diagnosis = (
        narrative.get("diagnosis", "")
        or narrative.get("Diagnosis", "")
        or data.get("diagnosis", "")
    )
    fix = (
        narrative.get("fix_suggestion", "")
        or narrative.get("fix", "")
        or narrative.get("Fix Suggestion", "")
        or data.get("fix_suggestion", "")
    )
    impact = (
        narrative.get("impact_assessment", "")
        or narrative.get("impact", "")
        or narrative.get("Impact Assessment", "")
        or data.get("impact_assessment", "")
    )

    # If narrative fields are still empty, try extracting from raw text
    if not diagnosis and not fix and not impact:
        md_result = _parse_markdown_narrative(raw_text, report)
        diagnosis = md_result.narrative_diagnosis
        fix = md_result.narrative_fix
        impact = md_result.narrative_impact

    return AdjudicationResult(
        final_verdict=data.get("final_verdict", report.verdict),
        confidence=float(data.get("confidence", 0.5)),
        overrides=overrides,
        narrative_diagnosis=diagnosis,
        narrative_fix=fix,
        narrative_impact=impact,
    )


def _parse_markdown_narrative(text: str, report: AuditReport) -> AdjudicationResult:
    """Extract narrative from markdown sections when JSON parsing fails."""
    diagnosis = ""
    fix = ""
    impact = ""

    # Try to find ### Diagnosis, ### Fix Suggestion, ### Impact Assessment
    diag_match = re.search(
        r'###?\s*Diagnosis\s*\n(.*?)(?=###|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    if diag_match:
        diagnosis = diag_match.group(1).strip()

    fix_match = re.search(
        r'###?\s*Fix\s*(?:Suggestion)?\s*\n(.*?)(?=###|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    if fix_match:
        fix = fix_match.group(1).strip()

    impact_match = re.search(
        r'###?\s*Impact\s*(?:Assessment)?\s*\n(.*?)(?=###|\Z)', text, re.DOTALL | re.IGNORECASE
    )
    if impact_match:
        impact = impact_match.group(1).strip()

    # Try to find verdict
    verdict = report.verdict
    verdict_match = re.search(r'(?:final.?verdict|verdict)\s*[:=]\s*(\w+)', text, re.IGNORECASE)
    if verdict_match:
        verdict = verdict_match.group(1)

    return AdjudicationResult(
        final_verdict=verdict,
        confidence=0.7,
        overrides=[],
        narrative_diagnosis=diagnosis or "Unable to parse adjudication narrative.",
        narrative_fix=fix,
        narrative_impact=impact,
    )


def _fallback_adjudication(
    report: AuditReport,
    fidelity_score: Optional[float],
    error_msg: str = "",
) -> AdjudicationResult:
    """Fallback: accept Diagnostician's verdict, generate basic narrative."""
    a_types = [c for c in report.classifications if c.classification == SorryType.A_REFUTED]
    c_types = [c for c in report.classifications if c.classification == SorryType.C_SUSPECT_ERROR]

    if a_types:
        first_a = a_types[0]
        diagnosis = (
            f"The proof contains {len(a_types)} logical error(s). "
            f"Primary error at line {first_a.sorry.line}: {first_a.reasoning[:200]}"
        )
        if first_a.counterexample:
            diagnosis += f" Counterexample: {first_a.counterexample[:200]}"

        fix = "Review and correct the identified logical error(s)."
        if first_a.salvageable and first_a.alternative_proof:
            fix = f"The theorem is salvageable. Alternative: {first_a.alternative_proof[:200]}"

        impact = f"The root cause affects {report.blocked_count} downstream step(s)."
    elif c_types:
        first_c = c_types[0]
        diagnosis = (
            f"The proof has {len(c_types)} suspect step(s) requiring review. "
            f"Primary concern at line {first_c.sorry.line}: {first_c.reasoning[:200]}"
        )
        fix = "Review the flagged steps and verify the reasoning."
        impact = f"{len(c_types)} step(s) flagged for human review."
    else:
        diagnosis = "No logical errors detected. All verification gaps are due to formalization limitations."
        fix = "No mathematical fixes needed."
        impact = "The proof appears mathematically sound."

    overrides = [
        AdjudicationOverride(
            sorry_id=c.sorry.sorry_id,
            original_type=c.classification.value,
            final_type=c.classification.value,
            override=False,
            review_note=f"[Fallback — Adjudicator unavailable: {error_msg[:100]}]",
        )
        for c in report.classifications
        if c.classification != SorryType.E_INDETERMINATE or True  # include all for review
    ]

    return AdjudicationResult(
        final_verdict=report.verdict,
        confidence=0.5,
        overrides=overrides,
        narrative_diagnosis=diagnosis,
        narrative_fix=fix,
        narrative_impact=impact,
    )


# Keep backward compatibility
generate_narrative = None  # Removed — use adjudicate() instead
