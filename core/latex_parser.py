"""LaTeX parser — extract theorem/proof environments from .tex files.

Supports:
  - \\begin{theorem}...\\end{theorem} (and variants)
  - \\begin{proof}...\\end{proof}
  - \\begin{lemma}, \\begin{proposition}, \\begin{corollary}
  - Named theorems via \\label{} or optional argument [name]
  - Macro context extraction for Translator

Usage:
    from core.latex_parser import parse_latex_file, extract_proof_block

    blocks = parse_latex_file("paper.tex")
    for b in blocks:
        print(f"{b.env_type}: {b.label} ({b.start_line}-{b.end_line})")
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


THEOREM_ENVS = {
    "theorem", "lemma", "proposition", "corollary",
    "claim", "conjecture", "fact", "assumption",
}

PROOF_ENV = "proof"


@dataclass
class TheoremBlock:
    """A theorem-like environment extracted from LaTeX."""
    env_type: str           # "theorem", "lemma", etc.
    label: str = ""         # \\label{...} value
    name: str = ""          # Optional [name] argument
    statement: str = ""     # The theorem statement text
    proof: str = ""         # The proof text (if found)
    start_line: int = 0
    end_line: int = 0
    # Raw LaTeX (for Translator context)
    raw_statement: str = ""
    raw_proof: str = ""


@dataclass
class LaTeXContext:
    """Global context extracted from a LaTeX file."""
    macros: list[str] = field(default_factory=list)         # \\newcommand definitions
    packages: list[str] = field(default_factory=list)       # \\usepackage
    theorem_defs: list[str] = field(default_factory=list)   # \\newtheorem definitions
    preamble: str = ""                                       # Full preamble text


@dataclass
class LaTeXParseResult:
    """Complete parse result for a LaTeX file."""
    blocks: list[TheoremBlock] = field(default_factory=list)
    context: LaTeXContext = field(default_factory=LaTeXContext)
    source_file: str = ""


def parse_latex_file(file_path: str) -> LaTeXParseResult:
    """Parse a LaTeX file and extract all theorem/proof blocks.

    Args:
        file_path: Path to .tex file.

    Returns:
        LaTeXParseResult with all theorem blocks and context.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"LaTeX file not found: {file_path}")

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    result = LaTeXParseResult(source_file=str(path))

    # Extract context (preamble)
    result.context = _extract_context(content)

    # Extract theorem/proof blocks
    result.blocks = _extract_blocks(content, lines)

    # Match proofs to theorems
    _match_proofs(result.blocks)

    return result


def extract_proof_block(
    file_path: str,
    label: Optional[str] = None,
    theorem_name: Optional[str] = None,
    index: int = 0,
) -> Optional[str]:
    """Extract a specific proof from a LaTeX file.

    Args:
        file_path: Path to .tex file.
        label: Label to search for (e.g., "thm:main").
        theorem_name: Name to search for (e.g., "Central Limit Theorem").
        index: If no label/name given, return the N-th theorem (0-indexed).

    Returns:
        Combined theorem statement + proof text, or None.
    """
    result = parse_latex_file(file_path)

    for block in result.blocks:
        if label and block.label == label:
            return _format_block(block)
        if theorem_name and theorem_name.lower() in block.name.lower():
            return _format_block(block)

    # Fall back to index
    # Filter to blocks that have both statement and proof
    with_proof = [b for b in result.blocks if b.proof]
    if not with_proof:
        with_proof = result.blocks

    if 0 <= index < len(with_proof):
        return _format_block(with_proof[index])

    return None


def _format_block(block: TheoremBlock) -> str:
    """Format a theorem block as natural text for the Translator."""
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


def _extract_context(content: str) -> LaTeXContext:
    """Extract preamble context: macros, packages, theorem definitions."""
    ctx = LaTeXContext()

    # Find preamble (before \begin{document})
    doc_start = content.find("\\begin{document}")
    preamble = content[:doc_start] if doc_start >= 0 else ""
    ctx.preamble = preamble

    # Extract \newcommand and \def
    for m in re.finditer(r'(\\(?:re)?newcommand\*?\{[^}]+\}(?:\[\d+\])?\{[^}]*\})', preamble):
        ctx.macros.append(m.group(1))
    for m in re.finditer(r'(\\def\\[a-zA-Z]+\{[^}]*\})', preamble):
        ctx.macros.append(m.group(1))
    for m in re.finditer(r'(\\DeclareMathOperator\*?\{[^}]+\}\{[^}]+\})', preamble):
        ctx.macros.append(m.group(1))

    # Extract packages
    for m in re.finditer(r'\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}', preamble):
        ctx.packages.extend(p.strip() for p in m.group(1).split(","))

    # Extract theorem definitions
    for m in re.finditer(r'(\\newtheorem\*?\{[^}]+\}\{[^}]+\}(?:\[[^\]]*\])?)', preamble):
        ctx.theorem_defs.append(m.group(1))

    return ctx


def _extract_blocks(content: str, lines: list[str]) -> list[TheoremBlock]:
    """Extract all theorem-like and proof environments."""
    blocks = []

    # Pattern for \begin{env}[optional name]...\end{env}
    all_envs = THEOREM_ENVS | {PROOF_ENV}
    env_pattern = "|".join(re.escape(e) for e in all_envs)

    for m in re.finditer(
        rf'\\begin\{{({env_pattern})\*?\}}'
        rf'(?:\[([^\]]*)\])?'
        rf'(.*?)'
        rf'\\end\{{\1\*?\}}',
        content, re.DOTALL
    ):
        env_type = m.group(1)
        name = m.group(2) or ""
        body = m.group(3).strip()

        # Calculate line numbers
        start_pos = m.start()
        end_pos = m.end()
        start_line = content[:start_pos].count("\n") + 1
        end_line = content[:end_pos].count("\n") + 1

        # Extract label
        label = ""
        label_match = re.search(r'\\label\{([^}]+)\}', body)
        if label_match:
            label = label_match.group(1)
            # Remove label from body
            body = body[:label_match.start()] + body[label_match.end():]

        # Minimal LaTeX cleanup (keep readable)
        clean_body = _clean_latex(body)

        block = TheoremBlock(
            env_type=env_type,
            label=label,
            name=name,
            statement=clean_body if env_type != PROOF_ENV else "",
            proof=clean_body if env_type == PROOF_ENV else "",
            start_line=start_line,
            end_line=end_line,
            raw_statement=body if env_type != PROOF_ENV else "",
            raw_proof=body if env_type == PROOF_ENV else "",
        )
        blocks.append(block)

    return blocks


def _match_proofs(blocks: list[TheoremBlock]) -> None:
    """Match proof blocks to their preceding theorem blocks."""
    for i, block in enumerate(blocks):
        if block.env_type == PROOF_ENV and block.proof:
            # Find the nearest preceding theorem-like block without a proof
            for j in range(i - 1, -1, -1):
                if blocks[j].env_type in THEOREM_ENVS and not blocks[j].proof:
                    blocks[j].proof = block.proof
                    blocks[j].raw_proof = block.raw_proof
                    break


def _clean_latex(text: str) -> str:
    """Minimal LaTeX cleanup for readability (preserve math semantics)."""
    # Remove \label{...}
    text = re.sub(r'\\label\{[^}]*\}', '', text)
    # Keep math as-is (don't strip $...$)
    # Clean up excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()
