# Mathlib Reference: Real Analysis

## Limits and Convergence

### Filter-Based Limits
```
Filter.Tendsto (f : α → β) (l₁ : Filter α) (l₂ : Filter β) : Prop
  -- "f(x) → L as x → a" is:  Tendsto f (𝓝 a) (𝓝 L)
  -- "f(n) → L as n → ∞" is:  Tendsto f atTop (𝓝 L)

Filter.atTop : Filter ℕ        -- the "n → ∞" filter
nhds (a : α) : Filter α        -- the neighborhood filter at a
```

**Translation trap**: Mathlib uses filters, NOT ε-δ directly. When the proof says
"as n → ∞, Xₙ → L", translate to `Tendsto Xₙ atTop (𝓝 L)`.

### Sequences
```
CauchySeq (f : ℕ → α) : Prop
Filter.Tendsto f atTop (𝓝 L)   -- sequence convergence
```

## Continuity and Differentiability

### Continuity
```
Continuous (f : α → β) : Prop
ContinuousOn (f : α → β) (s : Set α) : Prop
ContinuousAt (f : α → β) (a : α) : Prop
```

### Differentiability
```
HasDerivAt (f : ℝ → ℝ) (f' : ℝ) (x : ℝ) : Prop
Differentiable (𝕜 : Type) (f : α → β) : Prop
DifferentiableAt (𝕜 : Type) (f : α → β) (x : α) : Prop
deriv (f : ℝ → ℝ) (x : ℝ) : ℝ
fderiv (𝕜 : Type) (f : α → β) (x : α) : α →L[𝕜] β  -- Fréchet derivative
```

**Translation trap**: `deriv` is the scalar derivative (ℝ → ℝ). For multivariate
functions, use `fderiv`. `HasDerivAt f f' x` means `f'(x) = f'` (not `f'` as a function).

### Integration
```
∫ x, f x ∂μ                    -- MeasureTheory.integral
∫ x in s, f x ∂μ               -- MeasureTheory.set_integral
MeasureTheory.Integrable (f : α → β) (μ : Measure α) : Prop
```

## Series and Sums
```
tsum (f : ℕ → ℝ) : ℝ           -- ∑ n, f n (may be junk if not summable)
Summable (f : ℕ → ℝ) : Prop
HasSum (f : ℕ → ℝ) (a : ℝ) : Prop
Finset.sum (s : Finset α) (f : α → β) : β  -- finite sums
```

## Common Tactics
```
continuity   -- prove continuity goals
fun_prop     -- prove function property goals (continuous, measurable, etc.)
norm_num     -- numeric normalization
linarith     -- linear arithmetic
nlinarith    -- nonlinear arithmetic
positivity   -- positivity goals
field_simp   -- clear denominators
```

## Recommended Imports
```lean
import Mathlib.Analysis.SpecificLimits.Basic
import Mathlib.Analysis.Calculus.Deriv.Basic
import Mathlib.Analysis.Calculus.FDeriv.Basic
import Mathlib.MeasureTheory.Integral.Bochner
import Mathlib.Topology.Algebra.InfiniteSum.Basic
```
