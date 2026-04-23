import sys
import textwrap
import types

import pytest


class DummyResponse:
    def __init__(self, content: str):
        self.content = content


class DummyClient:
    provider = "dummy"
    model = "dummy-model"

    def __init__(self, content: str):
        self._content = content

    def chat(self, _prompt: str) -> DummyResponse:
        return DummyResponse(self._content)


@pytest.fixture(autouse=True)
def stub_ai_client_module(monkeypatch):
    module = types.ModuleType("core.ai_client")
    module.AIClient = DummyClient
    monkeypatch.setitem(sys.modules, "core.ai_client", module)


def test_sanitize_strips_inline_block_comments_and_ledgers():
    from core.back_translator import sanitize_lean_for_backtranslation

    lean = textwrap.dedent(
        """
        theorem foo : True := by
          -- AMBIGUITY_LEDGER
          --   original: "foo"
          -- SORRY_ID: s1
          -- STEP 1: "from triviality"
          have h : True := by /- original proof text -/
            trivial -- leaked comment
          exact h
        """
    ).strip()

    sanitized = sanitize_lean_for_backtranslation(lean)
    assert "original proof text" not in sanitized
    assert "AMBIGUITY_LEDGER" not in sanitized
    assert "leaked comment" not in sanitized
    assert "-- AUDIT_STEP_ID: s1" in sanitized


def test_proof_structure_parent_and_sorry_not_contaminated_by_child():
    from core.proof_structure import analyze_proof_structure

    lean = textwrap.dedent(
        """
        theorem foo (ha : True) : True := by
          -- SORRY_ID: s1
          have h1 : True := by
            exact ha
          -- SORRY_ID: s2
          have h2 : True := by
            have h3 : True := by
              sorry
            exact h3
          exact h2
        """
    ).strip()

    structure = analyze_proof_structure(lean)
    steps = {step.name: step for step in structure.steps}

    assert steps["h1"].sorry_id == "s1"
    assert steps["h2"].sorry_id == "s2"
    assert steps["h3"].parent == "h2"
    assert steps["h2"].has_sorry is False
    assert steps["h3"].has_sorry is True
    assert steps["h3"].end_line < steps["h2"].end_line


def test_compare_auto_returns_unknown_on_json_parse_failure():
    from core.back_translator import BackTranslationArtifact, compare_auto

    client = DummyClient("this is not valid json")
    artifact = BackTranslationArtifact(
        proof_text="Proof.",
        summary={"theorem": "T", "proof_steps": []},
        raw_text="raw",
        parse_ok=True,
    )

    result = compare_auto(client, "Original proof.", artifact)

    assert result.review_status == "unknown"
    assert result.parse_failed is True
    assert result.fidelity_score is None
    assert result.requires_human is True


def test_compute_fidelity_breakdown_preserves_v1_ai_semantic_weight():
    from core.fidelity import compute_fidelity_breakdown

    low = compute_fidelity_breakdown("a = b", "a = b", ai_semantic_score=0.0)
    high = compute_fidelity_breakdown("a = b", "a = b", ai_semantic_score=1.0)

    assert low.composite_score < high.composite_score
