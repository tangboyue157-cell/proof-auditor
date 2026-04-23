# Mathlib Reference: Measure Theory

## Measures and Measurability

### Measurable Spaces
```
MeasurableSpace (α : Type)                    -- σ-algebra structure
MeasurableSet (s : Set α) : Prop              -- s is measurable
Measurable (f : α → β) : Prop                 -- f is measurable
AEMeasurable (f : α → β) (μ : Measure α) : Prop  -- a.e. measurable
StronglyMeasurable (f : α → β) : Prop         -- strongly measurable
AEStronglyMeasurable (f : α → β) (μ : Measure α) : Prop
```

**Translation trap**: `Measurable f` ≠ `AEMeasurable f μ`.
- `Measurable` is the strict version (preimage of measurable set is measurable)
- `AEMeasurable` is the a.e. version (agrees with a measurable function a.e.)
When informal proofs say "measurable", they usually mean `AEStronglyMeasurable` in Mathlib.

### Measures
```
MeasureTheory.Measure (α : Type) : Type
MeasureTheory.Measure.map (f : α → β) (μ : Measure α) : Measure β  -- pushforward
MeasureTheory.Measure.restrict (μ : Measure α) (s : Set α) : Measure α
MeasureTheory.Measure.dirac (a : α) : Measure α
MeasureTheory.MeasureSpace (α : Type) : Type  -- default measure
volume : Measure α                             -- the canonical measure
```

### Finite and Probability Measures
```
MeasureTheory.IsFiniteMeasure (μ : Measure α) : Prop
MeasureTheory.IsProbabilityMeasure (μ : Measure α) : Prop
  -- μ(Set.univ) = 1
```

## Integration

### Lebesgue Integral
```
∫ x, f x ∂μ                    -- Bochner integral
∫⁻ x, f x ∂μ                   -- Lebesgue integral (for ENNReal-valued)
MeasureTheory.Integrable (f : α → β) (μ : Measure α) : Prop
MeasureTheory.MemLp (f : α → β) (p : ENNReal) (μ : Measure α) : Prop  -- f ∈ Lᵖ
```

### Key Theorems
```
MeasureTheory.integral_add      -- ∫ (f + g) = ∫ f + ∫ g
MeasureTheory.integral_smul     -- ∫ c • f = c • ∫ f
MeasureTheory.lintegral_mono    -- monotone convergence (ENNReal)
MeasureTheory.tendsto_integral_of_dominated_convergence  -- DCT
MeasureTheory.integral_prod     -- Fubini
```

### Almost Everywhere
```
MeasureTheory.ae (μ : Measure α) : Filter α   -- the a.e. filter
∀ᵐ x ∂μ, P x                   -- P holds a.e.
=ᵐ[μ]                          -- equality a.e.
≤ᵐ[μ]                          -- inequality a.e.
```

## Conditional Expectation
```
MeasureTheory.condexp (m : MeasurableSpace α) (μ : Measure α) (f : α → β) : α → β
  -- E[f | m]
```

## Common Tactics
```
measurability    -- prove measurability goals
fun_prop         -- prove function property goals
exact?           -- search for exact match
```

## Recommended Imports
```lean
import Mathlib.MeasureTheory.Measure.MeasureSpace
import Mathlib.MeasureTheory.Integral.Bochner
import Mathlib.MeasureTheory.Integral.Lebesgue
import Mathlib.MeasureTheory.Function.ConditionalExpectation.Basic
```
