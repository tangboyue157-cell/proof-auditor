import Mathlib

theorem sum_of_two_odd_integers_is_even (a b : ℤ) (ha : Odd a) (hb : Odd b) :
    Even (a + b) := by
  -- STEP 1: "Let a and b be two odd integers."
  -- ORIGINAL: "Let a and b be two odd integers."
  -- This is represented by the integer variables `a`, `b` and the hypotheses
  -- `ha : Odd a`, `hb : Odd b`.

  -- Note: the original proof uses a single witness `k` for both `a` and `b`;
  -- the formalization below preserves that step literally.
  have hrep : ∃ k : ℤ, a = 2 * k + 1 ∧ b = 2 * k + 1 := by
    -- STEP 2: "By definition of odd integers, there exists an integer k such that
    -- a = 2k + 1 and b = 2k + 1."
    -- ORIGINAL: "By definition of odd integers, there exists an integer k such that a = 2k + 1 and b = 2k + 1."
    sorry

  rcases hrep with ⟨k, hk_a, hk_b⟩

  have hsum1 : a + b = (2 * k + 1) + (2 * k + 1) := by
    -- STEP 3: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
    -- ORIGINAL: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
    sorry

  have hsum2 : a + b = 4 * k + 2 ∧ 4 * k + 2 = 2 * (2 * k + 1) := by
    -- STEP 4: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
    -- ORIGINAL: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
    sorry

  have hsum2' : a + b = 2 * (2 * k + 1) := by
    rcases hsum2 with ⟨hleft, hright⟩
    exact hleft.trans hright

  have heven : Even (a + b) := by
    -- STEP 5: "Since 2k + 1 is an integer, we conclude that a + b is even."
    -- ORIGINAL: "Since 2k + 1 is an integer, we conclude that a + b is even."
    -- This step uses the rewritten form `hsum2'`.
    sorry

  exact heven

/-
translation_map.yaml
theorem: sum_of_two_odd_integers_is_even
mappings:
  step_1:
    lean_location: "theorem parameters `a`, `b`, `ha`, `hb`"
    original: "Let a and b be two odd integers."
    sorry: none
  step_2:
    lean_location: "have hrep"
    original: "By definition of odd integers, there exists an integer k such that a = 2k + 1 and b = 2k + 1."
  step_3:
    lean_location: "have hsum1"
    original: "Therefore, their sum is a + b = (2k + 1) + (2k + 1)."
  step_4:
    lean_location: "have hsum2"
    original: "By rearranging the terms, we get a + b = 4k + 2 = 2(2k + 1)."
  step_5:
    lean_location: "have heven"
    original: "Since 2k + 1 is an integer, we conclude that a + b is even."
-/