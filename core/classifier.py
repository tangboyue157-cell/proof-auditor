"""Sorry classification engine v4 — [0,1] verification score model.

Core design:
  s = 0     → Lean compiled ¬P → VERIFIED_ERROR   (Type A)
  s = 1     → Lean compiled P  → VERIFIED_CORRECT  (Type B)
  s ∈ (0,1) → neither compiled → NEEDS_REVIEW      (Types C/D/E)

Five classification types:
  A. Refuted           — goal mechanically refuted (Lean proved ¬P)
  B. Verified          — sorry mechanically resolved (Lean proved P)
  C. Suspect Error     — AI suspects reasoning is wrong (s low)
  D. Likely Correct    — AI believes correct but can't mechanize (s high)
  E. Indeterminate     — insufficient info / blocked / ambiguous (s mid)

Translation errors are handled as a pipeline quality gate (Round 1.5),
NOT as a sorry classification.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(Enum):
    """Top-level three-way verdict."""
    VERIFIED_ERROR = "VERIFIED_ERROR"
    VERIFIED_CORRECT = "VERIFIED_CORRECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    TRANSLATION_FAILED = "TRANSLATION_FAILED"


class SorryType(Enum):
    """Classification types for sorry gaps (5-type system)."""
    A_REFUTED = "A"
    B_VERIFIED = "B"
    C_SUSPECT_ERROR = "C"
    D_LIKELY_CORRECT = "D"
    E_INDETERMINATE = "E"

    @classmethod
    def from_legacy(cls, value: str) -> "SorryType":
        """Convert legacy A1-G labels to new A-E labels."""
        legacy_map = {
            "A1": cls.A_REFUTED,
            "A2": cls.C_SUSPECT_ERROR,
            "A": cls.A_REFUTED,
            "B": cls.E_INDETERMINATE,   # old B (translation error) → indeterminate
            "C": cls.D_LIKELY_CORRECT,  # old C (Mathlib gap) → likely correct
            "D": cls.B_VERIFIED,        # old D (API miss, tactic solved) → verified
            "E": cls.D_LIKELY_CORRECT,  # old E (formalization hard) → likely correct
            "F": cls.E_INDETERMINATE,   # old F (source ambiguity) → indeterminate
            "G": cls.E_INDETERMINATE,   # old G (blocked descendant) → indeterminate
        }
        return legacy_map.get(value, cls.E_INDETERMINATE)

    @property
    def verdict(self) -> Verdict:
        """Return the top-level verdict for this classification."""
        if self == SorryType.A_REFUTED:
            return Verdict.VERIFIED_ERROR
        if self == SorryType.B_VERIFIED:
            return Verdict.VERIFIED_CORRECT
        return Verdict.NEEDS_REVIEW

    @property
    def severity(self) -> str:
        """Error severity for reporting."""
        if self == SorryType.A_REFUTED:
            return "critical"
        if self == SorryType.C_SUSPECT_ERROR:
            return "high"
        if self == SorryType.B_VERIFIED:
            return "info"
        return "medium"

    @property
    def default_score(self) -> float:
        """Default verification score for this type."""
        if self == SorryType.A_REFUTED:
            return 0.0
        if self == SorryType.B_VERIFIED:
            return 1.0
        if self == SorryType.C_SUSPECT_ERROR:
            return 0.15
        if self == SorryType.D_LIKELY_CORRECT:
            return 0.85
        return 0.5  # E_INDETERMINATE

    # Backward compatibility
    @property
    def is_type_a(self) -> bool:
        return self == SorryType.A_REFUTED


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
    """Multi-axis internal classification (richer than A-E label)."""
    fidelity: Fidelity = Fidelity.EXACT
    justification: JustificationStatus = JustificationStatus.UNKNOWN
    mechanization: MechanizationStatus = MechanizationStatus.NOT_APPLICABLE
    provenance: ProvenanceStatus = ProvenanceStatus.ROOT

    def derive_label(self) -> SorryType:
        """Derive external A-E label from internal axes."""
        # Blocked descendant → E (indeterminate)
        if self.provenance == ProvenanceStatus.BLOCKED_DESCENDANT:
            return SorryType.E_INDETERMINATE

        # Source ambiguity → E (indeterminate)
        if self.fidelity == Fidelity.AMBIGUOUS_SOURCE:
            return SorryType.E_INDETERMINATE

        # Translation fidelity suspect → E (indeterminate)
        # Note: translation errors should ideally be caught at the quality gate,
        # but if they slip through, we mark as indeterminate rather than trusting
        # any classification built on a bad translation.
        if self.fidelity == Fidelity.SUSPECT:
            return SorryType.E_INDETERMINATE

        # A: goal is provably false (mechanically verified)
        if self.justification == JustificationStatus.INVALID_FALSE:
            return SorryType.A_REFUTED

        # B: tactic/API found a proof (mechanically verified)
        if self.mechanization == MechanizationStatus.API_FOUND:
            return SorryType.B_VERIFIED

        # C: reasoning appears invalid (AI judgment, not mechanically verified)
        if self.justification == JustificationStatus.INVALID_NONSEQUITUR:
            return SorryType.C_SUSPECT_ERROR

        # D: library gap or boilerplate
        if self.mechanization in (
            MechanizationStatus.LIBRARY_MISSING,
            MechanizationStatus.BOILERPLATE_HEAVY,
        ):
            return SorryType.D_LIKELY_CORRECT

        # Default: E (indeterminate)
        return SorryType.E_INDETERMINATE


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
    verification_score: float = 0.5  # [0,1] verification score
    reasoning: str = ""
    evidence: dict = field(default_factory=dict)
    # Internal axes (for analysis)
    internal_axes: Optional[InternalAxes] = None
    # Verification details
    counterexample: Optional[str] = None
    alternative_proof: Optional[str] = None
    salvageable: bool = False  # True if alternative proof exists
    # Risk score (for prioritization)
    risk_score: float = 0.0
    # Obligation group (for many-to-many mapping)
    obligation_group: Optional[str] = None

    @property
    def verdict(self) -> Verdict:
        """Convenience: get the verdict from the classification."""
        return self.classification.verdict


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
        return sum(1 for c in self.classifications if c.classification == SorryType.A_REFUTED)

    @property
    def verified_count(self) -> int:
        """Count of mechanically verified correct gaps (Type B)."""
        return sum(1 for c in self.classifications if c.classification == SorryType.B_VERIFIED)

    @property
    def needs_review_count(self) -> int:
        """Count of gaps needing human review (Types C/D/E)."""
        return sum(
            1 for c in self.classifications
            if c.classification in (SorryType.C_SUSPECT_ERROR, SorryType.D_LIKELY_CORRECT, SorryType.E_INDETERMINATE)
        )

    @property
    def root_cause_errors(self) -> list[SorryClassification]:
        """Type A errors that are root causes."""
        return [
            c for c in self.classifications
            if c.classification == SorryType.A_REFUTED
            and (c.internal_axes is None
                 or c.internal_axes.provenance == ProvenanceStatus.ROOT)
        ]

    @property
    def high_confidence_errors(self) -> list[SorryClassification]:
        return [
            c for c in self.classifications
            if c.classification == SorryType.A_REFUTED and c.confidence >= 0.8
        ]

    @property
    def suspect_errors(self) -> list[SorryClassification]:
        """Type C: AI suspects error but no mechanical verification."""
        return [
            c for c in self.classifications
            if c.classification == SorryType.C_SUSPECT_ERROR
        ]

    def compute_verdict(self) -> str:
        """Compute overall verdict based on classifications."""
        root_errors = self.root_cause_errors
        suspect = self.suspect_errors

        if any(c.confidence >= 0.8 for c in root_errors):
            self.verdict = Verdict.VERIFIED_ERROR.value
        elif root_errors:
            self.verdict = Verdict.VERIFIED_ERROR.value
        elif suspect:
            self.verdict = Verdict.NEEDS_REVIEW.value
        elif all(c.classification == SorryType.B_VERIFIED for c in self.classifications):
            self.verdict = Verdict.VERIFIED_CORRECT.value
        else:
            self.verdict = Verdict.NEEDS_REVIEW.value

        # Compute root cause info
        self.root_causes = [
            c.sorry.sorry_id for c in self.classifications
            if c.internal_axes and c.internal_axes.provenance == ProvenanceStatus.ROOT
            and c.classification == SorryType.A_REFUTED
        ]
        self.blocked_count = sum(
            1 for c in self.classifications
            if c.internal_axes and c.internal_axes.provenance == ProvenanceStatus.BLOCKED_DESCENDANT
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

    # Group by verdict
    verdict_groups = {
        "VERIFIED_ERROR": [],
        "VERIFIED_CORRECT": [],
        "NEEDS_REVIEW": [],
    }
    for c in classifications:
        v = c.classification.verdict.value
        verdict_groups.setdefault(v, []).append(c)

    lines = [
        f"Audit of: {proof_title}",
        f"Total sorry gaps: {len(classifications)}",
        f"Verdict: {report.verdict}",
        "",
        "Breakdown:",
    ]

    for verdict_name, group in verdict_groups.items():
        if group:
            lines.append(f"  {verdict_name}: {len(group)}")
            for c in group:
                lines.append(f"    Type {c.classification.value} ({c.classification.name}): "
                           f"score={c.verification_score:.2f}, confidence={c.confidence:.0%}")

    if report.root_causes:
        lines.append(f"Root cause errors: {len(report.root_causes)}")
    if report.blocked_count:
        lines.append(f"Blocked descendants: {report.blocked_count}")

    report.summary = "\n".join(lines)
    return report
