"""PDF extractor — extract theorems and proofs from PDF files.

Supports three extraction backends:
    pymupdf    — fast, local, no API cost (default)
    ai-enhance — pymupdf extraction + AI LaTeX restoration (moderate cost)
    vision     — render pages as images, send to multimodal AI (highest accuracy, highest cost)

The extracted text is returned as TheoremBlock objects (reusing latex_parser.py)
or as raw text suitable for the Translator Agent.

Usage:
    from core.pdf_extractor import extract_from_pdf

    # Full PDF (default pymupdf backend, zero cost)
    result = extract_from_pdf("paper.pdf")

    # Specific theorem
    result = extract_from_pdf("paper.pdf", theorem="4.1")

    # Specific pages
    result = extract_from_pdf("paper.pdf", pages="3-5,8")

    # With AI enhancement
    result = extract_from_pdf("paper.pdf", backend="ai-enhance")

    # Vision mode for scanned/image PDFs
    result = extract_from_pdf("paper.pdf", backend="vision")
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.latex_parser import TheoremBlock, LaTeXParseResult


# ── Theorem-like block detection in markdown ──

THEOREM_KEYWORDS = [
    "theorem", "lemma", "corollary", "proposition",
    "definition", "remark", "example", "conjecture",
]
PROOF_KEYWORDS = ["proof", "proof sketch", "proof outline"]

THEOREM_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*|#{1,4}\s*)"
    r"(" + "|".join(THEOREM_KEYWORDS) + r")"
    r"\s*(\d+(?:\.\d+)*)?\s*"
    r"(?:\(([^)]*)\))?\s*\.?\s*\*?\*?",
    re.IGNORECASE,
)

PROOF_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*|#{1,4}\s*)"
    r"(" + "|".join(PROOF_KEYWORDS) + r")"
    r"\s*(?:of\s+(?:theorem|lemma|corollary|proposition)\s*(\d+(?:\.\d+)*))?\.?\s*\*?\*?",
    re.IGNORECASE,
)


# ── Data structures ──


@dataclass
class PDFExtractionResult:
    """Result of PDF extraction."""
    blocks: list[TheoremBlock] = field(default_factory=list)
    raw_text: str = ""
    source_file: str = ""
    backend: str = ""
    pages_processed: list[int] = field(default_factory=list)
    total_pages: int = 0
    extraction_warnings: list[str] = field(default_factory=list)

    @property
    def has_blocks(self) -> bool:
        return len(self.blocks) > 0

    def get_proof_text(
        self,
        label: str | None = None,
        theorem_name: str | None = None,
        index: int = 0,
    ) -> str | None:
        """Get formatted proof text for the Translator Agent.

        If label or theorem_name is provided, search for a matching block.
        Otherwise, return the block at the given index.
        Falls back to raw_text if no blocks were extracted.
        """
        if not self.blocks:
            return self.raw_text if self.raw_text else None

        for block in self.blocks:
            if label and block.label == label:
                return _format_block(block)
            if theorem_name and theorem_name.lower() in block.name.lower():
                return _format_block(block)

        # Fall back to index
        with_proof = [b for b in self.blocks if b.proof]
        if not with_proof:
            with_proof = [b for b in self.blocks if b.env_type != "proof"]
        if not with_proof:
            with_proof = self.blocks

        if 0 <= index < len(with_proof):
            return _format_block(with_proof[index])

        # Last resort: return raw text
        return self.raw_text if self.raw_text else None


def _format_block(block: TheoremBlock) -> str:
    """Format a theorem block as natural text for the Translator Agent."""
    parts = []
    env_name = block.env_type.capitalize()
    if block.name:
        parts.append(f"{env_name} ({block.name}):")
    else:
        parts.append(f"{env_name}:")
    parts.append(block.statement.strip())

    if block.proof:
        parts.append("\nProof:")
        parts.append(block.proof.strip())

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Page scanning: find relevant pages (fast, local)
# ═══════════════════════════════════════════════════════════


def _scan_pages_for_keyword(pdf_path: Path, keyword: str) -> list[int]:
    """Scan PDF text to find pages containing the keyword.
    Returns 0-indexed page numbers."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    matching_pages = []
    keyword_lower = keyword.lower()
    for i in range(len(doc)):
        text = doc[i].get_text().lower()
        if keyword_lower in text:
            matching_pages.append(i)
    doc.close()
    return matching_pages


