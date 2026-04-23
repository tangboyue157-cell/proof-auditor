import Mathlib

/-
AMBIGUITY_LEDGER:
- The phrases "odd integers" and "even" could be formalized either with Lean predicates
  such as `Odd` / `Even`, or by explicit existential formulas matching the proof text.
  CHOICE: use explicit existential formulas:
    `∃ k : ℤ, a = 2 * k + 1`,
    `∃ k : ℤ, b = 2 * k + 1`,
    `∃ m : ℤ, a + b = 2 * m`.
  ALTERNATIVE: use `Odd a`, `Odd b`, and `Even (a + b)`.
- Step 2 says: "there exists an integer k such that a = 2k + 1 and b = 2k + 1."
  This could be read literally as one shared witness `k`, or informally as two separate witnesses.
  CHOICE: one shared witness `k` (literal reading of the original).
  ALTERNATIVE: separate witnesses `k₁` and `k₂` (mathematically standard, but not faithful to the text).
- Step 4 writes the chain equality "a + b = 4k + 2 = 2(2k + 1)".
  CHOICE: represent this as the conjunction
    `a + b = 4 * k + 2 ∧ 4 * k + 2 = 2 * (2 * k + 1)`.
  ALTERNATIVE: represent only the final equality and keep the intermediate equality in a comment.
-/

/-
INTRODUCED_ASSUMPTIONS:
- NONE (all hypotheses come directly from the original text)
-/

/-
translation_map.yaml:
  step2_shared_k:
    original_step: 2
    original_text: "By definition of odd integers, there exists an integer k such that a = 2k + 1 and b = 2k + 1."
    claimed_reason: "definition of odd integers"
  step3_sum_substitution:
    original_step: 3
    original_text: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
    claimed_reason: "Therefore"
  step4_rearrangement:
    original_step: 4
    original_text: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
    claimed_reason: "By rearranging the terms"
  step5_even_conclusion:
    original_step: 5
    original_text: "Since 2k + 1 is an integer, we conclude that a + b is even."
    claimed_reason: "Since 2k + 1 is an integer"
-/

theorem sum_of_two_odd_integers_is_even
    (a b : ℤ)
    (ha : ∃ k : ℤ, a = 2 * k + 1)
    (hb : ∃ k : ℤ, b = 2 * k + 1) :
    ∃ m : ℤ, a + b = 2 * m := by
  -- STEP 1: "Let a and b be two odd integers."

  -- NOTE: The original proof uses one shared integer `k` for both `a` and `b`.
  -- This literal reading is preserved below.
  -- SORRY_ID: step2_shared_k
  -- STEP 2: "By definition of odd integers, there exists an integer k such that a = 2k + 1 and b = 2k + 1."
  -- CLAIMED_REASON: "definition of odd integers"
  have h2 : ∃ k : ℤ, a = 2 * k + 1 ∧ b = 2 * k + 1 := by
    sorry

  rcases h2 with ⟨k, hk_a, hk_b⟩

  -- SORRY_ID: step3_sum_substitution
  -- STEP 3: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
  -- CLAIMED_REASON: "Therefore"
  have h3 : a + b = (2 * k + 1) + (2 * k + 1) := by
    sorry

  -- SORRY_ID: step4_rearrangement
  -- STEP 4: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
  -- CLAIMED_REASON: "By rearranging the terms"
  have h4 : a + b = 4 * k + 2 ∧ 4 * k + 2 = 2 * (2 * k + 1) := by
    sorry

  -- SORRY_ID: step5_even_conclusion
  -- STEP 5: "Since 2k + 1 is an integer, we conclude that a + b is even."
  -- CLAIMED_REASON: "Since 2k + 1 is an integer"
  have h5 : ∃ m : ℤ, a + b = 2 * m := by
    sorry

  exact h5