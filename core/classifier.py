"""Sorry classification engine — the core innovation of Proof Auditor.

v2: Multi-axis internal representation with backward-compatible A-G labels.

Internal axes:
  - fidelity: exact | suspect | ambiguous_source
  - justification_status: valid | invalid_false | invalid_nonsequitur | unknown
  - mechanization_status: api_found | api_missing | library_missing | boilerplate_heavy
  - provenance_status: root | blocked_descendant

External labels (derived from axes):
  A1. False Claim           — goal is provably false (counterexample exists)
  A2. Invalid Justification — goal may be true, but original reasoning is wrong
  B.  Translation Error     — AI mistranslated the mathematics
  C.  Mathlib Gap           — correct but Mathlib lacks the lemma
  D.  API Miss              — lemma exists but wasn't found
  E.  Formalization Hard    — correct but mechanically difficult
  F.  Source Ambiguity      — original text is ambiguous/underspecified
  G.  Blocked Descendant    — not a root cause; inherited from upstream sorry
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SorryType(Enum):
    """Classification types for sorry gaps (external labels)."""
    A1_FALSE_CLAIM = "A1"
    A2_INVALID_JUSTIFICATION = "A2"
    B_TRANSLATION_ERROR = "B"
    C_MATHLIB_GAP = "C"
    D_API_MISS = "D"
    E_FORMALIZATION_HARD = "E"
    F_SOURCE_AMBIGUITY = "F"
    G_BLOCKED_DESCENDANT = "G"

    # Backward compatibility aliases
    @classmethod
    def from_legacy(cls, value: str) -> "SorryType":
        """Convert legacy A-E labels to new A1/A2-G labels."""
        legacy_map = {"A": cls.A1_FALSE_CLAIM}  # Legacy A defaults to A1
        return legacy_map.get(value, cls(value))

    @property
    def is_type_a(self) -> bool:
        return self in (SorryType.A1_FALSE_CLAIM, SorryType.A2_INVALID_JUSTIFICATION)

    @property
    def severity(self) -> str:
        """Error severity for reporting."""
        if self.is_type_a:
            return "critical"
        if self == SorryType.B_TRANSLATION_ERROR:
            return "high"  # B can cascade contamination
        if self == SorryType.G_BLOCKED_DESCENDANT:
            return "info"
        return "medium"


# ── Internal multi-axis classification ──────────────────────


class Fidelity(Enum):
    EXACT = "exact"
    SUSPECT = "suspect"
    AMBIGUOUS_SOURCE = "ambiguous_source"


class JustificationStatus(Enum):
    VALID = "valid"
    INVALID_FALSE = "invalid_false"            # Goal itself is false
    INVALID_NONSEQUITUR = "invalid_nonsequitur"  # Goal may be true, reasoning wrong
    UNKNOWN = "unknown"


class MechanizationStatus(Enum):
    API_FOUND = "api_found"
    API_MISSING = "api_missing"
    LIBRARY_MISSING = "library_missing"
    BOILERPLATE_HEAVY = "boilerplate_heavy"
    NOT_APPLICABLE = "not_applicable"


class ProvenanceStatus(Enum):
    ROOT = "root"
    BLOCKED_DESCENDANT = "blocked_descendant"


@dataclass
class InternalAxes:
    """Multi-axis internal classification (richer than A-G label)."""
    fidelity: Fidelity = Fidelity.EXACT
    justification: JustificationStatus = JustificationStatus.UNKNOWN
    mechanization: MechanizationStatus = MechanizationStatus.NOT_APPLICABLE
    provenance: ProvenanceStatus = ProvenanceStatus.ROOT

    def derive_label(self) -> SorryType:
        """Derive external A1-G label from internal axes."""
        # G: blocked descendant (regardless of other axes)
        if self.provenance == ProvenanceStatus.BLOCKED_DESCENDANT:
            return SorryType.G_BLOCKED_DESCENDANT

        # F: source ambiguity
        if self.fidelity == Fidelity.AMBIGUOUS_SOURCE:
            return SorryType.F_SOURCE_AMBIGUITY

        # B: translation fidelity suspect
        if self.fidelity == Fidelity.SUSPECT:
            return SorryType.B_TRANSLATION_ERROR

        # A1: goal is provably false
        if self.justification == JustificationStatus.INVALID_FALSE:
            return SorryType.A1_FALSE_CLAIM

        # A2: goal may be true but reasoning is wrong
        if self.justification == JustificationStatus.INVALID_NONSEQUITUR:
            return SorryType.A2_INVALID_JUSTIFICATION

        # D: tactic/API found it
        if self.mechanization == MechanizationStatus.API_FOUND:
            return SorryType.D_API_MISS

        # C: library gap
        if self.mechanization == MechanizationStatus.LIBRARY_MISSING:
            return SorryType.C_MATHLIB_GAP

        # E: everything else
        if self.mechanization == MechanizationStatus.BOILERPLATE_HEAVY:
            return SorryType.E_FORMALIZATION_HARD

        # Default
        return SorryType.E_FORMALIZATION_HARD


# ── Data structures ─────────────────────────────────────────


@dataclass
class SorryGap:
    """A single sorry gap with its metadata."""
    sorry_id: str
    file: str
    line: int
    lean_goal: str
    original_step: str = ""
    lean_error: Optional[str] = None
    # Dependency tracking
    blocked_by: list[str] = field(default_factory=list)    # sorry_ids this depends on
    blocks: list[str] = field(default_factory=list)        # sorry_ids blocked by this
    # Source mapping (many-to-many)
    source_spans: list[tuple[int, int]] = field(default_factory=list)  # (start, end) in original
    # Claimed reason from original proof
    claimed_reason: str = ""


@dataclass
class SorryClassification:
    """Classification result for a single sorry gap."""
    sorry: SorryGap
    classification: SorryType
    confidence: float  # 0.0 to 1.0
    reasoning: str = ""
    evidence: dict = field(default_factory=dict)
    # Internal axes (for analysis)
    internal_axes: Optional[InternalAxes] = None
    # Verification details
    counterexample: Optional[str] = None
    alternative_proof: Optional[str] = None
    salvageable: bool = False  # True if alternative proof exists (but A2 stays A2)
    # Risk score (for A-loop prioritization)
    risk_score: float = 0.0
    # Obligation group (for many-to-many mapping)
    obligation_group: Optional[str] = None


@dataclass
class AuditReport:
    """Complete audit report for a proof."""
    proof_title: str
    total_sorrys: int
    classifications: list[SorryClassification]
    verdict: str = ""
    summary: str = ""
    # Root cause analysis
    root_causes: list[str] = field(default_factory=list)  # sorry_ids that are root causes
    blocked_count: int = 0

    @property
    def type_a_count(self) -> int:
        return sum(1 for c in self.classifications if c.classification.is_type_a)

    @property
    def root_cause_errors(self) -> list[SorryClassification]:
        """Type A errors that are root causes (not blocked descendants)."""
        return [
            c for c in self.classifications
            if c.classification.is_type_a
            and (c.internal_axes is None
                 or c.internal_axes.provenance == ProvenanceStatus.ROOT)
        ]

    @property
    def high_confidence_errors(self) -> list[SorryClassification]:
        return [
            c for c in self.classifications
            if c.classification.is_type_a and c.confidence >= 0.8
        ]

    def compute_verdict(self) -> str:
        """Compute overall verdict based on classifications."""
        root_errors = self.root_cause_errors
        if any(c.confidence >= 0.8 for c in root_errors):
            self.verdict = "ERROR_DETECTED"
        elif root_errors:
            self.verdict = "SUSPICIOUS"
        elif any(c.classification == SorryType.B_TRANSLATION_ERROR for c in self.classifications):
            self.verdict = "TRANSLATION_ISSUES"
        else:
            self.verdict = "LIKELY_CORRECT"

        # Compute root cause info
        self.root_causes = [
            c.sorry.sorry_id for c in self.classifications
            if c.internal_axes and c.internal_axes.provenance == ProvenanceStatus.ROOT
            and c.classification.is_type_a
        ]
        self.blocked_count = sum(
            1 for c in self.classifications
            if c.classification == SorryType.G_BLOCKED_DESCENDANT
        )
        return self.verdict


def generate_report(
    proof_title: str,
    classifications: list[SorryClassification],
) -> AuditReport:
    """Generate an audit report from sorry classifications."""
    report = AuditReport(
        proof_title=proof_title,
        total_sorrys=len(classifications),
        classifications=classifications,
    )
    report.compute_verdict()

    # Generate human-readable summary
    type_counts: dict[str, int] = {}
    for c in classifications:
        t = c.classification.value
        type_counts[t] = type_counts.get(t, 0) + 1

    lines = [
        f"Audit of: {proof_title}",
        f"Total sorry gaps: {len(classifications)}",
    ]
    for t, count in sorted(type_counts.items()):
        lines.append(f"  Type {t}: {count}")

    if report.root_causes:
        lines.append(f"Root causes: {len(report.root_causes)}")
    if report.blocked_count:
        lines.append(f"Blocked descendants: {report.blocked_count}")

    lines.append(f"Verdict: {report.verdict}")
    report.summary = "\n".join(lines)
    return report