def _scan_pages_for_theorem(pdf_path: Path, theorem_id: str) -> list[int]:
    """Find pages containing a specific theorem (e.g., '4.1', 'Theorem 4.1').
    Returns the theorem page + next page (for proofs that span pages)."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    matching = []
    all_kws = THEOREM_KEYWORDS + ["example", "remark", "conjecture"]
    keyword_alt = "|".join(all_kws + ["thm", "lem", "cor", "prop", "def", "ex", "rem"])
    patterns = [
        re.compile(rf"(?:{keyword_alt})\.?\s*{re.escape(theorem_id)}", re.IGNORECASE),
        re.compile(rf"\b{re.escape(theorem_id)}\b"),
    ]
    for i in range(len(doc)):
        text = doc[i].get_text()
        if any(p.search(text) for p in patterns):
            matching.append(i)
            if i + 1 < len(doc):
                matching.append(i + 1)
    doc.close()
    return sorted(set(matching))


def _parse_page_range(page_spec: str, total_pages: int) -> list[int]:
    """Parse page range like '1-5,8,10-12' into 0-indexed page list."""
    pages = set()
    for part in page_spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = max(1, int(start))
            end = min(total_pages, int(end))
            pages.update(range(start - 1, end))
        else:
            p = int(part)
            if 1 <= p <= total_pages:
                pages.add(p - 1)
    return sorted(pages)


# ═══════════════════════════════════════════════════════════
# Backend: pymupdf (fast, local, no model)
# ═══════════════════════════════════════════════════════════


def _run_pymupdf(pdf_path: Path, pages: list[int] | None = None) -> str:
    """Extract PDF to markdown using pymupdf4llm."""
    try:
        import pymupdf4llm
    except ImportError:
        raise SystemExit(
            "[pdf-extract] pymupdf4llm not found. Install: pip install pymupdf4llm"
        )

    if pages is not None:
        md_text = pymupdf4llm.to_markdown(str(pdf_path), pages=pages)
    else:
        md_text = pymupdf4llm.to_markdown(str(pdf_path))

    return md_text


# ═══════════════════════════════════════════════════════════
# Backend: ai-enhance (pymupdf + AI LaTeX restoration)
# ═══════════════════════════════════════════════════════════


def _run_ai_enhance(
    pdf_path: Path,
    pages: list[int] | None = None,
    theorem_id: str | None = None,
    query: str | None = None,
) -> str:
    """Extract PDF text via pymupdf, then use AI to restore LaTeX formatting."""
    from core.ai_client import AIClient

    # Step 1: Extract raw text
    raw_text = _run_pymupdf(pdf_path, pages)

    # Step 2: Build focus instruction
    focus = _build_focus_instruction(theorem_id, query)

    prompt = f"""Below is text extracted from a mathematics/statistics PDF.
The math formulas have been converted to Unicode and lost their LaTeX formatting.

YOUR TASK: Restore the mathematical content to proper LaTeX and identify theorem/proof blocks.

{focus}

For each theorem-like block found, output in this EXACT format:

## [Type] [Number] [Optional Name]
[Full statement with LaTeX: $...$ for inline, $$...$$ for display math]

### Proof
[Proof content if present]

Rules:
- Use standard LaTeX: \\mathbb{{E}}, \\operatorname{{Var}}, \\mathcal{{N}}, etc.
- Preserve ALL mathematical details — every subscript, superscript, condition
- If a formula is unclear, add: %% OCR_UNCERTAIN: [what's unclear]

--- PDF TEXT ---
{raw_text}
--- END PDF TEXT ---"""

    client = _get_ai_client()
    resp = client.chat(prompt)
    return resp.content


# ═══════════════════════════════════════════════════════════
# Backend: vision (render to images, multimodal AI)
# ═══════════════════════════════════════════════════════════


def _pdf_to_page_images(
    pdf_path: Path, pages: list[int] | None = None
) -> list[tuple[int, bytes]]:
    """Convert PDF pages to PNG images for multimodal AI.
    Returns list of (page_num, png_bytes)."""
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    images = []
    page_indices = pages if pages is not None else range(len(doc))
    for page_num in page_indices:
        page = doc[page_num]
        mat = pymupdf.Matrix(2, 2)  # 2x resolution for better quality
        pix = page.get_pixmap(matrix=mat)
        images.append((page_num, pix.tobytes("png")))
    doc.close()
    return images


def _run_vision(
    pdf_path: Path,
    pages: list[int] | None = None,
    theorem_id: str | None = None,
    query: str | None = None,
) -> str:
    """Use multimodal AI to extract theorems from PDF page images.
    This is the most accurate method and works on scanned/image PDFs."""
    from core.ai_client import AIClient

    page_images = _pdf_to_page_images(pdf_path, pages)
    focus = _build_focus_instruction(theorem_id, query)

    instructions = f"""{focus}

For each theorem-like block found, output in this EXACT format:

## [Type] [Number] [Optional Name]
[Full statement with LaTeX: $...$ for inline, $$...$$ for display math]

### Proof
[Proof content if present]

Rules:
- Use standard LaTeX: \\mathbb{{E}}, \\operatorname{{Var}}, \\mathcal{{N}}, etc.
- Preserve ALL mathematical details — every subscript, superscript, condition
- Skip headers/footers, page numbers, author info
- If a formula is unclear, add: %% OCR_UNCERTAIN: [what's unclear]"""

    all_parts: list[str] = []
    batch_size = 10

    client = _get_ai_client()

    for batch_start in range(0, len(page_images), batch_size):
        batch = page_images[batch_start : batch_start + batch_size]
        page_nums = [p + 1 for p, _ in batch]
        page_range_str = (
            f"{page_nums[0]}-{page_nums[-1]}"
            if len(page_nums) > 1
            else str(page_nums[0])
        )
        print(f"  [pdf-extract] Sending pages {page_range_str} to AI...")

        # Build multimodal content
        content_parts = []
        for _, img_bytes in batch:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                        "detail": "high",
                    },
                }
            )
        content_parts.append(
            {
                "type": "text",
                "text": instructions + f"\n\nPages shown: {page_range_str}",
            }
        )

        resp = client.chat_multimodal(content_parts)
        all_parts.append(resp)

    return "\n\n".join(all_parts)


