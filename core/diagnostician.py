"""Diagnostician module — connects AI Agent with LSP data for sorry classification.

This module implements Round 3 of the audit loop:
  1. Takes sorry goals + tactic results from the LSP (Round 2 output)
  2. Sends them to the Diagnostician AI Agent with structured prompts
  3. Parses the AI response into SorryClassification objects

Also implements preliminary Verifier logic (Round 4):
  - For Type A suspects, asks the AI to attempt counterexample construction
"""

import json
import re
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient
from core.classifier import (
    AuditReport,
    SorryClassification,
    SorryGap,
    SorryType,
    generate_report,
)

# Prompt templates
AGENTS_DIR = Path(__file__).parent.parent / "agents"


def _load_prompt(name: str) -> str:
    """Load an agent prompt file."""
    path = AGENTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text()
    return ""


def classify_sorry_with_ai(
    client: AIClient,
    sorry_data: dict,
    original_proof: str,
    lean_code: str,
) -> SorryClassification:
    """Classify a single sorry using the Diagnostician Agent.

    Args:
        client: AI client instance.
        sorry_data: Dict from lean_lsp.diagnose_sorry() containing:
            - file, line, column, goal, tactic_results, any_tactic_solved
        original_proof: The original natural language proof text.
        lean_code: The full Lean translation.

    Returns:
        SorryClassification with type, confidence, and reasoning.
    """
    system_prompt = _load_prompt("diagnostician")

    # Build the user prompt with all evidence
    tactic_summary = ""
    for tr in sorry_data.get("tactic_results", []):
        status = "✅ SOLVED" if tr["success"] else "❌ failed"
        tactic_summary += f"  {tr['tactic']}: {status}"
        if tr.get("result"):
            tactic_summary += f" — {tr['result']}"
        tactic_summary += "\n"

    user_prompt = f"""Please classify this sorry gap. Respond with ONLY a JSON object (no markdown).

## Original Proof
{original_proof}

## Lean Translation (excerpt around sorry)
{lean_code}

## Sorry Location
- File: {sorry_data['file']}
- Line: {sorry_data['line']}

## Goal State at this sorry
{sorry_data['goal']}

## Tactic Search Results
{tactic_summary}

## Your Task
Classify this sorry into one of: A (logical gap), B (translation error), C (Mathlib gap), D (API miss), E (formalization hard).

Respond with this exact JSON format:
{{
  "classification": "A",
  "confidence": 0.85,
  "reasoning": "The goal requires proving ∃ k such that both a=2k+1 and b=2k+1, which forces a=b. This is a genuine logical error in the original proof."
}}
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_classification_response(resp.content)
    except Exception as e:
        result = {
            "classification": "E",
            "confidence": 0.3,
            "reasoning": f"AI classification failed: {e}",
        }

    # Build the SorryGap and SorryClassification
    gap = SorryGap(
        sorry_id=f"sorry_L{sorry_data['line']}",
        file=sorry_data["file"],
        line=sorry_data["line"],
        lean_goal=sorry_data.get("goal", ""),
    )

    type_map = {
        "A": SorryType.A_LOGICAL_GAP,
        "B": SorryType.B_TRANSLATION_ERROR,
        "C": SorryType.C_MATHLIB_GAP,
        "D": SorryType.D_API_MISS,
        "E": SorryType.E_FORMALIZATION_HARD,
    }

    classification = type_map.get(
        result.get("classification", "E"),
        SorryType.E_FORMALIZATION_HARD,
    )

    return SorryClassification(
        sorry=gap,
        classification=classification,
        confidence=float(result.get("confidence", 0.5)),
        reasoning=result.get("reasoning", ""),
        evidence={
            "tactic_results": sorry_data.get("tactic_results", []),
            "goal": sorry_data.get("goal", ""),
        },
    )


def verify_type_a(
    client: AIClient,
    sorry_data: dict,
    original_proof: str,
) -> dict:
    """Round 4: Verifier Agent — attempt counterexample for Type A suspects.

    Args:
        client: AI client instance.
        sorry_data: Dict with goal state.
        original_proof: Original proof text.

    Returns:
        Dict with counterexample info and updated confidence.
    """
    system_prompt = _load_prompt("verifier")

    user_prompt = f"""A sorry gap has been flagged as a potential logical error (Type A).
Your job is to find a COUNTEREXAMPLE to prove this goal is FALSE.

## Original Proof Claim
{original_proof}

## Lean Goal (claimed to be unprovable)
{sorry_data['goal']}

## Your Task
1. Try to construct a specific counterexample (concrete values for the variables).
2. If you find one, explain why it disproves the goal.
3. If you cannot find one, explain why this might still be correct.

Respond with this exact JSON format:
{{
  "counterexample_found": true,
  "counterexample": "Let a = 3, b = 5. Then a is odd (k=1) and b is odd (k=2), but no single k satisfies both a=2k+1 and b=2k+1.",
  "confidence_type_a": 0.95,
  "reasoning": "The goal requires a single k for both a and b, but different odd numbers have different k values."
}}
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_classification_response(resp.content)
    except Exception as e:
        result = {
            "counterexample_found": False,
            "counterexample": None,
            "confidence_type_a": 0.5,
            "reasoning": f"Verification failed: {e}",
        }

    return result


def _parse_classification_response(text: str) -> dict:
    """Parse JSON from AI response, handling markdown code blocks."""
    # Try to extract JSON from code blocks first
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    # Try direct JSON parse
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object in the text
    brace_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return {"classification": "E", "confidence": 0.3, "reasoning": text}


def run_full_audit(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    sorry_diagnoses: list[dict],
    proof_title: str = "Untitled Proof",
) -> AuditReport:
    """Run the complete audit pipeline (Rounds 3-5).

    Args:
        client: AI client.
        original_proof: Natural language proof.
        lean_code: Full Lean translation.
        sorry_diagnoses: List of dicts from lean_lsp.diagnose_sorry().
        proof_title: Title for the report.

    Returns:
        Complete AuditReport.
    """
    classifications = []

    # Round 3: Classify each sorry
    for sorry_data in sorry_diagnoses:
        classification = classify_sorry_with_ai(
            client, sorry_data, original_proof, lean_code
        )
        classifications.append(classification)

    # Round 4: Verify Type A suspects
    for cls in classifications:
        if cls.classification == SorryType.A_LOGICAL_GAP:
            sorry_data_for_verify = {
                "goal": cls.sorry.lean_goal,
                "line": cls.sorry.line,
            }
            verify_result = verify_type_a(client, sorry_data_for_verify, original_proof)

            cls.evidence["verification"] = verify_result
            if verify_result.get("counterexample_found"):
                cls.confidence = max(
                    cls.confidence, verify_result.get("confidence_type_a", 0.9)
                )
                cls.reasoning += (
                    f"\n\n[Verifier] Counterexample found: "
                    f"{verify_result.get('counterexample', '')}"
                )
            else:
                # Downgrade confidence if no counterexample
                cls.confidence = min(cls.confidence, 0.6)
                cls.reasoning += (
                    "\n\n[Verifier] No counterexample found. "
                    "May be formalization difficulty rather than logical error."
                )

    # Round 5: Generate report
    report = generate_report(proof_title, classifications)
    return report
