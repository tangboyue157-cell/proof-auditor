# Mathlib Reference: Elementary Number Theory

## Core Definitions

### Parity
```
Odd (n : ℤ)       ↔ ∃ k, n = 2 * k + 1
Even (n : ℤ)      ↔ ∃ k, n = 2 * k
Int.even_or_odd (n : ℤ) : Even n ∨ Odd n
Int.odd_add       : Odd a → Odd b → Even (a + b)
Int.even_add      : Even a → Even b → Even (a + b)
Int.even_mul_add_one (k : ℤ) : Even (2 * k) → Odd (2 * k + 1)
```

**Translation trap**: When an informal proof says "there exists an integer k such that a = 2k+1",
use the `Odd` predicate if available, OR the explicit existential — match the proof's intent.
If the proof uses ONE variable k for two different odd numbers, you MUST preserve that.

### Divisibility
```
Dvd.dvd (a b : ℤ) : Prop      -- notation: a ∣ b
Int.dvd_iff_emod_eq_zero       : a ∣ b ↔ b % a = 0
Nat.Prime (p : ℕ)              : Prop
Int.gcd (a b : ℤ)              : ℕ
```

### Modular Arithmetic
```
Int.emod (a b : ℤ) : ℤ         -- notation: a % b
ZMod (n : ℕ) : Type            -- ℤ/nℤ
ZMod.val (a : ZMod n) : ℕ
```

## Common Tactics
```
omega        -- linear arithmetic over ℤ and ℕ
norm_num     -- numeric normalization
decide       -- decidable propositions
ring         -- ring equations
```

## Recommended Imports
```lean
import Mathlib.Data.Int.Parity
import Mathlib.Data.Int.GCD
import Mathlib.Data.ZMod.Basic
import Mathlib.Tactic
```