# ═══════════════════════════════════════════════════════════
# Common: theorem extraction from markdown
# ═══════════════════════════════════════════════════════════


def _extract_theorem_blocks_from_md(md_text: str) -> list[TheoremBlock]:
    """Parse markdown to extract theorem-like blocks with their proofs."""
    raw_blocks: list[dict] = []
    lines = md_text.split("\n")
    current_block: dict | None = None
    current_proof_for: str | None = None
    buffer: list[str] = []

    def flush():
        nonlocal current_block, buffer, current_proof_for
        if current_block is not None:
            content = "\n".join(buffer).strip()
            if current_proof_for is not None:
                # Attach proof to the nearest preceding theorem
                for b in reversed(raw_blocks):
                    if (
                        b.get("number") == current_proof_for
                        or current_proof_for is None
                    ):
                        b["proof_hint"] = content
                        break
                else:
                    if raw_blocks:
                        raw_blocks[-1]["proof_hint"] = content
            else:
                current_block["statement"] = content
                raw_blocks.append(current_block)
            current_block = None
            current_proof_for = None
            buffer = []

    for line in lines:
        thm_match = THEOREM_HEADING_RE.search(line)
        if thm_match:
            flush()
            kind = thm_match.group(1).lower()
            number = thm_match.group(2) or ""
            name = thm_match.group(3) or ""
            current_block = {
                "kind": kind,
                "number": number,
                "name": name,
                "statement": "",
                "proof_hint": "",
            }
            rest = line[thm_match.end() :].strip()
            if rest:
                buffer.append(rest)
            continue

        proof_match = PROOF_HEADING_RE.search(line)
        if proof_match:
            flush()
            current_block = {
                "kind": "proof",
                "number": "",
                "name": "",
                "statement": "",
                "proof_hint": "",
            }
            current_proof_for = proof_match.group(2) or None
            rest = line[proof_match.end() :].strip()
            if rest:
                buffer.append(rest)
            continue

        if current_block is not None:
            buffer.append(line)

    flush()

    # Convert to TheoremBlock objects
    theorem_blocks: list[TheoremBlock] = []
    for rb in raw_blocks:
        if rb["kind"] == "proof":
            continue
        block = TheoremBlock(
            env_type=rb["kind"],
            name=rb.get("name", ""),
            statement=rb.get("statement", ""),
            proof=rb.get("proof_hint", ""),
            raw_statement=rb.get("statement", ""),
            raw_proof=rb.get("proof_hint", ""),
        )
        if rb.get("number"):
            block.label = f"{rb['kind']}_{rb['number']}"
        theorem_blocks.append(block)

    return theorem_blocks


def _get_ai_client():
    """Create an AIClient using the same provider/model as the audit pipeline.
    Reads PA_UI_PROVIDER and PA_UI_MODEL env vars (set by the web UI)."""
    from core.ai_client import AIClient

    provider = os.environ.get("PA_UI_PROVIDER", "openai")
    model_name = os.environ.get("PA_UI_MODEL", "")
    kwargs: dict = {"provider": provider}
    if model_name:
        kwargs["model"] = model_name
    return AIClient(**kwargs)


