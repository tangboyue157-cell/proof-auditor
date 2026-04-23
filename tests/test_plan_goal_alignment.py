"""Tests for core.plan_goal_alignment — Plan-vs-Goal direct alignment."""

from __future__ import annotations

from dataclasses import dataclass

from core.plan_goal_alignment import (
    PlanGoalAlignmentResult,
    StepAlignment,
    align_plan_with_goals,
    build_step_id_to_line_map,
    is_alpha_equivalent,
    normalize_proposition,
)
from core.forward_translator import (
    ForwardTranslationPlan,
    ProofBinder,
    ProofStepPlan,
)


@dataclass
class FakeSorryGoal:
    """Stand-in for core.lean_lsp.SorryGoal in tests."""
    file: str = "test.lean"
    line: int = 0
    column: int = 0
    goal: str = ""
    context: str = ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Normalization tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNormalization:
    def test_whitespace(self):
        assert normalize_proposition("  a  +  b  ") == "a + b"

    def test_unicode_to_ascii(self):
        assert "Int" in normalize_proposition("ℤ")
        assert "Nat" in normalize_proposition("ℕ")
        assert "forall" in normalize_proposition("∀ x : ℤ, x > 0")

    def test_outer_parens_removed(self):
        assert normalize_proposition("(a + b)") == "a + b"
        assert normalize_proposition("((a + b))") == "a + b"

    def test_unbalanced_parens_preserved(self):
        result = normalize_proposition("(a + b) + (c + d)")
        assert "+" in result

    def test_empty(self):
        assert normalize_proposition("") == ""
        assert normalize_proposition("   ") == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Alpha equivalence tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAlphaEquivalence:
    def test_exact_match(self):
        assert is_alpha_equivalent("∃ k : ℤ, a = 2 * k + 1", "∃ k : ℤ, a = 2 * k + 1")

    def test_variable_renaming(self):
        assert is_alpha_equivalent(
            "∃ k : ℤ, a = 2 * k + 1",
            "∃ j : ℤ, a = 2 * j + 1",
        )

    def test_different_structure(self):
        assert not is_alpha_equivalent(
            "∃ k : ℤ, a = 2 * k + 1",
            "∀ k : ℤ, a = 2 * k + 1",
        )

    def test_different_var_count(self):
        assert not is_alpha_equivalent(
            "∃ k : ℤ, a = 2 * k + 1",
            "∃ k : ℤ, ∃ j : ℤ, a = 2 * k + 1 ∧ b = 2 * j + 1",
        )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step ID mapping tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestStepIdMapping:
    def test_basic_mapping(self):
        lean = """\
import Mathlib

theorem test (n : ℕ) : n = n := by
  -- SORRY_ID: s1
  -- STEP 1: "reflexivity"
  have s1 : n = n := by
    sorry

  -- SORRY_ID: s2
  have s2 : True := by
    sorry

  exact s1
"""
        mapping = build_step_id_to_line_map(lean)
        assert "s1" in mapping
        assert "s2" in mapping
        assert mapping["s1"] == 7   # sorry on line 7
        assert mapping["s2"] == 11  # sorry on line 11

    def test_empty_code(self):
        assert build_step_id_to_line_map("") == {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Full alignment tests
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAlignment:
    def _make_plan(self, steps: list[tuple[str, str]]) -> ForwardTranslationPlan:
        """Helper: create a plan with given (step_id, claim) pairs."""
        return ForwardTranslationPlan(
            theorem_name="test",
            conclusion="True",
            proof_steps=[
                ProofStepPlan(step_id=sid, claim=claim)
                for sid, claim in steps
            ],
        )

    def _make_lean(self, step_ids: list[str]) -> str:
        """Helper: create Lean code with sorry markers."""
        lines = ["import Mathlib", "", "theorem test : True := by"]
        for sid in step_ids:
            lines.append(f"  -- SORRY_ID: {sid}")
            lines.append(f"  have {sid} : True := by")
            lines.append("    sorry")
            lines.append("")
        lines.append("  trivial")
        return "\n".join(lines)

    def test_exact_match(self):
        plan = self._make_plan([("s1", "n = n"), ("s2", "True")])
        goals = [
            FakeSorryGoal(line=6, goal="n = n"),
            FakeSorryGoal(line=10, goal="True"),
        ]
        lean = self._make_lean(["s1", "s2"])
        result = align_plan_with_goals(plan, goals, lean)

        assert result.overall_score >= 0.95
        assert result.structural_match
        assert all(a.alignment_type == "exact" for a in result.alignments)

    def test_alpha_equivalent(self):
        plan = self._make_plan([("s1", "∃ k : ℤ, a = 2 * k + 1")])
        goals = [FakeSorryGoal(line=6, goal="∃ j : ℤ, a = 2 * j + 1")]
        lean = self._make_lean(["s1"])
        result = align_plan_with_goals(plan, goals, lean)

        assert result.overall_score >= 0.9
        assert result.alignments[0].alignment_type == "alpha_equiv"

    def test_major_difference(self):
        plan = self._make_plan([("s1", "Even (a + b)")])
        goals = [FakeSorryGoal(line=6, goal="Odd (a * b)")]
        lean = self._make_lean(["s1"])
        result = align_plan_with_goals(plan, goals, lean)

        assert result.overall_score < 0.5
        assert result.alignments[0].alignment_type == "major_diff"

    def test_unmatched_plan_step(self):
        plan = self._make_plan([("s1", "True"), ("s2", "False")])
        goals = [FakeSorryGoal(line=6, goal="True")]
        lean = self._make_lean(["s1"])  # s2 not in lean
        result = align_plan_with_goals(plan, goals, lean)

        assert "s2" in result.unmatched_plan_steps
        assert not result.structural_match
        assert result.overall_score < 1.0

    def test_empty_plan_and_goals(self):
        plan = self._make_plan([])
        result = align_plan_with_goals(plan, [], "import Mathlib\ntheorem t : True := trivial")

        assert result.overall_score == 1.0
        assert result.structural_match

    def test_plan_with_no_claim(self):
        """Steps without explicit claims should get minor_diff, not crash."""
        plan = self._make_plan([("s1", "")])
        goals = [FakeSorryGoal(line=6, goal="n = n")]
        lean = self._make_lean(["s1"])
        result = align_plan_with_goals(plan, goals, lean)

        assert result.alignments[0].alignment_type == "minor_diff"
        assert result.alignments[0].alignment_score == 0.5

    def test_containment_gives_minor_diff(self):
        plan = self._make_plan([("s1", "a = 2 * k + 1")])
        goals = [FakeSorryGoal(line=6, goal="∃ k : ℤ, a = 2 * k + 1")]
        lean = self._make_lean(["s1"])
        result = align_plan_with_goals(plan, goals, lean)

        # Plan claim is contained in the goal
        assert result.alignments[0].alignment_type == "minor_diff"
        assert result.alignments[0].alignment_score >= 0.6
