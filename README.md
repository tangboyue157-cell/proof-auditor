# Proof Auditor

**AI-powered mathematical proof verification via sorry diagnosis.**

Proof Auditor translates informal mathematical proofs into Lean 4, then classifies the resulting `sorry` gaps to determine whether the original proof is correct or contains logical errors — without requiring full formalization.

## How It Works

```
Mathematical Proof (LaTeX / PDF)
        │
        ▼  [Translator Agent]
Lean 4 Formalization (with sorry gaps)
        │
        ▼  [Lean LSP / Compiler]
Compilation Diagnostics + Sorry Locations
        │
        ▼  [Diagnostician Agent]
Sorry Classification (per gap)
        │
        ▼  [Verifier Agent]
Counterexample Search (for suspected errors)
        │
        ▼
Structured Audit Report
```

## Sorry Classification

Each `sorry` gap is classified into one of five types:

| Type | Meaning | Implication |
|------|---------|-------------|
| **A. Logical Gap** | Original proof has a logical error | ❌ Proof may be incorrect |
| **B. Translation Error** | AI misunderstood the math | ✅ Proof itself is fine |
| **C. Mathlib Gap** | Required lemma not in Mathlib | ✅ Proof itself is fine |
| **D. API Miss** | Lemma exists but AI couldn't find it | ✅ Proof itself is fine |
| **E. Formalization Difficulty** | Correct but hard to mechanize | ✅ Proof itself is fine |

The core value: **reliably distinguishing Type A from Types B–E.**

## Project Structure

```
proof-auditor/
├── core/               # Core logic
│   ├── translator.py   # Proof → Lean translation (AI-driven)
│   ├── compiler.py     # Compilation + diagnostics (Lean LSP)
│   ├── classifier.py   # Sorry classification engine
│   ├── reporter.py     # Audit report generation
│   └── loop.py         # Main orchestration loop
│
├── agents/             # Agent prompt templates
│   ├── translator.md   # Translator Agent prompt
│   ├── diagnostician.md # Diagnostician Agent prompt
│   └── verifier.md     # Verifier Agent (counterexample search)
│
├── tools/              # External tool integrations
│   └── lean_lsp.py     # Lean LSP MCP interface
│
├── schemas/            # Data format definitions
│   ├── audit_report.schema.json
│   └── sorry_classification.yaml
│
├── pipeline/           # Input processing
│   └── latex_extract.py
│
├── benchmark/          # Evaluation datasets
│   ├── known_correct/  # Proofs known to be correct
│   ├── known_buggy/    # Proofs with deliberate errors
│   └── evaluate.py     # Precision/recall/F1 evaluation
│
├── scripts/            # Utility scripts
├── tests/              # Unit tests
└── docs/               # Documentation
```

## Quick Start

```bash
# Clone
git clone https://github.com/mockingbird-gan/proof-auditor.git
cd proof-auditor

# Install dependencies
pip install -e .

# Run audit on a proof
python -m core.loop --input examples/proof.tex --output report.json
```

## Key Design Decisions

1. **Sorry diagnosis, not sorry elimination.** Unlike Archon or StepProof, we don't try to fill every sorry. Instead, we classify them.
2. **Domain-agnostic.** Works on any mathematical proof, not limited to a specific area. Statistical proofs serve as a case study.
3. **Multi-round feedback.** Translation → Compilation → Classification → Verification, with the AI re-examining each sorry in context.
4. **Counterexample search.** For suspected Type A (logical) errors, we actively try to construct counterexamples.

## Related Work

| System | Goal | Our Difference |
|--------|------|----------------|
| [StepProof](https://arxiv.org/abs/2506.10558) | Step-by-step autoformalization | We classify sorry, not eliminate |
| [Safe](https://arxiv.org/abs/2505.xxxxx) | Audit AI reasoning steps | We audit human proofs |
| [Archon](https://github.com/frenzymath/Archon) | Autonomous formalization | We diagnose, not formalize |
| [SorryDB](https://github.com/xxx/sorrydb) | Sorry benchmark (all correct) | We include deliberate errors |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache-2.0
