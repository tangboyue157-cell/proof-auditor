"""Tests for core.forward_translator — structured forward translation pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from core.forward_translator import (
    ForwardTranslationPlan,
    forward_translate,
    parse_forward_translation_plan,
    render_plan_to_lean,
    validate_forward_translation_plan,
)


@dataclass
class DummyResponse:
    content: str


class DummyClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.provider = "dummy"
        self.model = "dummy-model"

    def chat(self, prompt: str):  # noqa: ARG002
        if not self.responses:
            raise AssertionError("No more responses queued")
        return DummyResponse(self.responses.pop(0))


def test_forward_translate_renders_auditable_lean_skeleton():
    response = r'''
    {
      "imports": ["Mathlib"],
      "namespace": "Audit",
      "theorem_name": "odd_add_odd",
      "binders": [
        {"name": "a", "type": "ℤ", "role": "binder"},
        {"name": "b", "type": "ℤ", "role": "binder"},
        {"name": "ha", "type": "Odd a", "role": "hypothesis"},
        {"name": "hb", "type": "Odd b", "role": "hypothesis"}
      ],
      "conclusion": "Even (a + b)",
      "proof_steps": [
        {
          "step_id": "s1",
          "original_text": "Expand oddness of a.",
          "claim": "∃ k : ℤ, a = 2 * k + 1",
          "depends_on": ["ha"],
          "introduces": ["k", "hk"],
          "reason": "unfold Odd",
          "status": "sorry",
          "lean_code": ""
        },
        {
          "step_id": "s2",
          "original_text": "Conclude the target.",
          "claim": "Even (a + b)",
          "depends_on": ["s1", "hb"],
          "introduces": [],
          "reason": "combine witnesses",
          "status": "sorry",
          "lean_code": ""
        }
      ],
      "final_proof": "sorry",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    '''
    client = DummyClient([response])
    result = forward_translate(client, "Let a and b be odd integers. Then a + b is even.")

    assert not result.fatal_issues
    assert "theorem odd_add_odd" in result.lean_code
    assert "-- SORRY_ID: s1" in result.lean_code
    assert "have s1 : ∃ k : ℤ, a = 2 * k + 1 := by" in result.lean_code
    assert "-- AMBIGUITY_LEDGER: []" in result.lean_code
    assert result.requires_human_review is False


def test_introduced_assumption_triggers_review():
    response = r'''
    {
      "theorem_name": "needs_assumption",
      "binders": [{"name": "n", "type": "Nat", "role": "binder"}],
      "conclusion": "n = n",
      "proof_steps": [],
      "final_proof": "rfl",
      "ambiguities": [],
      "introduced_assumptions": ["n > 0"]
    }
    '''
    client = DummyClient([response])
    result = forward_translate(client, "We assume n is positive and conclude n = n.")

    assert result.requires_human_review is True
    assert any(issue.code == "introduced_assumptions" for issue in result.issues)


def test_unknown_dependency_is_fatal():
    plan = parse_forward_translation_plan(
        r'''
        {
          "theorem_name": "bad_dep",
          "binders": [{"name": "h", "type": "True", "role": "hypothesis"}],
          "conclusion": "True",
          "proof_steps": [
            {
              "step_id": "s2",
              "original_text": "Use a missing step.",
              "claim": "True",
              "depends_on": ["s9"],
              "introduces": [],
              "reason": "",
              "status": "sorry",
              "lean_code": ""
            }
          ],
          "final_proof": "trivial",
          "ambiguities": [],
          "introduced_assumptions": []
        }
        '''
    )
    issues = validate_forward_translation_plan(plan)

    assert any(issue.code == "unknown_dependency" and issue.severity == "fatal" for issue in issues)


def test_compile_repair_loop_uses_repaired_plan():
    initial = r'''
    {
      "theorem_name": "demo",
      "binders": [{"name": "h", "type": "True", "role": "hypothesis"}],
      "conclusion": "True",
      "proof_steps": [],
      "final_proof": "bad_tactic",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    '''
    repaired = r'''
    {
      "theorem_name": "demo",
      "binders": [{"name": "h", "type": "True", "role": "hypothesis"}],
      "conclusion": "True",
      "proof_steps": [],
      "final_proof": "exact h",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    '''
    client = DummyClient([initial, repaired])

    calls = []

    def compile_callback(lean_code: str):
        calls.append(lean_code)
        if "bad_tactic" in lean_code:
            return False, ["unknown tactic 'bad_tactic'"]
        return True, []

    result = forward_translate(
        client,
        "Assume h : True. Then True.",
        compile_callback=compile_callback,
        max_repair_rounds=1,
    )

    assert result.compiles is True
    assert result.repaired_rounds == 1
    assert "exact h" in result.lean_code
    assert len(calls) == 2


def test_mathlib_context_injected_for_probability():
    """The forward translator should automatically detect probability domain."""
    response = r'''
    {
      "theorem_name": "clt_example",
      "binders": [
        {"name": "X", "type": "Ω → ℝ", "role": "binder"},
        {"name": "hX", "type": "AEStronglyMeasurable X ℙ", "role": "hypothesis"}
      ],
      "conclusion": "True",
      "proof_steps": [],
      "final_proof": "trivial",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    '''
    client = DummyClient([response])
    result = forward_translate(
        client,
        "Let X be a random variable with finite variance. "
        "By the Central Limit Theorem, the distribution converges.",
    )

    # Should have detected probability domain
    assert result.mathlib_context  # non-empty
    assert "ProbabilityTheory" in result.mathlib_context
    # Should include CLT warning
    assert "CLT" in result.mathlib_context


def test_parse_plan_from_json_fenced():
    """JSON wrapped in markdown fences should parse correctly."""
    text = '''Some intro text
```json
{
  "theorem_name": "test",
  "binders": [],
  "conclusion": "True",
  "proof_steps": [],
  "final_proof": "trivial",
  "ambiguities": [],
  "introduced_assumptions": []
}
```
'''
    plan = parse_forward_translation_plan(text)
    assert plan.parse_ok is True
    assert plan.theorem_name == "test"
    assert plan.conclusion == "True"


def test_parse_plan_failure_returns_safe_default():
    """Unparseable response should not crash."""
    plan = parse_forward_translation_plan("This is not JSON at all.")
    assert plan.parse_ok is False
    assert plan.theorem_name == "audit_theorem"  # default


def test_render_plan_has_all_sorry_annotations():
    """Rendered Lean should have SORRY_ID, STEP, and CLAIMED_REASON for every step."""
    plan = parse_forward_translation_plan(r'''
    {
      "theorem_name": "annotated",
      "binders": [{"name": "n", "type": "ℕ", "role": "binder"}],
      "conclusion": "n = n",
      "proof_steps": [
        {
          "step_id": "s1",
          "original_text": "By reflexivity.",
          "claim": "n = n",
          "depends_on": [],
          "introduces": [],
          "reason": "reflexivity",
          "status": "sorry",
          "lean_code": ""
        }
      ],
      "final_proof": "exact s1",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    ''')
    lean = render_plan_to_lean(plan)
    assert "-- SORRY_ID: s1" in lean
    assert '-- STEP 1: "By reflexivity."' in lean
    assert '-- CLAIMED_REASON: "reflexivity"' in lean
    assert "have s1 : n = n := by" in lean
    assert "    sorry" in lean
    assert "exact s1" in lean


def test_validate_duplicate_binder():
    plan = parse_forward_translation_plan(r'''
    {
      "theorem_name": "dup",
      "binders": [
        {"name": "x", "type": "ℕ", "role": "binder"},
        {"name": "x", "type": "ℤ", "role": "binder"}
      ],
      "conclusion": "True",
      "proof_steps": [],
      "final_proof": "trivial",
      "ambiguities": [],
      "introduced_assumptions": []
    }
    ''')
    issues = validate_forward_translation_plan(plan)
    assert any(i.code == "duplicate_binder" for i in issues)