def _build_focus_instruction(
    theorem_id: str | None = None, query: str | None = None
) -> str:
    """Build focus instruction for AI extraction."""
    if theorem_id:
        return (
            f"Focus on Theorem/Lemma/Definition {theorem_id}. "
            "Extract its FULL statement and proof."
        )
    elif query:
        return (
            f"Focus on content related to: {query}. "
            "Extract all relevant theorems, definitions, and proofs."
        )
    else:
        return (
            "Extract ALL theorems, lemmas, definitions, propositions, "
            "corollaries, and their proofs."
        )


# ═══════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════


def extract_from_pdf(
    pdf_path: str,
    *,
    backend: str = "pymupdf",
    theorem: str | None = None,
    pages: str | None = None,
    query: str | None = None,
) -> PDFExtractionResult:
    """Extract theorems and proofs from a PDF file.

    Args:
        pdf_path: Path to the PDF file.
        backend: Extraction backend — "pymupdf" (default, zero cost),
                 "ai-enhance" (pymupdf + AI LaTeX fix), or
                 "vision" (multimodal AI, best for scanned PDFs).
        theorem: Extract a specific theorem by ID (e.g., "4.1").
        pages: Page range to extract (e.g., "1-5,8").
        query: Search keyword to find relevant pages.

    Returns:
        PDFExtractionResult with extracted blocks and raw text.
    """
    path = Path(pdf_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file, got: {path.suffix}")

    import pymupdf

    doc = pymupdf.open(str(path))
    total_pages = len(doc)
    doc.close()

    print(f"  [pdf-extract] PDF: {path.name} ({total_pages} pages)")
    print(f"  [pdf-extract] Backend: {backend}")

    # Determine target pages
    target_pages: list[int] | None = None

    if theorem:
        target_pages = _scan_pages_for_theorem(path, theorem)
        if target_pages:
            print(
                f"  [pdf-extract] Theorem '{theorem}' found on pages: "
                f"{[p + 1 for p in target_pages]}"
            )
        else:
            print(
                f"  [pdf-extract] Theorem '{theorem}' not found in text scan. "
                "Extracting all pages."
            )

    elif query:
        target_pages = _scan_pages_for_keyword(path, query)
        if target_pages:
            print(
                f"  [pdf-extract] Query '{query}' matches pages: "
                f"{[p + 1 for p in target_pages]}"
            )
        else:
            print(
                f"  [pdf-extract] Query '{query}' not found. "
                "Extracting all pages."
            )
            target_pages = None

    elif pages:
        target_pages = _parse_page_range(pages, total_pages)
        print(
            f"  [pdf-extract] Using specified pages: "
            f"{[p + 1 for p in target_pages]}"
        )

    # Run extraction backend
    warnings: list[str] = []

    if backend == "pymupdf":
        md_text = _run_pymupdf(path, target_pages)
    elif backend == "ai-enhance":
        md_text = _run_ai_enhance(path, target_pages, theorem, query)
    elif backend == "vision":
        md_text = _run_vision(path, target_pages, theorem, query)
    else:
        raise ValueError(f"Unknown backend: {backend}. Use: pymupdf, ai-enhance, vision")

    # Extract theorem blocks
    blocks = _extract_theorem_blocks_from_md(md_text)
    pages_list = target_pages if target_pages else list(range(total_pages))

    if blocks:
        print(f"  [pdf-extract] Extracted {len(blocks)} theorem-like blocks")
    else:
        print(f"  [pdf-extract] No structured blocks found; raw text available ({len(md_text)} chars)")
        warnings.append("No structured theorem blocks detected. Raw text will be used.")

    return PDFExtractionResult(
        blocks=blocks,
        raw_text=md_text,
        source_file=str(path),
        backend=backend,
        pages_processed=pages_list,
        total_pages=total_pages,
        extraction_warnings=warnings,
    )


def list_pdf_theorems(pdf_path: str) -> None:
    """Print a list of all detected theorems in a PDF (quick scan)."""
    result = extract_from_pdf(pdf_path)
    if result.blocks:
        print(f"\n  Available theorems ({len(result.blocks)}):")
        for i, b in enumerate(result.blocks):
            name = f" ({b.name})" if b.name else ""
            label = f" [{b.label}]" if b.label else ""
            has_proof = " ✅ has proof" if b.proof else " ⚠️ no proof"
            print(f"    {i + 1}. {b.env_type}{name}{label}{has_proof}")
    else:
        print(f"\n  No structured theorems detected in {pdf_path}.")
        print(f"  The full text ({len(result.raw_text)} chars) can still be audited.")
