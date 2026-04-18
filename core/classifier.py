"""Sorry classification engine — the core innovation of Proof Auditor.

This module takes:
  - A Lean file with sorry gaps
  - Compilation diagnostics
  - A translation map (sorry → original proof step)

And produces a classification for each sorry gap:
  A. Logical Gap      — original proof has a real error
  B. Translation Error — AI mistranslated the math
  C. Mathlib Gap       — correct but Mathlib lacks the lemma
  D. API Miss          — lemma exists but wasn't found
  E. Formalization Hard — correct but mechanically difficult
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SorryType(Enum):
    """Classification types for sorry gaps."""
    A_LOGICAL_GAP = "A"
    B_TRANSLATION_ERROR = "B"
    C_MATHLIB_GAP = "C"
    D_API_MISS = "D"
    E_FORMALIZATION_HARD = "E"


@dataclass
class SorryGap:
    """A single sorry gap with its metadata."""
    sorry_id: str
    file: str
    line: int
    lean_goal: str
    original_step: str = ""
    lean_error: Optional[str] = None


@dataclass
class SorryClassification:
    """Classification result for a single sorry gap."""
    sorry: SorryGap
    classification: SorryType
    confidence: float  # 0.0 to 1.0
    reasoning: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass
class AuditReport:
    """Complete audit report for a proof."""
    proof_title: str
    total_sorrys: int
    classifications: list[SorryClassification]
    verdict: str = ""  # "LIKELY_CORRECT", "SUSPICIOUS", "ERROR_DETECTED"
    summary: str = ""

    @property
    def type_a_count(self) -> int:
        return sum(
            1 for c in self.classifications
            if c.classification == SorryType.A_LOGICAL_GAP
        )

    @property
    def high_confidence_errors(self) -> list[SorryClassification]:
        return [
            c for c in self.classifications
            if c.classification == SorryType.A_LOGICAL_GAP and c.confidence >= 0.8
        ]

    def compute_verdict(self) -> str:
        """Compute overall verdict based on classifications."""
        if self.high_confidence_errors:
            self.verdict = "ERROR_DETECTED"
        elif self.type_a_count > 0:
            self.verdict = "SUSPICIOUS"
        else:
            self.verdict = "LIKELY_CORRECT"
        return self.verdict


def classify_sorry(gap: SorryGap) -> SorryClassification:
    """Classify a single sorry gap.

    This is the main entry point for sorry classification.
    In Phase 0, this will be a simple heuristic.
    In Phase 1+, this will call AI agents.

    Args:
        gap: The sorry gap to classify.

    Returns:
        A SorryClassification with type, confidence, and reasoning.
    """
    # TODO: Phase 0 — heuristic classification
    # TODO: Phase 1 — AI-driven classification
    raise NotImplementedError("Sorry classification not yet implemented")


def generate_report(
    proof_title: str,
    classifications: list[SorryClassification],
) -> AuditReport:
    """Generate an audit report from sorry classifications.

    Args:
        proof_title: Title of the proof being audited.
        classifications: List of sorry classifications.

    Returns:
        A complete AuditReport.
    """
    report = AuditReport(
        proof_title=proof_title,
        total_sorrys=len(classifications),
        classifications=classifications,
    )
    report.compute_verdict()

    # Generate human-readable summary
    type_counts = {}
    for c in classifications:
        t = c.classification.value
        type_counts[t] = type_counts.get(t, 0) + 1

    lines = [f"Audit of: {proof_title}", f"Total sorry gaps: {len(classifications)}"]
    for t, count in sorted(type_counts.items()):
        lines.append(f"  Type {t}: {count}")
    lines.append(f"Verdict: {report.verdict}")

    report.summary = "\n".join(lines)
    return report
