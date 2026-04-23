"""Plan-vs-Goal Alignment — Round 2.1 of the audit pipeline.

Compares the ForwardTranslationPlan's step claims against Lean LSP sorry
goal states to detect translation fidelity issues WITHOUT back-translation.

Key advantage: the plan claims come from the JSON plan (Round 1), and the
sorry goals come from Lean LSP (Round 2). Both are structured representations,
so comparison is mostly deterministic — no extra AI calls for exact/α-equivalent
matches.

Usage:
    from core.plan_goal_alignment import align_plan_with_goals

    result = align_plan_with_goals(plan, sorry_goals, lean_code)
    print(result.overall_score)  # 0.0 to 1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

try:
    from core.forward_translator import ForwardTranslationPlan, ProofStepPlan
    from core.lean_lsp import SorryGoal
except Exception:
    pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data classes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class StepAlignment:
    """Alignment result for a single plan step vs LSP sorry goal."""

    step_id: str
    plan_claim: str = ""           # From ForwardTranslationPlan
    lean_goal: str = ""            # From LSP SorryGoal
    alignment_score: float = 0.0   # 0.0 to 1.0
    alignment_type: str = "unaligned"  # exact | alpha_equiv | minor_diff | major_diff | unaligned
    issues: list[str] = field(default_factory=list)
    sorry_line: int = 0


@dataclass
class PlanGoalAlignmentResult:
    """Complete alignment result for all plan steps vs LSP goals."""

    alignments: list[StepAlignment] = field(default_factory=list)
    overall_score: float = 0.0
    unmatched_plan_steps: list[str] = field(default_factory=list)
    unmatched_goals: list[str] = field(default_factory=list)
    structural_match: bool = True
    step_count_plan: int = 0
    step_count_goals: int = 0

    def summary(self) -> str:
        lines = [
            f"Plan-Goal Alignment: {self.overall_score:.0%}",
            f"  Plan steps: {self.step_count_plan}",
            f"  LSP sorry goals: {self.step_count_goals}",
            f"  Structural match: {self.structural_match}",
        ]
        for a in self.alignments:
            icon = {"exact": "✅", "alpha_equiv": "✅", "minor_diff": "⚠️",
                    "major_diff": "🔴", "unaligned": "❓"}.get(a.alignment_type, "•")
            lines.append(f"  {icon} {a.step_id}: {a.alignment_type} ({a.alignment_score:.0%})")
            if a.issues:
                for issue in a.issues:
                    lines.append(f"      → {issue}")
        if self.unmatched_plan_steps:
            lines.append(f"  Unmatched plan steps: {', '.join(self.unmatched_plan_steps)}")
        if self.unmatched_goals:
            lines.append(f"  Unmatched LSP goals: {', '.join(self.unmatched_goals)}")
        return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Proposition normalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Unicode → ASCII normalization for Lean propositions
_UNICODE_MAP = {
    "ℤ": "Int", "ℕ": "Nat", "ℝ": "Real", "ℂ": "Complex",
    "→": "->", "←": "<-", "↔": "<->",
    "∀": "forall", "∃": "exists",
    "∧": "/\\", "∨": "\\/",
    "≤": "<=", "≥": ">=", "≠": "!=",
    "α": "alpha", "β": "beta", "γ": "gamma",
    "δ": "delta", "ε": "epsilon", "σ": "sigma",
    "μ": "mu", "ν": "nu", "λ": "lambda",
    "π": "pi", "ω": "omega", "φ": "phi", "ψ": "psi",
    "∈": "mem", "∉": "not_mem", "⊆": "subset",
}

# Variables that are semantically equivalent under α-renaming
_BINDER_RE = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*:', re.UNICODE)


def normalize_proposition(prop: str) -> str:
    """Normalize a Lean proposition for comparison.

    - Strip whitespace
    - Normalize unicode to ASCII equivalents
    - Collapse multiple spaces
    - Remove surrounding parentheses
    """
    text = prop.strip()
    if not text:
        return ""

    # Unicode normalization
    for u, a in _UNICODE_MAP.items():
        text = text.replace(u, a)

    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)

    # Remove outer parens if balanced
    while len(text) > 2 and text[0] == '(' and text[-1] == ')':
        inner = text[1:-1]
        depth = 0
        balanced = True
        for ch in inner:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth < 0:
                balanced = False
                break
        if balanced and depth == 0:
            text = inner.strip()
        else:
            break

    return text


def extract_bound_variables(prop: str) -> list[str]:
    """Extract bound variable names from a proposition."""
    return _BINDER_RE.findall(prop)


def is_alpha_equivalent(prop_a: str, prop_b: str) -> bool:
    """Check if two propositions are α-equivalent (same structure, different var names).

    Simple heuristic: replace bound variable names with canonical names and compare.
    """
    norm_a = normalize_proposition(prop_a)
    norm_b = normalize_proposition(prop_b)

    if norm_a == norm_b:
        return True  # exact match, trivially α-equivalent

    vars_a = extract_bound_variables(norm_a)
    vars_b = extract_bound_variables(norm_b)

    if len(vars_a) != len(vars_b):
        return False

    # Build renaming map
    canonical_a = norm_a
    canonical_b = norm_b
    for i, (va, vb) in enumerate(zip(vars_a, vars_b)):
        canon = f"_v{i}_"
        # Replace whole-word only
        canonical_a = re.sub(rf'\b{re.escape(va)}\b', canon, canonical_a)
        canonical_b = re.sub(rf'\b{re.escape(vb)}\b', canon, canonical_b)

    return canonical_a == canonical_b


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step ID → sorry line mapping
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_SORRY_ID_RE = re.compile(r'--\s*SORRY_ID:\s*(\S+)')


def build_step_id_to_line_map(lean_code: str) -> dict[str, int]:
    """Build a mapping from step_id to the line number of the corresponding sorry.

    Scans the Lean code for `-- SORRY_ID: <step_id>` comments and finds the
    next `sorry` on a subsequent line.
    """
    lines = lean_code.splitlines()
    step_to_line: dict[str, int] = {}
    pending_step_id: Optional[str] = None

    for i, line in enumerate(lines, 1):  # 1-indexed
        m = _SORRY_ID_RE.search(line)
        if m:
            pending_step_id = m.group(1)

        if pending_step_id and 'sorry' in line.strip():
            step_to_line[pending_step_id] = i
            pending_step_id = None

    return step_to_line


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core alignment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _compare_propositions(plan_claim: str, lean_goal: str) -> StepAlignment:
    """Compare a plan claim with a Lean goal and produce an alignment."""

    norm_claim = normalize_proposition(plan_claim)
    norm_goal = normalize_proposition(lean_goal)

    # Empty claim means the plan step had no explicit claim
    if not norm_claim:
        return StepAlignment(
            step_id="",
            plan_claim=plan_claim,
            lean_goal=lean_goal,
            alignment_score=0.5,
            alignment_type="minor_diff",
            issues=["Plan step has no explicit claim; cannot compare with Lean goal."],
        )

    # Exact match after normalization
    if norm_claim == norm_goal:
        return StepAlignment(
            step_id="",
            plan_claim=plan_claim,
            lean_goal=lean_goal,
            alignment_score=1.0,
            alignment_type="exact",
        )

    # α-equivalence check
    if is_alpha_equivalent(plan_claim, lean_goal):
        return StepAlignment(
            step_id="",
            plan_claim=plan_claim,
            lean_goal=lean_goal,
            alignment_score=0.95,
            alignment_type="alpha_equiv",
            issues=["Variable names differ but structure is equivalent."],
        )

    # Check if one is a substring/prefix of the other (common for minor type differences)
    if norm_claim in norm_goal or norm_goal in norm_claim:
        return StepAlignment(
            step_id="",
            plan_claim=plan_claim,
            lean_goal=lean_goal,
            alignment_score=0.7,
            alignment_type="minor_diff",
            issues=[f"One proposition contains the other. Plan: '{plan_claim[:60]}', Goal: '{lean_goal[:60]}'."],
        )

    # Token overlap heuristic
    tokens_claim = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', norm_claim))
    tokens_goal = set(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', norm_goal))

    if tokens_claim and tokens_goal:
        overlap = len(tokens_claim & tokens_goal) / max(len(tokens_claim), len(tokens_goal))
    else:
        overlap = 0.0

    if overlap >= 0.7:
        return StepAlignment(
            step_id="",
            plan_claim=plan_claim,
            lean_goal=lean_goal,
            alignment_score=0.6,
            alignment_type="minor_diff",
            issues=[f"High token overlap ({overlap:.0%}) but not structurally equivalent."],
        )

    # Major difference
    return StepAlignment(
        step_id="",
        plan_claim=plan_claim,
        lean_goal=lean_goal,
        alignment_score=0.2,
        alignment_type="major_diff",
        issues=[
            f"Plan claim and Lean goal differ significantly.",
            f"  Plan: {plan_claim[:80]}",
            f"  Goal: {lean_goal[:80]}",
        ],
    )


def align_plan_with_goals(
    plan: ForwardTranslationPlan,
    sorry_goals: list[SorryGoal],
    lean_code: str,
) -> PlanGoalAlignmentResult:
    """Align ForwardTranslationPlan steps with LSP sorry goals.

    This is the main entry point for Round 2.1.

    Args:
        plan: The structured translation plan from Round 1.
        sorry_goals: Sorry goal states from Round 2 (LSP analysis).
        lean_code: The rendered Lean code (used for step_id → line mapping).

    Returns:
        PlanGoalAlignmentResult with per-step alignments and overall score.
    """
    result = PlanGoalAlignmentResult(
        step_count_plan=len(plan.proof_steps),
        step_count_goals=len(sorry_goals),
    )

    if not plan.proof_steps and not sorry_goals:
        result.overall_score = 1.0
        result.structural_match = True
        return result

    # Build step_id → sorry line mapping from Lean code
    step_to_line = build_step_id_to_line_map(lean_code)

    # Build sorry line → SorryGoal mapping
    line_to_goal: dict[int, SorryGoal] = {}
    for sg in sorry_goals:
        line_to_goal[sg.line] = sg

    # Also try nearby lines (±2) for fuzzy matching
    def find_goal_for_line(target_line: int) -> Optional[SorryGoal]:
        if target_line in line_to_goal:
            return line_to_goal[target_line]
        for delta in [1, -1, 2, -2]:
            if target_line + delta in line_to_goal:
                return line_to_goal[target_line + delta]
        return None

    matched_goal_lines: set[int] = set()
    alignments: list[StepAlignment] = []

    for step in plan.proof_steps:
        sorry_line = step_to_line.get(step.step_id, 0)
        goal = find_goal_for_line(sorry_line) if sorry_line else None

        if goal is None:
            # Plan step has no corresponding sorry goal
            alignment = StepAlignment(
                step_id=step.step_id,
                plan_claim=step.claim,
                lean_goal="",
                alignment_score=0.0,
                alignment_type="unaligned",
                issues=["No corresponding sorry goal found in LSP output."],
                sorry_line=sorry_line,
            )
            result.unmatched_plan_steps.append(step.step_id)
        else:
            alignment = _compare_propositions(step.claim, goal.goal)
            alignment.step_id = step.step_id
            alignment.sorry_line = goal.line
            matched_goal_lines.add(goal.line)

        alignments.append(alignment)

    # Find unmatched LSP goals
    for sg in sorry_goals:
        if sg.line not in matched_goal_lines:
            result.unmatched_goals.append(f"L{sg.line}")

    result.alignments = alignments

    # Structural match: same count and no unmatched items
    result.structural_match = (
        len(result.unmatched_plan_steps) == 0
        and len(result.unmatched_goals) == 0
    )

    # Overall score
    if alignments:
        total_score = sum(a.alignment_score for a in alignments)
        result.overall_score = total_score / len(alignments)
    else:
        result.overall_score = 1.0 if not sorry_goals else 0.0

    # Penalize structural mismatch
    if not result.structural_match:
        unmatched_count = len(result.unmatched_plan_steps) + len(result.unmatched_goals)
        total_items = max(len(plan.proof_steps), len(sorry_goals), 1)
        penalty = 1.0 - (unmatched_count / (2 * total_items))
        result.overall_score *= max(0.3, penalty)

    return result
