# Mathlib Reference: Probability Theory

## Random Variables and Distributions

### Probability Spaces
```
MeasureTheory.IsProbabilityMeasure (μ : Measure Ω) : Prop
  -- μ(univ) = 1
ProbabilityTheory  -- namespace for probability-specific API
```

### Distributions
```
MeasureTheory.Measure.map (X : Ω → ℝ) (ℙ : Measure Ω) : Measure ℝ  -- distribution of X
ProbabilityTheory.IdentDistrib (X Y : Ω → ℝ) (μ ν : Measure Ω) : Prop  -- same distribution
```

**Translation trap**: Mathlib has NO standalone "random variable" type.
A random variable is just a measurable function `X : Ω → ℝ`.
"X ~ N(μ,σ²)" is expressed as: `Measure.map X ℙ = gaussianReal μ σ²`

### Independence
```
ProbabilityTheory.IndepFun (X Y : Ω → ℝ) (μ : Measure Ω) : Prop
ProbabilityTheory.iIndepFun (f : ι → Ω → ℝ) (μ : Measure Ω) : Prop  -- i.i.d. family
```

## Expectation and Moments

### Expectation
```
-- E[X] is simply:
∫ ω, X ω ∂ℙ

-- E[f(X)] = ∫ f dμ_X (change of variables):
MeasureTheory.integral_map (hf : AEMeasurable X ℙ) (g : ℝ → ℝ)
```

**Translation trap**: There is NO `Expectation X` function in Mathlib.
Expectation is `∫ ω, X ω ∂ℙ` (the Bochner integral).

### Variance
```
ProbabilityTheory.variance (X : Ω → ℝ) (μ : Measure Ω) : ℝ
  -- Var(X) = E[(X - E[X])²]
ProbabilityTheory.variance_def'
  -- variance X μ = ∫ x, (x - ∫ x, x ∂μ) ^ 2 ∂μ
ProbabilityTheory.variance_nonneg
ProbabilityTheory.variance_add   -- Var(X+Y) when independent
```

### Moments and Characteristic Functions
```
ProbabilityTheory.moment (X : Ω → ℝ) (n : ℕ) (μ : Measure Ω) : ℝ
ProbabilityTheory.centralMoment (X : Ω → ℝ) (n : ℕ) (μ : Measure Ω) : ℝ
ProbabilityTheory.mgf (X : Ω → ℝ) (μ : Measure Ω) (t : ℝ) : ℝ   -- moment generating function
ProbabilityTheory.cgf (X : Ω → ℝ) (μ : Measure Ω) (t : ℝ) : ℝ   -- cumulant generating function
```

## Convergence Modes

### Almost Sure Convergence
```
-- X_n → X a.s. is:
∀ᵐ ω ∂ℙ, Filter.Tendsto (fun n => X n ω) atTop (𝓝 (X_limit ω))
```

### Convergence in Probability
```
-- X_n →ᵖ X is:
∀ ε > 0, Filter.Tendsto (fun n => ℙ {ω | ε ≤ ‖X n ω - X_limit ω‖}) atTop (𝓝 0)
```

### Convergence in Distribution
```
-- X_n →ᵈ X is expressed via weak convergence of measures:
MeasureTheory.Measure.tendsto_iff_forall_integral_tendsto
-- or via characteristic functions (Lévy continuity)
```

**Translation trap**: Mathlib does NOT have a single predicate for convergence in
distribution. You must use the filter/measure formulation above.

## Gaussian Distribution
```
gaussianReal (μ σ : ℝ) : Measure ℝ   -- N(μ, σ²) distribution
  -- requires: σ ≥ 0
```

## Key Theorems (Mathlib)
```
-- These may or may not exist in current Mathlib:
ProbabilityTheory.strong_law_ae    -- Strong Law of Large Numbers
-- CLT, Delta Method, etc. are generally NOT in Mathlib yet
```

## Recommended Imports
```lean
import Mathlib.Probability.Variance
import Mathlib.Probability.Moments
import Mathlib.Probability.Independence.Basic
import Mathlib.Probability.Distributions.Gaussian
import Mathlib.MeasureTheory.Measure.MeasureSpace
```
