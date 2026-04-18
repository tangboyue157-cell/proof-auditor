"""Translator output parser — extracts structured metadata from Lean code comments.

Parses the hard outputs required by the Translator Agent v2:
  1. ambiguity_ledger: disambiguation choices made during translation
  2. introduced_assumptions: assumptions added beyond the original text
  3. claimed_reasons: per-sorry justification claims from the original proof
  4. translation_map: sorry-to-step mapping (YAML in comments)
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AmbiguityEntry:
    """A single disambiguation choice."""
    term: str           # The ambiguous term
    choice: str         # Which interpretation was chosen
    alternative: str    # The other interpretation(s)
    step: str = ""      # Which proof step this relates to


@dataclass
class IntroducedAssumption:
    """An assumption added by the Translator that wasn't in the original."""
    assumption: str         # The Lean assumption text
    reason: str = ""        # Why it was added (e.g., "required by Lean")
    is_infrastructure: bool = False  # True if it's Lean infrastructure, not math


@dataclass
class ClaimedReason:
    """The original proof's claimed justification for a step."""
    sorry_id: str       # Which sorry this belongs to
    reason: str         # What the original proof claims (e.g., "by Fubini")
    step_number: int = 0


@dataclass
class TranslatorMetadata:
    """All structured metadata extracted from Translator output."""
    ambiguity_ledger: list[AmbiguityEntry] = field(default_factory=list)
    introduced_assumptions: list[IntroducedAssumption] = field(default_factory=list)
    claimed_reasons: list[ClaimedReason] = field(default_factory=list)
    sorry_to_step: dict[str, int] = field(default_factory=dict)  # sorry_id → step number
    has_metadata: bool = False  # True if any metadata was found


def parse_translator_output(lean_code: str) -> TranslatorMetadata:
    """Parse structured metadata from Lean code comments.

    Looks for blocks like:
      AMBIGUITY_LEDGER:
      INTRODUCED_ASSUMPTIONS:
      CLAIMED_REASON:
      SORRY_ID:

    Args:
        lean_code: The full Lean 4 code with comments.

    Returns:
        TranslatorMetadata with all extracted information.
    """
    metadata = TranslatorMetadata()

    # Parse ambiguity ledger
    metadata.ambiguity_ledger = _parse_ambiguity_ledger(lean_code)

    # Parse introduced assumptions
    metadata.introduced_assumptions = _parse_introduced_assumptions(lean_code)

    # Parse claimed reasons (per-sorry)
    metadata.claimed_reasons = _parse_claimed_reasons(lean_code)

    # Parse sorry-to-step mapping
    metadata.sorry_to_step = _parse_sorry_ids(lean_code)

    metadata.has_metadata = bool(
        metadata.ambiguity_ledger
        or metadata.introduced_assumptions
        or metadata.claimed_reasons
        or metadata.sorry_to_step
    )

    return metadata


def _parse_ambiguity_ledger(code: str) -> list[AmbiguityEntry]:
    """Extract AMBIGUITY_LEDGER block."""
    entries = []

    # Find the block between AMBIGUITY_LEDGER: and the next section/closing
    block_match = re.search(
        r"AMBIGUITY_LEDGER:\s*\n(.*?)(?:\n\s*(?:INTRODUCED_|CLAIMED_|-/|\Z))",
        code, re.DOTALL
    )
    if not block_match:
        return entries

    block = block_match.group(1)

    # Parse each "- " entry
    for item in re.finditer(r'-\s*"([^"]+)"[^:]*:\s*(.*?)(?=\n\s*-|\n\s*$|\Z)', block, re.DOTALL):
        term = item.group(1)
        body = item.group(2).strip()

        choice_match = re.search(r'CHOICE:\s*(.+?)(?:\n|$)', body)
        alt_match = re.search(r'ALTERNATIVE:\s*(.+?)(?:\n|$)', body)

        entries.append(AmbiguityEntry(
            term=term,
            choice=choice_match.group(1).strip() if choice_match else "",
            alternative=alt_match.group(1).strip() if alt_match else "",
        ))

    return entries


def _parse_introduced_assumptions(code: str) -> list[IntroducedAssumption]:
    """Extract INTRODUCED_ASSUMPTIONS block."""
    entries = []

    block_match = re.search(
        r"INTRODUCED_ASSUMPTIONS:\s*\n(.*?)(?:\n\s*(?:AMBIGUITY_|CLAIMED_|-/|\Z))",
        code, re.DOTALL
    )
    if not block_match:
        return entries

    block = block_match.group(1)

    if "NONE" in block.upper():
        return entries

    for item in re.finditer(r'-\s*(.*?)(?=\n\s*-|\Z)', block, re.DOTALL):
        text = item.group(1).strip()
        if not text:
            continue

        is_infra = "LEAN_INFRASTRUCTURE" in text.upper() or "required by Lean" in text
        # Try to split into assumption and reason
        parts = text.split("—", 1)
        if len(parts) == 2:
            entries.append(IntroducedAssumption(
                assumption=parts[0].strip(),
                reason=parts[1].strip(),
                is_infrastructure=is_infra,
            ))
        else:
            entries.append(IntroducedAssumption(
                assumption=text,
                is_infrastructure=is_infra,
            ))

    return entries


def _parse_claimed_reasons(code: str) -> list[ClaimedReason]:
    """Extract CLAIMED_REASON comments throughout the code."""
    entries = []

    # Find all -- CLAIMED_REASON: ... lines
    for match in re.finditer(r'--\s*CLAIMED_REASON:\s*(.+)', code):
        reason = match.group(1).strip().strip('"\'')

        # Try to find the associated SORRY_ID nearby (within 5 lines before)
        pos = match.start()
        preceding = code[max(0, pos - 300):pos]
        id_match = re.search(r'SORRY_ID:\s*(\S+)', preceding)
        sorry_id = id_match.group(1) if id_match else ""

        # Try to find step number
        step_match = re.search(r'STEP\s+(\d+)', preceding)
        step_num = int(step_match.group(1)) if step_match else 0

        entries.append(ClaimedReason(
            sorry_id=sorry_id,
            reason=reason,
            step_number=step_num,
        ))

    return entries


def _parse_sorry_ids(code: str) -> dict[str, int]:
    """Extract SORRY_ID → step number mapping."""
    mapping = {}

    for match in re.finditer(
        r'--\s*SORRY_ID:\s*(\S+).*?--\s*STEP\s+(\d+)',
        code, re.DOTALL
    ):
        sorry_id = match.group(1)
        step_num = int(match.group(2))
        mapping[sorry_id] = step_num

    return mapping
