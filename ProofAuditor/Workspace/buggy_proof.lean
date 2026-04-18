import Mathlib

/--
The theorem statement formalizing: "The sum of two odd integers is even."

STEP 1 of the informal proof,
"Let `a` and `b` be two odd integers,"
is represented by the variables `a`, `b` and the hypotheses `ha : Odd a`, `hb : Odd b`.
-/
theorem sum_of_two_odd_integers_is_even (a b : ℤ) (ha : Odd a) (hb : Odd b) :
    Even (a + b) := by
  have hrepr : ∃ k l : ℤ, a = 2 * k + 1 ∧ b = 2 * l + 1 := by
    -- SORRY_ID: step2_repr
    -- STEP 2: "By definition of odd integers, there exists an integer k such that
    -- a = 2k + 1 and b = 2k + 1."
    -- ORIGINAL: Step 2 of the informal proof.
    -- FORMALIZATION NOTE: to express the valid general statement, we use
    -- possibly different witnesses `k` and `l` for `a` and `b`.
    sorry
  rcases hrepr with ⟨k, l, hk, hl⟩
  have hsum : a + b = (2 * k + 1) + (2 * l + 1) := by
    -- SORRY_ID: step3_sum
    -- STEP 3: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
    -- ORIGINAL: Step 3 of the informal proof.
    -- FORMALIZATION NOTE: the second odd witness is `l`.
    sorry
  have hrewrite : a + b = 2 * (k + l + 1) := by
    -- SORRY_ID: step4_rearrange
    -- STEP 4: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
    -- ORIGINAL: Step 4 of the informal proof.
    -- FORMALIZATION NOTE: with separate witnesses, the rearranged form is
    -- `a + b = 2 * (k + l + 1)`.
    sorry
  have heven : Even (a + b) := by
    -- SORRY_ID: step5_even
    -- STEP 5: "Since 2k + 1 is an integer, we conclude that a + b is even."
    -- ORIGINAL: Step 5 of the informal proof.
    -- FORMALIZATION NOTE: from `a + b = 2 * (k + l + 1)` we conclude that
    -- `a + b` has the form required for an even integer.
    sorry
  exact heven

/-
translation_map.yaml
theorem: sum_of_two_odd_integers_is_even
step_1:
  kind: theorem_arguments
  original_text: "Let a and b be two odd integers."
  lean_correspondence:
    - "a b : ℤ"
    - "ha : Odd a"
    - "hb : Odd b"
sorrys:
  - id: step2_repr
    lean_location: "have hrepr : ∃ k l : ℤ, a = 2 * k + 1 ∧ b = 2 * l + 1 := by sorry"
    original_step: 2
    original_text: "By definition of odd integers, there exists an integer k such that a = 2k + 1 and b = 2k + 1."
    note: "Formalized with separate witnesses k and l."
  - id: step3_sum
    lean_location: "have hsum : a + b = (2 * k + 1) + (2 * l + 1) := by sorry"
    original_step: 3
    original_text: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
    note: "Formalized with witnesses k and l."
  - id: step4_rearrange
    lean_location: "have hrewrite : a + b = 2 * (k + l + 1) := by sorry"
    original_step: 4
    original_text: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
    note: "Formalized with the correct rearrangement for separate witnesses."
  - id: step5_even
    lean_location: "have heven : Even (a + b) := by sorry"
    original_step: 5
    original_text: "Since 2k + 1 is an integer, we conclude that a + b is even."
    note: "Uses the witness obtained from the rearranged expression."
-/