"""Back-Translation module — Round 1.5 of the audit pipeline.

Translates Lean code back to natural language (independently of the original proof),
then compares the back-translation with the original to detect translation errors.

Supports 4 modes:
  - off:    Skip back-translation entirely
  - auto:   AI compares original vs back-translation automatically
  - human:  Display both texts side-by-side for human review
  - hybrid: AI flags low-confidence items, only those go to human review

Key design principle: The Back-Translator Agent NEVER sees the original proof.
This ensures the errors of forward and backward translation are uncorrelated.
"""

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from core.ai_client import AIClient
from core.proof_structure import analyze_proof_structure

AGENTS_DIR = Path(__file__).parent.parent / "agents"


class BackTranslationMode(Enum):
    OFF = "off"
    AUTO = "auto"
    HUMAN = "human"
    HYBRID = "hybrid"
    WEB = "web"  # Back-translate only, defer comparison to web UI


@dataclass
class StepComparison:
    """Comparison result for a single proof step."""
    step_id: str
    original_text: str
    back_translated_text: str
    match: bool          # Does the meaning match?
    confidence: float    # How confident is the comparison?
    discrepancy: str     # Description of any difference


@dataclass
class BackTranslationArtifact:
    """Parsed output of the back-translator agent."""
    proof_text: str
    summary: Optional[dict] = None
    raw_text: str = ""
    parse_ok: bool = False
    parse_error: str = ""
    unproved_step_ids: list[str] = field(default_factory=list)


@dataclass
class BackTranslationResult:
    """Complete back-translation verification result."""
    back_translation: str       # Natural-language proof text
    back_translation_summary: Optional[dict] = None
    back_translation_raw: str = ""
    comparisons: list[StepComparison] = field(default_factory=list)
    overall_match: bool = False
    review_status: str = "unknown"   # match | mismatch | unknown | pending_human_review
    fidelity_score: Optional[float] = None   # 0.0 to 1.0, or None if not evaluated
    flagged_lines: list = field(default_factory=list)  # step_ids or lean-line markers with issues
    requires_human: bool = False
    human_message: str = ""
    parse_failed: bool = False
    compiles: Optional[bool] = None
    unproved_steps: list[str] = field(default_factory=list)


def _strip_lean_block_comments_preserve_lines(text: str) -> str:
    """Remove Lean block comments while preserving line structure.

    Lean block comments can be nested. We therefore use a small scanner
    instead of a single regex. Newlines are preserved so that surrounding
    line numbers remain approximately stable for human-readable diagnostics.
    """
    out: list[str] = []
    i = 0
    depth = 0
    while i < len(text):
        if text.startswith("/-", i):
            depth += 1
            i += 2
            continue
        if depth > 0 and text.startswith("-/", i):
            depth -= 1
            i += 2
            continue

        ch = text[i]
        if depth > 0:
            if ch == "\n":
                out.append("\n")
            else:
                out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def sanitize_lean_for_backtranslation(lean_code: str) -> str:
    """Remove natural-language comments from Lean code before back-translation.

    The Back-Translator Agent must NEVER see the original proof text.
    This function strips:
      - STEP comments that quote original text
      - CLAIMED_REASON comments
      - SORRY_ID comments (replaced with neutral AUDIT_STEP_ID)
      - Block comments (/- ... -/), including inline ones
      - Docstrings
      - AMBIGUITY_LEDGER / INTRODUCED_ASSUMPTIONS / translation_map blocks
      - Trailing line comments after code

    Keeps:
      - Pure Lean code
      - -- AUDIT_STEP_ID: stepN markers
      - Lean compiler pragmas / commands
    """
    lean_code = _strip_lean_block_comments_preserve_lines(lean_code)

    lines = lean_code.splitlines()
    result: list[str] = []
    in_metadata_block = False

    for line in lines:
        stripped = line.strip()

        # Skip comment-ledger blocks introduced by the translator.
        if in_metadata_block:
            if re.match(r"\s*--\s*(SORRY_ID|STEP\s+\d+|CLAIMED_REASON)\b", line):
                in_metadata_block = False
            elif re.match(r"\s*--", line) or not stripped:
                continue
            else:
                in_metadata_block = False

        if re.match(r"\s*--\s*(AMBIGUITY_LEDGER|INTRODUCED_ASSUMPTIONS|RENDERER_ERROR)\b", line):
            in_metadata_block = True
            continue
        if re.match(r"\s*--\s*translation_map\b", line, re.IGNORECASE):
            in_metadata_block = True
            continue

        # Remove step-annotation comments that can leak the original proof.
        if re.match(r"\s*--\s*STEP\s+\d+\b", line):
            continue
        if re.match(r"\s*--\s*CLAIMED_REASON\s*:", line):
            continue

        # Replace SORRY_ID with neutral audit marker.
        sorry_id_match = re.match(r"(\s*)--\s*SORRY_ID:\s*(\S+)", line)
        if sorry_id_match:
            indent = sorry_id_match.group(1)
            step_id = sorry_id_match.group(2)
            result.append(f"{indent}-- AUDIT_STEP_ID: {step_id}")
            continue

        # Keep only the neutral audit marker among full-line comments.
        if re.match(r"\s*--\s*AUDIT_STEP_ID:\s*\S+", line):
            result.append(line.rstrip())
            continue
        if re.match(r"\s*--", line):
            continue

        # Strip trailing line comments from code lines as a final leakage barrier.
        if "--" in line:
            line = line.split("--", 1)[0].rstrip()

        if not line.strip():
            result.append("")
            continue

        result.append(line.rstrip())

    return "\n".join(result)


