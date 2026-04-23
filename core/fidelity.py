"""
Fidelity Scoring for Back-Translation Verification — v2.

Replaces the old weighted-average approach with a hard-cap + multi-dimensional
scoring system designed for mathematical proof translation.

Key differences from v1:
  - 5 semantic dimensions (statement, binder, atom, step, dependency)
  - Hard caps for fatal errors (quantifier scope, conclusion change, etc.)
  - α-renaming awareness (variable names don't penalize if binding is correct)
  - Relation-aware atom comparison (= vs < vs ≤ etc.)
  - Backward-compatible composite_score property

Usage:
    from core.fidelity import compute_fidelity_breakdown, compute_fidelity_v2

    # Old API (still works, delegates to v2 internally):
    breakdown = compute_fidelity_breakdown(original, back_translation)
    print(breakdown.composite_score)

    # New API (richer output):
    breakdown = compute_fidelity_v2(original, back_translation, ai_diagnosis=diagnosis)
    print(breakdown.final_score)
    print(breakdown.fatal_issues)
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ═══════════════════════════════════════════════════════════════════
# HARD CAP THRESHOLDS
# ═══════════════════════════════════════════════════════════════════

CAPS = {
    'compilation_failure':          0.10,
    'statement_mismatch':           0.20,
    'conclusion_changed':           0.25,
    'quantifier_scope_mismatch':    0.35,
    'assumption_added_or_removed':  0.40,
    'witness_dependency_changed':   0.40,
    'relation_direction_error':     0.45,
}

# Weights for the base score (before caps)
# plan_goal is the highest weight because it directly compares
# the AI's plan claims against Lean LSP goal states — the most
# reliable fidelity signal available.
WEIGHTS = {
    'plan_goal':   0.30,
    'statement':   0.25,
    'binder':      0.15,
    'atom':        0.15,
    'step':        0.10,
    'dependency':  0.05,
}


# ═══════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class CapApplied:
    """A hard cap that was triggered."""
    cap: float
    reason: str
    details: str = ""


@dataclass
class StepDiagnosis:
    """AI-provided structured diagnosis for one proof step."""
    step_id: str
    severity: str  # fatal | major | minor | clean
    issue: str = ""
    original_claim: str = ""
    back_claim: str = ""


@dataclass
class FidelityBreakdown:
    """Multi-dimensional fidelity score with hard caps.

    The composite_score property is backward-compatible with v1.
    New code should use final_score and inspect subscores / fatal_issues.
    """
    # ── New v2 dimensions ──
    plan_goal_score: float = 1.0   # Plan-vs-Goal alignment (from Round 2.1)
    statement_score: float = 1.0
    binder_score: float = 1.0
    atom_score: float = 1.0
    step_score: float = 1.0
    dependency_score: float = 1.0

    # ── Hard cap tracking ──
    caps_applied: list = field(default_factory=list)  # list[CapApplied]
    fatal_issues: list = field(default_factory=list)  # list[str]

    # ── AI diagnosis data ──
    step_diagnoses: list = field(default_factory=list)  # list[StepDiagnosis]
    ai_confidence: float = 0.5  # AI's self-reported confidence

    # ── Backward-compatible v1 fields ──
    # These are derived from v2 dimensions for consumers that expect them.
    variable_score: float = 1.0
    quantifier_score: float = 1.0
    equation_score: float = 1.0
    ai_semantic_score: float = 1.0

    # v1 raw data (still populated for transparency)
    vars_original: set = field(default_factory=set)
    vars_backtranslation: set = field(default_factory=set)
    quants_original: list = field(default_factory=list)
    quants_backtranslation: list = field(default_factory=list)
    steps_original: int = 0
    steps_backtranslation: int = 0

    # v1 weights (preserved for backward compat)
    weights: dict = field(default_factory=lambda: {
        'variable': 0.25,
        'quantifier': 0.20,
        'step': 0.10,
        'equation': 0.15,
        'ai_semantic': 0.30,
    })

    # Optional override used only by the backward-compatible v1 wrapper.
    legacy_composite_override: Optional[float] = None

    @property
    def base_score(self) -> float:
        """Weighted base score BEFORE caps."""
        return (
            WEIGHTS['plan_goal'] * self.plan_goal_score +
            WEIGHTS['statement'] * self.statement_score +
            WEIGHTS['binder'] * self.binder_score +
            WEIGHTS['atom'] * self.atom_score +
            WEIGHTS['step'] * self.step_score +
            WEIGHTS['dependency'] * self.dependency_score
        )

    @property
    def final_score(self) -> float:
        """Final fidelity score after applying hard caps."""
        caps = [c.cap for c in self.caps_applied]
        if caps:
            return min(self.base_score, *caps)
        return self.base_score

    @property
    def composite_score(self) -> float:
        """Backward-compatible composite score.

        New v2 callers should read final_score. Legacy callers that still use
        compute_fidelity_breakdown(..., ai_semantic_score=...) receive the old
        weighted-average semantics via legacy_composite_override.
        """
        if self.legacy_composite_override is not None:
            return self.legacy_composite_override
        return self.final_score

    @property
    def decision(self) -> str:
        """Pass/reject/human_review decision based on score and issues."""
        if self.fatal_issues:
            return "reject"
        if self.final_score >= 0.7:
            return "pass"
        if self.final_score >= 0.5:
            return "human_review"
        return "reject"

    def summary(self) -> str:
        """Human-readable summary of the breakdown."""
        lines = [
            f"Fidelity: {self.final_score:.0%} ({self.decision})",
            f"  Plan-Goal:   {self.plan_goal_score:.0%} (×{WEIGHTS['plan_goal']:.0%})",
            f"  Statement:   {self.statement_score:.0%}",
            f"  Binders:     {self.binder_score:.0%}",
            f"  Atoms:       {self.atom_score:.0%}",
            f"  Steps:       {self.step_score:.0%}",
            f"  Dependency:  {self.dependency_score:.0%}",
            f"  Base score:  {self.base_score:.0%}",
        ]
        if self.caps_applied:
            lines.append("  ── Hard caps applied ──")
            for c in self.caps_applied:
                lines.append(f"    ⚠ Capped at {c.cap:.0%}: {c.reason}")
        if self.fatal_issues:
            lines.append("  ── Fatal issues ──")
            for f in self.fatal_issues:
                lines.append(f"    🔴 {f}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# CORE SCORING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def compute_fidelity_v2(
    original: str,
    back_translation: str,
    ai_diagnosis: Optional[dict] = None,
    compiles: Optional[bool] = None,
) -> FidelityBreakdown:
    """Compute fidelity with hard-cap + multi-dimensional scoring.

    This is the main entry point for v2 scoring.

    Args:
        original: Original proof text.
        back_translation: Back-translated text from Lean.
        ai_diagnosis: Optional structured AI diagnosis with fields:
            - statement: {match: bool, issue: str, severity: str}
            - binders: {match: bool, alpha_renaming: dict}
            - quantifiers: {match: bool, severity: str, issue: str}
            - assumptions: {added: list, removed: list}
            - atomic_formulas: list of {original, back, match, severity}
            - witness_dependencies: list of {original, back, match, severity}
            - steps: list of {step_id, severity, issue, ...}
            - confidence: float
        compiles: Whether the Lean code compiles (None = unknown).

    Returns:
        FidelityBreakdown with all dimensions, caps, and backward-compat fields.
    """
    breakdown = FidelityBreakdown()

    # ── Step 1: Programmatic extraction (v1 legacy, still useful) ──
    vars_a = extract_math_variables(original)
    vars_b = extract_math_variables(back_translation)
    quants_a = extract_quantifiers(original)
    quants_b = extract_quantifiers(back_translation)
    steps_a = count_proof_steps(original)
    steps_b = count_proof_steps(back_translation)
    eqs_a = extract_equations(original)
    eqs_b = extract_equations(back_translation)

    # Store raw data
    breakdown.vars_original = vars_a
    breakdown.vars_backtranslation = vars_b
    breakdown.quants_original = quants_a
    breakdown.quants_backtranslation = quants_b
    breakdown.steps_original = steps_a
    breakdown.steps_backtranslation = steps_b

    # ── Step 2: Compute v1 scores (for backward compat) ──
    breakdown.variable_score = variable_similarity(vars_a, vars_b)
    breakdown.quantifier_score = quantifier_consistency(quants_a, quants_b)
    breakdown.equation_score = equation_similarity(eqs_a, eqs_b)

    # ── Step 3: Compute v2 scores ──
    # Always establish a programmatic baseline first. This prevents partial or
    # underspecified AI diagnoses from silently leaving dimensions at 1.0.
    _apply_programmatic_scoring(
        breakdown, vars_a, vars_b, quants_a, quants_b, steps_a, steps_b, eqs_a, eqs_b
    )

    if ai_diagnosis and not ai_diagnosis.get("parse_failed", False):
        _apply_ai_diagnosis(breakdown, ai_diagnosis)

    # ── Step 4: Apply hard caps ──
    if compiles is False:
        breakdown.caps_applied.append(CapApplied(
            cap=CAPS['compilation_failure'],
            reason="Lean code does not compile",
        ))
        breakdown.fatal_issues.append("Lean code does not compile")

    _apply_programmatic_caps(breakdown, quants_a, quants_b)

    # ── Step 5: Populate backward-compat ai_semantic_score ──
    if ai_diagnosis and not ai_diagnosis.get("parse_failed", False):
        breakdown.ai_semantic_score = ai_diagnosis.get("confidence", 0.5)
    else:
        # Derive from step_score as fallback
        breakdown.ai_semantic_score = breakdown.step_score

    return breakdown


def _apply_ai_diagnosis(
    breakdown: FidelityBreakdown,
    diag: dict,
) -> None:
    """Apply structured AI diagnosis to populate v2 scores and caps."""

    # ── Statement fidelity ──
    stmt = diag.get("statement", {})
    if isinstance(stmt, dict):
        if stmt.get("match", True):
            breakdown.statement_score = 1.0
        else:
            severity = stmt.get("severity", "major")
            if severity == "fatal":
                breakdown.statement_score = 0.1
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['statement_mismatch'],
                    reason=f"Statement mismatch: {stmt.get('issue', '')}",
                ))
                breakdown.fatal_issues.append(f"Statement: {stmt.get('issue', 'mismatch')}")
            elif severity == "major":
                breakdown.statement_score = 0.3
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['conclusion_changed'],
                    reason=f"Conclusion changed: {stmt.get('issue', '')}",
                ))
            else:
                breakdown.statement_score = 0.6

    # ── Binder fidelity (α-renaming aware) ──
    binders = diag.get("binders", {})
    if isinstance(binders, dict):
        if binders.get("match", True):
            breakdown.binder_score = 1.0
        else:
            alpha = binders.get("alpha_renaming", {})
            if alpha and not binders.get("structural_mismatch", False):
                # Pure α-renaming: don't penalize
                breakdown.binder_score = 0.95
            else:
                breakdown.binder_score = 0.3

    # ── Quantifier fidelity ──
    quants = diag.get("quantifiers", {})
    if isinstance(quants, dict):
        if quants.get("match", True):
            pass  # Keep binder_score as-is
        else:
            severity = quants.get("severity", "major")
            if severity == "fatal":
                breakdown.binder_score = min(breakdown.binder_score, 0.1)
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['quantifier_scope_mismatch'],
                    reason=f"Quantifier scope: {quants.get('issue', '')}",
                ))
                breakdown.fatal_issues.append(f"Quantifier scope: {quants.get('issue', 'mismatch')}")
            elif severity == "major":
                breakdown.binder_score = min(breakdown.binder_score, 0.4)

    # ── Assumption changes ──
    assumptions = diag.get("assumptions", {})
    if isinstance(assumptions, dict):
        added = assumptions.get("added", [])
        removed = assumptions.get("removed", [])
        if added or removed:
            impact = len(added) + len(removed)
            breakdown.binder_score = min(breakdown.binder_score, max(0.2, 1.0 - 0.2 * impact))
            breakdown.caps_applied.append(CapApplied(
                cap=CAPS['assumption_added_or_removed'],
                reason=f"Assumptions changed: +{len(added)} -{len(removed)}",
                details=f"Added: {added}, Removed: {removed}",
            ))
            if removed:
                breakdown.fatal_issues.append(
                    f"Assumptions removed: {', '.join(str(r) for r in removed)}"
                )

    # ── Atomic formula fidelity ──
    atoms = diag.get("atomic_formulas", [])
    if isinstance(atoms, list) and atoms:
        total = len(atoms)
        clean = sum(1 for a in atoms if a.get("match", True))
        breakdown.atom_score = clean / total if total > 0 else 1.0

        # Check for relation direction errors
        for a in atoms:
            if not a.get("match", True) and a.get("severity") == "fatal":
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['relation_direction_error'],
                    reason=f"Relation error: {a.get('original', '')} ≠ {a.get('back', '')}",
                ))
                breakdown.fatal_issues.append(
                    f"Relation direction: {a.get('original', '?')} vs {a.get('back', '?')}"
                )

    # ── Witness dependency fidelity ──
    witnesses = diag.get("witness_dependencies", [])
    if isinstance(witnesses, list) and witnesses:
        total = len(witnesses)
        clean = sum(1 for w in witnesses if w.get("match", True))
        breakdown.dependency_score = clean / total if total > 0 else 1.0

        for w in witnesses:
            if not w.get("match", True):
                severity = w.get("severity", "major")
                if severity in ("fatal", "major"):
                    breakdown.caps_applied.append(CapApplied(
                        cap=CAPS['witness_dependency_changed'],
                        reason=f"Witness dependency: {w.get('original', '')} → {w.get('back', '')}",
                    ))
                    if severity == "fatal":
                        breakdown.fatal_issues.append(
                            f"Witness dependency: {w.get('original', '?')} vs {w.get('back', '?')}"
                        )

    # ── Step fidelity ──
    steps = diag.get("steps", [])
    if isinstance(steps, list) and steps:
        total = len(steps)
        clean = sum(1 for s in steps if s.get("severity") in ("clean", "minor", None))
        breakdown.step_score = clean / total if total > 0 else 1.0

        # Build step diagnoses
        for s in steps:
            breakdown.step_diagnoses.append(StepDiagnosis(
                step_id=s.get("step_id", "?"),
                severity=s.get("severity", "clean"),
                issue=s.get("issue", ""),
                original_claim=s.get("original_claim", ""),
                back_claim=s.get("back_claim", ""),
            ))

    # ── Confidence ──
    breakdown.ai_confidence = diag.get("confidence", 0.5)


def _apply_programmatic_scoring(
    breakdown: FidelityBreakdown,
    vars_a: set, vars_b: set,
    quants_a: list, quants_b: list,
    steps_a: int, steps_b: int,
    eqs_a: list, eqs_b: list,
) -> None:
    """Fallback: populate v2 scores from programmatic extraction when no AI diagnosis."""

    # Statement score: approximate from equation and quantifier overlap
    # Without AI, we can't precisely compare theorem statements,
    # so we use a reasonable proxy.
    eq_score = equation_similarity(eqs_a, eqs_b)
    var_score = variable_similarity(vars_a, vars_b)
    quant_score = quantifier_consistency(quants_a, quants_b)

    breakdown.statement_score = 0.5 * eq_score + 0.3 * var_score + 0.2 * quant_score
    breakdown.binder_score = 0.6 * var_score + 0.4 * quant_score
    breakdown.atom_score = eq_score
    breakdown.step_score = step_consistency(steps_a, steps_b)
    breakdown.dependency_score = 0.5  # Unknown without AI


def _apply_programmatic_caps(
    breakdown: FidelityBreakdown,
    quants_a: list, quants_b: list,
) -> None:
    """Apply caps detectable from programmatic extraction alone."""

    # Quantifier count mismatch → possible scope issue
    if quants_a and quants_b:
        # Check if ∀/∃ order is reversed
        if len(quants_a) >= 2 and len(quants_b) >= 2:
            if quants_a[:2] == ['∀', '∃'] and quants_b[:2] == ['∃', '∀']:
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['quantifier_scope_mismatch'],
                    reason="∀∃ → ∃∀ swap detected (programmatic)",
                ))
                breakdown.fatal_issues.append("∀∃ → ∃∀ quantifier swap")
            elif quants_a[:2] == ['∃', '∀'] and quants_b[:2] == ['∀', '∃']:
                breakdown.caps_applied.append(CapApplied(
                    cap=CAPS['quantifier_scope_mismatch'],
                    reason="∃∀ → ∀∃ swap detected (programmatic)",
                ))
                breakdown.fatal_issues.append("∃∀ → ∀∃ quantifier swap")

    # Significant quantifier count difference
    if quants_a or quants_b:
        count_diff = abs(len(quants_a) - len(quants_b))
        if count_diff >= 2:
            breakdown.caps_applied.append(CapApplied(
                cap=CAPS['assumption_added_or_removed'],
                reason=f"Quantifier count difference: {len(quants_a)} → {len(quants_b)}",
            ))


# ═══════════════════════════════════════════════════════════════════
# BACKWARD-COMPATIBLE API
# ═══════════════════════════════════════════════════════════════════

def compute_fidelity_breakdown(
    original: str,
    back_translation: str,
    ai_semantic_score: float = 1.0,
) -> FidelityBreakdown:
    """Compute fidelity breakdown — backward-compatible wrapper.

    DEPRECATED: Use compute_fidelity_v2() for richer diagnostics.

    This wrapper calls compute_fidelity_v2 but also restores the original v1
    weighted-average composite so existing callers do not silently change
    behavior when they still pass ai_semantic_score explicitly.
    """
    breakdown = compute_fidelity_v2(original, back_translation)

    # Preserve the caller-supplied semantic score.
    breakdown.ai_semantic_score = ai_semantic_score

    legacy_step_score = step_consistency(
        breakdown.steps_original,
        breakdown.steps_backtranslation,
    )
    weights = breakdown.weights
    breakdown.legacy_composite_override = (
        weights['variable'] * breakdown.variable_score +
        weights['quantifier'] * breakdown.quantifier_score +
        weights['step'] * legacy_step_score +
        weights['equation'] * breakdown.equation_score +
        weights['ai_semantic'] * ai_semantic_score
    )

    return breakdown


# ═══════════════════════════════════════════════════════════════════
# EXTRACTION FUNCTIONS (v1, preserved for backward compat + fallback)
# ═══════════════════════════════════════════════════════════════════

# ── Math Variable Extraction ──
# DEPRECATED: Use AI-based extraction in compare_auto() instead.

# Common single-letter math vars and subscripted forms
_VAR_PATTERN = re.compile(
    r"""
    (?<![a-zA-Z_])       # not preceded by letter/underscore
    ([a-zA-Z])           # single letter
    (?:                   # optional subscript
      [_]([a-zA-Z0-9]+)  #   underscore form: x_1, a_n
      |[₀-₉₊₋ₐₑₒₓₔ]+   #   unicode subscript
    )?
    (?![a-zA-Z_])        # not followed by letter/underscore
    """,
    re.VERBOSE,
)

# Words that look like variables but aren't
_STOPWORDS = {
    'a', 'i', 'A',  # articles / common words (kept if in math context)
    'is', 'if', 'or', 'an', 'be', 'by', 'of', 'in', 'so', 'it',
    'we', 'as', 'to', 'do', 'on', 'at', 'no',
    'the', 'and', 'for', 'not', 'but', 'are', 'was', 'has', 'let',
    'all', 'any', 'can', 'may', 'our', 'its', 'two', 'one',
    'then', 'that', 'this', 'such', 'with', 'from', 'have', 'each',
    'also', 'both', 'some', 'thus', 'hence', 'since', 'where',
    'there', 'every', 'which', 'their', 'these', 'those',
    'proof', 'step', 'claim', 'note',
}

# Symbols that are math variables in context
_MATH_SYMBOLS = re.compile(r'[α-ωΑ-Ω]')  # Greek letters


def extract_math_variables(text: str) -> set:
    """Extract likely mathematical variables from natural language proof text.

    DEPRECATED: This regex-based approach misses context. Prefer AI extraction.

    Returns a set of variable names found (lowercase normalized).
    """
    variables = set()

    # Find single-letter vars near math context (=, +, -, *, /, <, >)
    for line in text.splitlines():
        has_math = bool(re.search(r'[=+\-*/²³∀∃∈≤≥<>∧∨]', line))

        if has_math:
            for m in _VAR_PATTERN.finditer(line):
                var = m.group(1).lower()
                sub = m.group(2)
                if var not in _STOPWORDS or sub:
                    full_var = f"{var}_{sub}" if sub else var
                    variables.add(full_var)

        # Always extract Greek letters
        for m in _MATH_SYMBOLS.finditer(line):
            variables.add(m.group().lower())

    # Also extract from explicit patterns
    for m in re.finditer(r'(?:let|where|integer|variable|denote)\s+([a-zA-Z])\b', text, re.I):
        variables.add(m.group(1).lower())

    return variables


# ── Quantifier Extraction ──
# DEPRECATED: Only detects flat quantifier sequence, not scope/tree structure.

_QUANTIFIER_PATTERNS = [
    (r'\bfor\s+all\b', '∀'),
    (r'\bfor\s+every\b', '∀'),
    (r'\bfor\s+any\b', '∀'),
    (r'∀', '∀'),
    (r'\bthere\s+exist(?:s)?\b', '∃'),
    (r'∃', '∃'),
]


def extract_quantifiers(text: str) -> list:
    """Extract quantifiers (∀/∃) from text in order of appearance.

    DEPRECATED: Returns flat list, misses scope/nesting. Use AI-based extraction.
    """
    quantifiers = []
    text_lower = text.lower()

    occurrences = []
    for pattern, symbol in _QUANTIFIER_PATTERNS:
        for m in re.finditer(pattern, text_lower if symbol != '∀' or pattern.startswith(r'\b') else text):
            occurrences.append((m.start(), symbol))

    occurrences.sort(key=lambda x: x[0])
    prev_pos = -10
    for pos, sym in occurrences:
        if pos - prev_pos > 2:  # avoid double-counting same match
            quantifiers.append(sym)
            prev_pos = pos

    return quantifiers


# ── Equation Extraction ──
# DEPRECATED: Only matches LHS = RHS, misses inequalities and relations.

_EQUATION_PATTERN = re.compile(
    r"""
    ([a-zA-Zα-ω][a-zA-Z0-9_₀-₉]*    # LHS: variable or expression
    (?:\s*[+\-*/]\s*[a-zA-Zα-ω0-9_₀-₉()]+)*)  # optional operations
    \s*=\s*                              # equals sign
    ([a-zA-Zα-ω0-9_₀-₉()+\-*/\s]+)     # RHS
    """,
    re.VERBOSE,
)


def extract_equations(text: str) -> list:
    """Extract mathematical equations (LHS = RHS) from text.

    DEPRECATED: Only handles =, not <, ≤, ∈, ∣, etc.
    Also matches if only LHS OR RHS matches, which is fragile.
    Use AI-based relation-aware comparison instead.

    Returns list of (lhs, rhs) string pairs, normalized.
    """
    equations = []
    for m in _EQUATION_PATTERN.finditer(text):
        lhs = re.sub(r'\s+', '', m.group(1).strip())
        rhs = re.sub(r'\s+', '', m.group(2).strip())
        if len(lhs) >= 1 and len(rhs) >= 1:
            equations.append((lhs.lower(), rhs.lower()))
    return equations


# ── Step Counting ──

_STEP_PATTERNS = [
    r'(?:step|Step|STEP)\s*\d+',
    r'(?:first|second|third|fourth|fifth|then|next|therefore|hence|thus|finally)\b',
]


def count_proof_steps(text: str) -> int:
    """Estimate the number of proof steps in text.

    Uses heuristics: explicit step labels, transition words, sentence count.
    """
    # Try explicit step labels first
    explicit = len(re.findall(r'(?:step|Step|STEP)\s*\d+', text))
    if explicit >= 2:
        return explicit

    # Count logical transition indicators
    transitions = 0
    for pattern in _STEP_PATTERNS:
        transitions += len(re.findall(pattern, text))

    if transitions >= 2:
        return transitions

    # Fallback: count sentences with math content
    sentences = re.split(r'[.!?]\s+', text)
    math_sentences = sum(1 for s in sentences if re.search(r'[=∀∃<>≤≥]', s))
    return max(math_sentences, 1)


# ── Similarity Metrics (v1, preserved) ──

def variable_similarity(vars_a: set, vars_b: set) -> float:
    """Jaccard similarity of variable sets.

    DEPRECATED: Penalizes α-renaming. Use binder_score from AI diagnosis instead.
    """
    if not vars_a and not vars_b:
        return 1.0
    if not vars_a or not vars_b:
        return 0.0
    intersection = vars_a & vars_b
    union = vars_a | vars_b
    return len(intersection) / len(union)


def quantifier_consistency(quants_a: list, quants_b: list) -> float:
    """Measure quantifier sequence consistency.

    DEPRECATED: Compares flat sequences, not tree structure.
    """
    if not quants_a and not quants_b:
        return 1.0
    if not quants_a or not quants_b:
        return 0.0

    count_a = {q: quants_a.count(q) for q in set(quants_a)}
    count_b = {q: quants_b.count(q) for q in set(quants_b)}
    all_quants = set(count_a) | set(count_b)

    if not all_quants:
        return 1.0

    count_score = sum(
        min(count_a.get(q, 0), count_b.get(q, 0)) / max(count_a.get(q, 0), count_b.get(q, 0))
        for q in all_quants
    ) / len(all_quants)

    lcs_len = _lcs_length(quants_a, quants_b)
    order_score = (2 * lcs_len) / (len(quants_a) + len(quants_b)) if (quants_a or quants_b) else 1.0

    return 0.5 * count_score + 0.5 * order_score


def step_consistency(steps_a: int, steps_b: int) -> float:
    """Score based on step count similarity."""
    if steps_a == 0 and steps_b == 0:
        return 1.0
    return 1.0 - abs(steps_a - steps_b) / max(steps_a, steps_b)


def equation_similarity(eqs_a: list, eqs_b: list) -> float:
    """Score based on equation overlap.

    DEPRECATED: Matches if only LHS or RHS matches, which is fragile.
    """
    if not eqs_a and not eqs_b:
        return 1.0
    if not eqs_a or not eqs_b:
        return 0.0

    matched = 0
    used = set()
    for lhs_a, rhs_a in eqs_a:
        for j, (lhs_b, rhs_b) in enumerate(eqs_b):
            if j not in used and (lhs_a == lhs_b or rhs_a == rhs_b):
                matched += 1
                used.add(j)
                break

    total = max(len(eqs_a), len(eqs_b))
    return matched / total if total > 0 else 1.0


# ── Helper ──

def _lcs_length(a: list, b: list) -> int:
    """Longest common subsequence length."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    return dp[m][n]
