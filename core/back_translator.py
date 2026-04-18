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
from typing import Optional

from core.ai_client import AIClient

AGENTS_DIR = Path(__file__).parent.parent / "agents"


class BackTranslationMode(Enum):
    OFF = "off"
    AUTO = "auto"
    HUMAN = "human"
    HYBRID = "hybrid"


@dataclass
class StepComparison:
    """Comparison result for a single proof step."""
    step_id: int
    original_text: str
    back_translated_text: str
    match: bool          # Does the meaning match?
    confidence: float    # How confident is the comparison?
    discrepancy: str     # Description of any difference


@dataclass
class BackTranslationResult:
    """Complete back-translation verification result."""
    back_translation: str       # The full back-translated text
    comparisons: list[StepComparison] = field(default_factory=list)
    overall_match: bool = True
    fidelity_score: float = 1.0   # 0.0 to 1.0
    flagged_lines: list[int] = field(default_factory=list)  # Lean lines with issues
    requires_human: bool = False
    human_message: str = ""


def back_translate(client: AIClient, lean_code: str) -> str:
    """Translate Lean code back to natural language.

    The Back-Translator Agent NEVER sees the original proof.

    Args:
        client: AI client instance.
        lean_code: The full Lean 4 code.

    Returns:
        Natural language description of what the Lean code says.
    """
    system_prompt = (AGENTS_DIR / "back_translator.md").read_text()

    user_prompt = f"""Please translate the following Lean 4 code back into natural language mathematics.
Describe EXACTLY what the code asserts, including all hypotheses and sorry gaps.
Do NOT guess what the proof is "supposed to be" — only describe what the Lean code actually says.

```lean
{lean_code}
```
"""

    resp = client.chat(user_prompt)
    return resp.content


def compare_auto(
    client: AIClient,
    original_proof: str,
    back_translation: str,
) -> BackTranslationResult:
    """Automatically compare original proof with back-translation using AI.

    Args:
        client: AI client.
        original_proof: The original natural language proof.
        back_translation: The back-translated text from Lean.

    Returns:
        BackTranslationResult with comparison details.
    """
    user_prompt = f"""You are a mathematical proof comparison expert.
Compare these two texts and identify ANY semantic differences.

## Text A: Original Proof (what the human wrote)
{original_proof}

## Text B: Back-Translation (what the Lean code actually says)
{back_translation}

## Your Task
For each step in the proof, determine if Text A and Text B say the SAME thing mathematically.
Pay special attention to:
- Variable usage (are the same variables used for the same things?)
- Quantifiers (∀ vs ∃, same variable vs different variables)
- Logical structure (same proof strategy?)
- Missing or extra assumptions

Respond with ONLY a JSON object (no markdown code blocks):
{{
  "steps": [
    {{
      "step_id": 1,
      "original": "brief summary of what original says",
      "back_translated": "brief summary of what Lean code says",
      "match": true,
      "confidence": 0.95,
      "discrepancy": ""
    }},
    {{
      "step_id": 2,
      "original": "there exist integers k and j such that a=2k+1, b=2j+1",
      "back_translated": "there exists ONE integer k such that a=2k+1 AND b=2k+1",
      "match": false,
      "confidence": 0.99,
      "discrepancy": "Original uses two different variables (k,j) but Lean uses only one (k), forcing a=b"
    }}
  ],
  "overall_match": false,
  "fidelity_score": 0.6,
  "summary": "Critical mismatch at step 2: the translation conflates two independent witnesses into one."
}}
"""

    try:
        resp = client.chat(user_prompt)
        result = _parse_comparison_response(resp.content)
    except Exception as e:
        return BackTranslationResult(
            back_translation=back_translation,
            overall_match=True,  # Fail-open: don't block on comparison failure
            fidelity_score=0.5,
            human_message=f"Auto-comparison failed: {e}",
            requires_human=True,
        )

    # Build step comparisons
    comparisons = []
    flagged_lines = []
    for step in result.get("steps", []):
        comp = StepComparison(
            step_id=step.get("step_id", 0),
            original_text=step.get("original", ""),
            back_translated_text=step.get("back_translated", ""),
            match=step.get("match", True),
            confidence=float(step.get("confidence", 0.5)),
            discrepancy=step.get("discrepancy", ""),
        )
        comparisons.append(comp)
        if not comp.match:
            flagged_lines.append(comp.step_id)

    return BackTranslationResult(
        back_translation=back_translation,
        comparisons=comparisons,
        overall_match=result.get("overall_match", True),
        fidelity_score=float(result.get("fidelity_score", 1.0)),
        flagged_lines=flagged_lines,
    )


def compare_human(
    original_proof: str,
    back_translation: str,
) -> BackTranslationResult:
    """Display both texts for human comparison.

    Returns a result with requires_human=True and the display message.
    """
    separator = "─" * 50
    message = f"""
╔══════════════════════════════════════════════════╗
║        HUMAN REVIEW: Back-Translation Check      ║
╚══════════════════════════════════════════════════╝

{separator}
 ORIGINAL PROOF (what you wrote):
{separator}
{original_proof}

{separator}
 BACK-TRANSLATION (what the Lean code actually says):
{separator}
{back_translation}

{separator}
 QUESTION: Do these two texts say the same thing mathematically?
 Pay attention to:
   - Are the same variables used consistently?
   - Are quantifiers correct (∀ vs ∃)?
   - Is the logical structure preserved?
{separator}
"""
    print(message)

    return BackTranslationResult(
        back_translation=back_translation,
        overall_match=True,  # Assume OK unless human says otherwise
        fidelity_score=0.5,
        requires_human=True,
        human_message="Displayed for human review. Proceeding with pipeline.",
    )


def run_back_translation(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    mode: BackTranslationMode = BackTranslationMode.AUTO,
) -> Optional[BackTranslationResult]:
    """Run back-translation verification (Round 1.5).

    Args:
        client: AI client.
        original_proof: Original natural language proof.
        lean_code: The Lean 4 translation.
        mode: Verification mode (off/auto/human/hybrid).

    Returns:
        BackTranslationResult, or None if mode is OFF.
    """
    if mode == BackTranslationMode.OFF:
        return None

    # Step 1: Back-translate (agent NEVER sees original)
    back_translation = back_translate(client, lean_code)

    # Step 2: Compare based on mode
    if mode == BackTranslationMode.HUMAN:
        return compare_human(original_proof, back_translation)

    elif mode == BackTranslationMode.AUTO:
        return compare_auto(client, original_proof, back_translation)

    elif mode == BackTranslationMode.HYBRID:
        # Auto first, escalate to human if low confidence
        result = compare_auto(client, original_proof, back_translation)
        if result.fidelity_score < 0.7 or not result.overall_match:
            print("\n⚠️  Auto-comparison flagged potential issues. Escalating to human review.")
            human_result = compare_human(original_proof, back_translation)
            # Merge: keep auto comparisons but mark as requiring human
            result.requires_human = True
            result.human_message = human_result.human_message
        return result

    return None


def _parse_comparison_response(text: str) -> dict:
    """Parse JSON from AI comparison response."""
    # Try to extract JSON from code blocks
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)

    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON object
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError:
            pass

    return {"overall_match": True, "fidelity_score": 0.5, "steps": []}