def extract_proof_skeleton(lean_code: str) -> list[dict]:
    """Extract a neutral proof skeleton from static proof structure.

    The skeleton is built from syntactic proof structure rather than from
    natural-language comments, so it can cover both proved and sorry steps
    without leaking original prose or claimed reasons.
    """
    structure = analyze_proof_structure(lean_code)
    steps: list[dict] = []

    for idx, step in enumerate(structure.steps, 1):
        step_id = step.sorry_id or step.name or f"s{idx}"
        steps.append({
            "step_id": step_id,
            "label": f"Step {idx}",
            "step_type": step.step_type,
            "lean_line": step.line,
            "end_line": step.end_line,
            "status": "sorry" if step.has_sorry else "proved",
            "dependencies": list(step.references),
        })

    return steps


def _extract_json_object(text: str) -> tuple[Optional[dict], Optional[str]]:
    """Extract a JSON object from a model response."""
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    candidate = text
    if json_match:
        candidate = json_match.group(1).strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data, candidate
        except json.JSONDecodeError:
            pass

    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        candidate = text[brace_start:brace_end].strip()
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data, candidate
        except json.JSONDecodeError:
            pass

    return None, None


def _parse_back_translation_response(text: str) -> BackTranslationArtifact:
    """Parse the back-translator output into prose + structured summary."""
    summary, json_payload = _extract_json_object(text)

    proof_text = text.strip()
    if json_payload:
        proof_text = proof_text.replace(json_payload, "").strip()
    proof_text = re.sub(r"```(?:json)?\s*.*?```", "", proof_text, flags=re.DOTALL)
    proof_text = re.sub(
        r"^\s*#{1,6}\s*Section\s*1\s*:\s*Natural Language Proof\s*$",
        "",
        proof_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    proof_text = re.sub(
        r"^\s*#{1,6}\s*Section\s*2\s*:\s*Structured Summary\s*$.*",
        "",
        proof_text,
        flags=re.IGNORECASE | re.DOTALL | re.MULTILINE,
    ).strip()

    if not proof_text:
        proof_text = text.strip()

    artifact = BackTranslationArtifact(
        proof_text=proof_text,
        summary=summary,
        raw_text=text,
        parse_ok=isinstance(summary, dict),
        parse_error="" if isinstance(summary, dict) else "Missing or invalid JSON summary.",
    )

    if isinstance(summary, dict):
        proof_steps = summary.get("proof_steps", [])
        if isinstance(proof_steps, list):
            artifact.unproved_step_ids = [
                str(step.get("step_id", ""))
                for step in proof_steps
                if isinstance(step, dict) and step.get("status") in ("sorry", "axiom")
            ]

    return artifact


def back_translate(
    client: AIClient,
    lean_code: str,
    skeleton: Optional[list] = None,
) -> BackTranslationArtifact:
    """Translate Lean code back to natural language.

    The Back-Translator Agent NEVER sees the original proof text.
    The lean_code MUST be sanitized before calling this function.
    If a skeleton is provided, it uses the step structure to organize output.

    Args:
        client: AI client instance.
        lean_code: The sanitized Lean 4 code (no original-text comments).
        skeleton: Optional proof skeleton (step_ids only, NO claimed_reasons).

    Returns:
        Parsed back-translation artifact with proof text and structured summary.
    """
    system_prompt = (AGENTS_DIR / "back_translator.md").read_text()

    skeleton_guidance = ""
    if skeleton:
        steps_desc = "\n".join(
            f"  - {s.get('label', s.get('step_id', '?'))}"
            f" [id={s.get('step_id', '?')}, status={s.get('status', 'proved')},"
            f" lines={s.get('lean_line', '?')}-{s.get('end_line', '?')}]"
            for s in skeleton
        )
        skeleton_guidance = f"""

## Proof Structure Guide

The proof has these steps. Use them to organize your output,
but describe what the Lean code ACTUALLY says at each step:

{steps_desc}

For each step, include the same step_id in the JSON summary.
If a step uses `sorry`, mark it as an unproved claim.
"""

    user_prompt = f"""Translate the following Lean 4 code back into a natural language mathematical proof.
Be LITERAL: describe exactly what the code says, not what it "should" say.
If a step uses `sorry`, state the claim but note it is UNPROVED.{skeleton_guidance}

```lean
{lean_code}
```
"""

    bt_client = AIClient(
        provider=client.provider,
        model=client.model,
        system_prompt=system_prompt,
    )
    resp = bt_client.chat(user_prompt)
    return _parse_back_translation_response(resp.content)


def compare_auto(
    client: AIClient,
    original_proof: str,
    artifact: BackTranslationArtifact,
    compiles: Optional[bool] = None,
) -> BackTranslationResult:
    """Compare original proof with back-translation using structured diagnosis.

    Uses AI to produce a multi-dimensional diagnosis (statement, binders,
    quantifiers, assumptions, atoms, witnesses), then applies hard-cap
    scoring via compute_fidelity_v2.
    """
    from core.fidelity import compute_fidelity_v2

    if not artifact.parse_ok:
        return BackTranslationResult(
            back_translation=artifact.proof_text,
            back_translation_summary=artifact.summary,
            back_translation_raw=artifact.raw_text,
            overall_match=False,
            review_status="unknown",
            fidelity_score=None,
            requires_human=True,
            human_message=(
                "Back-translator output did not contain a valid structured summary JSON. "
                "Automatic comparison withheld."
            ),
            parse_failed=True,
            compiles=compiles,
            unproved_steps=artifact.unproved_step_ids,
        )

    summary_json = json.dumps(artifact.summary, ensure_ascii=False, indent=2)

    user_prompt = f"""You are a mathematical proof comparison expert.
Compare these two texts and produce a STRUCTURED DIAGNOSIS of their differences.

## Text A: Original Proof
{original_proof}

## Text B1: Back-Translation (natural-language proof)
{artifact.proof_text}

## Text B2: Back-Translation Structured Summary
{summary_json}

## Instructions
Analyze these specific dimensions. For each, assign severity:
  - "clean": no issue
  - "minor": cosmetic difference (wording, notation style)
  - "major": mathematical content differs but not a logical contradiction
  - "fatal": logical contradiction, direction reversal, or scope error

Use Text B2 to reason about explicit step status (`proved` / `sorry` / `axiom`)
and step dependencies. If Text B1 and Text B2 conflict, trust Text B2.

Respond with ONLY a JSON object (no markdown code blocks):
{{
  "statement": {{
    "match": true,
    "severity": "clean",
    "issue": ""
  }},
  "binders": {{
    "match": true,
    "alpha_renaming": {{}},
    "structural_mismatch": false
  }},
  "quantifiers": {{
    "match": true,
    "severity": "clean",
    "issue": ""
  }},
  "assumptions": {{
    "added": [],
    "removed": []
  }},
  "atomic_formulas": [
    {{
      "original": "a = 2k+1",
      "back": "a = 2k+1",
      "match": true,
      "severity": "clean"
    }}
  ],
  "witness_dependencies": [
    {{
      "original": "k for a, j for b",
      "back": "same k for both a and b",
      "match": false,
      "severity": "major"
    }}
  ],
  "steps": [
    {{
      "step_id": "s1",
      "original_claim": "brief summary of original step",
      "back_claim": "brief summary of back-translated step",
      "severity": "clean",
      "issue": ""
    }}
  ],
  "overall_match": false,
  "confidence": 0.85,
  "summary": "Shared witness error at step 2"
}}

CRITICAL RULES:
1. "fatal" severity = logical contradiction or mathematical incorrectness, NOT just different wording.
2. α-renaming (x→y with same meaning) is NOT an error. Report in alpha_renaming, set match=true.
3. Quantifier scope changes (∀x∃y → ∃y∀x) are ALWAYS "fatal".
4. Relation direction changes (x<y → y<x, x≤y → x≥y) are ALWAYS "fatal".
5. An added/removed assumption is "fatal" if it changes the theorem's strength.
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_comparison_response(resp.content)
    except Exception as e:
        return BackTranslationResult(
            back_translation=artifact.proof_text,
            back_translation_summary=artifact.summary,
            back_translation_raw=artifact.raw_text,
            overall_match=False,
            review_status="unknown",
            fidelity_score=None,
            requires_human=True,
            human_message=f"AI comparison failed ({e}). Automatic comparison withheld.",
            parse_failed=True,
            compiles=compiles,
            unproved_steps=artifact.unproved_step_ids,
        )

    if result.get("parse_failed"):
        return BackTranslationResult(
            back_translation=artifact.proof_text,
            back_translation_summary=artifact.summary,
            back_translation_raw=artifact.raw_text,
            overall_match=False,
            review_status="unknown",
            fidelity_score=None,
            requires_human=True,
            human_message="Comparison model did not return valid JSON. Automatic comparison withheld.",
            parse_failed=True,
            compiles=compiles,
            unproved_steps=artifact.unproved_step_ids,
        )

    comparisons: list[StepComparison] = []
    flagged_lines: list[str] = []
    for step in result.get("steps", []):
        severity = step.get("severity", "clean")
        is_match = severity in ("clean", "minor")
        step_id = str(step.get("step_id", ""))
        comp = StepComparison(
            step_id=step_id,
            original_text=step.get("original_claim", step.get("original", "")),
            back_translated_text=step.get("back_claim", step.get("back_translated", "")),
            match=is_match,
            confidence=float(result.get("confidence", 0.8)),
            discrepancy=step.get("issue", ""),
        )
        comparisons.append(comp)
        if not is_match:
            flagged_lines.append(step_id)

    breakdown = compute_fidelity_v2(
        original_proof,
        artifact.proof_text,
        ai_diagnosis=result,
        compiles=compiles,
    )
    score = breakdown.composite_score

    ai_overall = bool(result.get("overall_match", False))
    has_fatal = bool(breakdown.fatal_issues)
    overall_match = ai_overall and score >= 0.7 and not has_fatal
    review_status = "match" if overall_match else "mismatch"

    message = breakdown.summary()
    if artifact.unproved_step_ids:
        message += (
            "\n  ── Back-translation status ──\n"
            f"    Unproved/axiomatic steps: {', '.join(artifact.unproved_step_ids)}"
        )

    return BackTranslationResult(
        back_translation=artifact.proof_text,
        back_translation_summary=artifact.summary,
        back_translation_raw=artifact.raw_text,
        comparisons=comparisons,
        overall_match=overall_match,
        review_status=review_status,
        fidelity_score=score,
        flagged_lines=flagged_lines,
        requires_human=False,
        human_message=message,
        parse_failed=False,
        compiles=compiles,
        unproved_steps=artifact.unproved_step_ids,
    )


def compare_human(
    original_proof: str,
    artifact: BackTranslationArtifact,
    compiles: Optional[bool] = None,
) -> BackTranslationResult:
    """Display both texts for human comparison and collect judgment."""
    separator = "─" * 50
    print(f"""
╔══════════════════════════════════════════════════╗
║        HUMAN REVIEW: Back-Translation Check      ║
╚══════════════════════════════════════════════════╝

{separator}
 📄 ORIGINAL PROOF (what you wrote):
{separator}
{original_proof}

{separator}
 🔁 BACK-TRANSLATION (what the Lean code actually says):
{separator}
{artifact.proof_text}

{separator}
 ❓ Do these two texts say the SAME thing mathematically?
    Pay attention to:
      • Are the same variables used consistently?
      • Are quantifiers correct (∀ vs ∃)?
      • Is the logical structure preserved?
      • Are any assumptions added or removed?
{separator}
""")

    if artifact.summary:
        print("Structured summary available for review:")
        print(json.dumps(artifact.summary, ensure_ascii=False, indent=2))
        print(separator)

    while True:
        choice = input("  [y] Yes, they match  |  [n] No, there are problems  →  ").strip().lower()
        if choice in ("y", "yes"):
            print("   ✅ Human approved: translation is faithful.\n")
            return BackTranslationResult(
                back_translation=artifact.proof_text,
                back_translation_summary=artifact.summary,
                back_translation_raw=artifact.raw_text,
                overall_match=True,
                review_status="match",
                fidelity_score=1.0,
                requires_human=True,
                human_message="Human approved translation as faithful.",
                parse_failed=not artifact.parse_ok,
                compiles=compiles,
                unproved_steps=artifact.unproved_step_ids,
            )
        if choice in ("n", "no"):
            break
        print("   Please enter 'y' or 'n'.")

    print("\n  Please describe the discrepancies (one per line, empty line to finish):")
    discrepancies = []
    while True:
        line = input("    > ").strip()
        if not line:
            break
        discrepancies.append(line)

    print("\n  How severe is the mismatch?")
    print("    [1] Minor: notation/phrasing differences, math is the same")
    print("    [2] Moderate: some steps differ but overall structure is close")
    print("    [3] Major: key variables, quantifiers, or logic are wrong")

    severity_map = {"1": 0.8, "2": 0.5, "3": 0.2}
    while True:
        severity = input("  Severity [1/2/3] →  ").strip()
        if severity in severity_map:
            fidelity = severity_map[severity]
            break
        print("   Please enter 1, 2, or 3.")

    comparisons = []
    for i, desc in enumerate(discrepancies, 1):
        comparisons.append(StepComparison(
            step_id=f"human_{i}",
            original_text="(human-identified)",
            back_translated_text="(human-identified)",
            match=False,
            confidence=1.0,
            discrepancy=desc,
        ))

    discrepancy_text = "\n".join(f"  - {d}" for d in discrepancies) if discrepancies else "(no details)"
    print(f"\n   🔴 Human rejected translation (fidelity: {fidelity:.0%})")
    print(f"   Discrepancies recorded: {len(discrepancies)}\n")

    return BackTranslationResult(
        back_translation=artifact.proof_text,
        back_translation_summary=artifact.summary,
        back_translation_raw=artifact.raw_text,
        comparisons=comparisons,
        overall_match=False,
        review_status="mismatch",
        fidelity_score=fidelity,
        flagged_lines=[c.step_id for c in comparisons],
        requires_human=True,
        human_message=f"Human rejected. Discrepancies:\n{discrepancy_text}",
        parse_failed=not artifact.parse_ok,
        compiles=compiles,
        unproved_steps=artifact.unproved_step_ids,
    )


def run_back_translation(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    mode: BackTranslationMode = BackTranslationMode.AUTO,
    compiles: Optional[bool] = None,
) -> Optional[BackTranslationResult]:
    """Run back-translation verification (Round 1.5)."""
    if mode == BackTranslationMode.OFF:
        return None

    sanitized_lean = sanitize_lean_for_backtranslation(lean_code)
    skeleton = extract_proof_skeleton(lean_code)

    artifact = back_translate(client, sanitized_lean, skeleton=skeleton)

    if mode == BackTranslationMode.HUMAN:
        return compare_human(original_proof, artifact, compiles=compiles)

    if mode == BackTranslationMode.AUTO:
        return compare_auto(client, original_proof, artifact, compiles=compiles)

    if mode == BackTranslationMode.HYBRID:
        auto_result = compare_auto(client, original_proof, artifact, compiles=compiles)
        needs_human = (
            auto_result.requires_human
            or auto_result.fidelity_score is None
            or auto_result.fidelity_score < 0.7
            or not auto_result.overall_match
        )
        if needs_human:
            print("\n⚠️  Auto-comparison flagged potential issues. Escalating to human review.")
            human_result = compare_human(original_proof, artifact, compiles=compiles)
            if auto_result.human_message:
                human_result.human_message = (
                    f"{human_result.human_message}\n\n[Auto diagnosis]\n{auto_result.human_message}"
                )
            return human_result
        return auto_result

    if mode == BackTranslationMode.WEB:
        return BackTranslationResult(
            back_translation=artifact.proof_text,
            back_translation_summary=artifact.summary,
            back_translation_raw=artifact.raw_text,
            overall_match=False,
            review_status="pending_human_review",
            fidelity_score=None,
            requires_human=True,
            human_message="Awaiting human review via web UI.",
            parse_failed=not artifact.parse_ok,
            compiles=compiles,
            unproved_steps=artifact.unproved_step_ids,
        )

    return None


def _parse_comparison_response(text: str) -> dict:
    """Parse JSON from AI comparison response."""
    data, _json_payload = _extract_json_object(text)
    if isinstance(data, dict):
        return data

    # Parse failure is "unknown", NOT "pass".
    return {"overall_match": False, "requires_human": True, "parse_failed": True, "steps": []}
