"""Reference extractor — extract theorems from reference PDFs for agent context.

Uses AI-enhanced extraction (ai-enhance backend) to identify specific
theorems, lemmas, propositions, and definitions from cited papers/textbooks.
The extracted content is formatted as structured markdown and injected into
the Diagnostician and Verifier agent prompts.

Usage:
    from core.reference_extractor import extract_reference_context

    context = extract_reference_context(["ref1.pdf", "ref2.pdf"])
    # Returns a formatted markdown string ready for prompt injection
"""

from __future__ import annotations

import os
from pathlib import Path

# Maximum total characters for reference context (≈7,500 tokens)
MAX_REFERENCE_CHARS = 30_000


def extract_reference_context(
    pdf_paths: list[str],
    *,
    max_chars: int = MAX_REFERENCE_CHARS,
) -> str:
    """Extract theorems from reference PDFs and format as agent context.

    Uses the ai-enhance backend (pymupdf + AI restoration) to accurately
    identify theorem statements, conditions, and proof sketches from each
    reference PDF.

    Args:
        pdf_paths: List of paths to reference PDF files.
        max_chars: Maximum total characters for the combined output.

    Returns:
        Formatted markdown string for prompt injection. Empty string if
        no references provided or extraction fails.
    """
    if not pdf_paths:
        return ""

    sections: list[str] = []
    total_chars = 0

    for i, pdf_path in enumerate(pdf_paths, 1):
        path = Path(pdf_path)
        if not path.exists():
            print(f"  [refs] ⚠️  Reference not found: {pdf_path}")
            continue
        if path.suffix.lower() != ".pdf":
            print(f"  [refs] ⚠️  Not a PDF: {pdf_path}")
            continue

        print(f"  [refs] Extracting theorems from reference {i}: {path.name}")

        try:
            section_text = _extract_single_reference(path)
        except Exception as e:
            print(f"  [refs] ⚠️  Failed to extract {path.name}: {e}")
            # Fall back to pymupdf raw text
            section_text = _extract_fallback(path)

        if not section_text:
            continue

        # Check budget
        remaining = max_chars - total_chars
        if remaining <= 0:
            print(f"  [refs] ⚠️  Character budget exhausted, skipping remaining references")
            break

        if len(section_text) > remaining:
            section_text = _smart_truncate(section_text, remaining)
            print(f"  [refs]   Truncated to {len(section_text)} chars (budget limit)")

        header = f"### Reference {i}: {path.name}\n\n"
        section = header + section_text
        sections.append(section)
        total_chars += len(section)
        print(f"  [refs]   Extracted {len(section_text)} chars")

    if not sections:
        return ""

    preamble = (
        "## Reference Materials\n\n"
        "The proof cites the following external sources. "
        "Use them to verify cited claims — check that cited theorems exist "
        "and that their conditions are satisfied.\n\n"
    )
    return preamble + "\n---\n\n".join(sections)


def _extract_single_reference(path: Path) -> str:
    """Extract theorems from a single reference PDF using AI enhancement."""
    from core.ai_client import AIClient
    from core.pdf_extractor import _run_pymupdf

    # Step 1: Extract raw text with pymupdf
    raw_text = _run_pymupdf(path)

    if not raw_text.strip():
        return ""

    # Step 2: Use AI to identify and structure theorems
    # Use the same provider/model as the audit pipeline
    provider = os.environ.get("PA_UI_PROVIDER", "openai")
    model_name = os.environ.get("PA_UI_MODEL", "")
    kwargs: dict = {"provider": provider}
    if model_name:
        kwargs["model"] = model_name

    client = AIClient(**kwargs)

    # Truncate raw text if too long for a single AI call (keep ~60K chars)
    if len(raw_text) > 60_000:
        raw_text = raw_text[:60_000] + "\n\n[... truncated ...]"

    prompt = f"""Below is text extracted from a mathematics/statistics reference paper or textbook.

YOUR TASK: Extract ALL theorem-like statements (theorems, lemmas, propositions, 
corollaries, definitions, assumptions) with their EXACT conditions and conclusions.

For each result found, output in this format:

**[Type] [Number] ([Optional Name])**
[Full statement with proper LaTeX: $...$ for inline, $$...$$ for display math]

*Conditions*: [List all required conditions/assumptions]

---

Rules:
- Preserve ALL mathematical conditions — do not omit hypotheses
- Use standard LaTeX notation
- Include theorem numbers for cross-referencing
- Skip proofs (only keep proof sketches if very short)
- Skip examples, exercises, and remarks unless they state important results
- If a result is unclear, add: %% UNCERTAIN: [what's unclear]

--- REFERENCE TEXT ---
{raw_text}
--- END REFERENCE TEXT ---"""

    resp = client.chat(prompt, temperature=0.2)
    return resp.content


def _extract_fallback(path: Path) -> str:
    """Fallback: extract raw text with pymupdf (no AI)."""
    try:
        from core.pdf_extractor import _run_pymupdf
        raw = _run_pymupdf(path)
        if raw.strip():
            return f"*[Raw text extraction — AI enhancement unavailable]*\n\n{raw}"
    except Exception:
        pass
    return ""


def _smart_truncate(text: str, max_chars: int) -> str:
    """Truncate text at a natural boundary (end of a theorem block).

    Tries to cut at '---' separators or double newlines to avoid
    cutting in the middle of a theorem statement.
    """
    if len(text) <= max_chars:
        return text

    # Try to find last '---' before max_chars
    truncated = text[:max_chars]
    last_sep = truncated.rfind("\n---\n")
    if last_sep > max_chars * 0.5:  # Only if we keep at least 50%
        return truncated[:last_sep] + "\n\n*[... remaining theorems truncated ...]*"

    # Try double newline
    last_para = truncated.rfind("\n\n")
    if last_para > max_chars * 0.5:
        return truncated[:last_para] + "\n\n*[... remaining theorems truncated ...]*"

    # Hard cut
    return truncated + "\n\n*[... truncated ...]*"
