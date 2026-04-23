# Mathlib Reference: Linear Algebra

## Vectors and Matrices

### Finite-Dimensional Vectors
```
EuclideanSpace ℝ (Fin n)         -- ℝⁿ
Matrix (Fin m) (Fin n) ℝ         -- m × n matrix
!![a, b; c, d]                   -- matrix literal notation
```

### Linear Maps
```
LinearMap (R : Type) (M : Type) (N : Type) : Type  -- notation: M →ₗ[R] N
ContinuousLinearMap (R : Type) (M : Type) (N : Type) : Type  -- notation: M →L[R] N
LinearMap.ker (f : M →ₗ[R] N) : Submodule R M
LinearMap.range (f : M →ₗ[R] N) : Submodule R N
```

### Matrix Operations
```
Matrix.mul (A : Matrix m n R) (B : Matrix n p R) : Matrix m p R
Matrix.transpose (A : Matrix m n R) : Matrix n m R  -- Aᵀ
Matrix.det (A : Matrix n n R) : R
Matrix.inv (A : Matrix n n R) : Matrix n n R
Matrix.trace (A : Matrix n n R) : R
```

### Inner Products and Norms
```
inner (x y : α) : 𝕜              -- ⟪x, y⟫
‖x‖                              -- norm
InnerProductSpace 𝕜 E : Prop
```

### Eigenvalues
```
Matrix.IsHermitian (A : Matrix n n ℂ) : Prop
Matrix.eigenvalues (A : Matrix n n ℝ) : Fin n → ℝ  -- for symmetric
```

## Recommended Imports
```lean
import Mathlib.LinearAlgebra.Matrix.NonsingularInverse
import Mathlib.LinearAlgebra.Matrix.Determinant.Basic
import Mathlib.Analysis.InnerProductSpace.Basic
```
