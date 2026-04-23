"""Mathlib domain reference system for the Translator Agent.

Detects mathematical domains from proof text keywords and assembles
relevant Lean 4 / Mathlib API references + translation-pattern warnings
to inject into the translator prompt.

Usage:
    from core.mathlib_reference import build_reference_context

    context = build_reference_context(proof_text)
    # → string with domain references + relevant pattern warnings
"""

import re
from pathlib import Path
from typing import Optional

import yaml

DATA_DIR = Path(__file__).parent.parent / "data"
DOMAINS_DIR = DATA_DIR / "mathlib_domains"
PATTERNS_FILE = DATA_DIR / "lean_translation_patterns.yaml"

# ── Domain detection ──────────────────────────────────────

# Each domain maps to a list of trigger keywords / regex patterns.
# The detector scans the proof text (case-insensitive) and returns
# all domains whose triggers appear.

DOMAIN_TRIGGERS: dict[str, list[str]] = {
    "elementary_number_theory": [
        r"\bodd\b", r"\beven\b", r"\bparity\b",
        r"\bdivisi\w*\b", r"\bprime\b", r"\bgcd\b",
        r"\bmod\b", r"\bremainder\b", r"\binteger\b",
        r"\bcongruent\b", r"\bcoprime\b",
    ],
    "real_analysis": [
        r"\blimit\b", r"\bconverg\w*\b", r"\bcontinuous\b",
        r"\bderivat\w*\b", r"\bdifferentiab\w*\b",
        r"\bintegra\w*\b", r"\bseries\b", r"\bsequence\b",
        r"\bsupremum\b", r"\binfimum\b", r"\bcompact\b",
        r"\buniform\w*\b", r"\bcauchy\b",
        r"\btaylor\b", r"\bmean.value\b",
    ],
    "measure_theory": [
        r"\bmeasur\w*\b", r"\bσ-algebra\b", r"\bsigma.algebra\b",
        r"\blebesgue\b", r"\bborel\b", r"\ba\.e\.\b",
        r"\balmost.every\w*\b", r"\balmost.sure\w*\b",
        r"\bfubini\b", r"\btonelli\b",
        r"\bdominated.convergence\b",
        r"\bσ-finit\w*\b", r"\bsigma.finit\w*\b",
    ],
    "probability": [
        r"\brandom.variable\b", r"\bdistribut\w*\b",
        r"\bexpectat\w*\b", r"\bvariance\b", r"\bcovariance\b",
        r"\bindependen\w*\b", r"\bi\.i\.d\.\b", r"\biid\b",
        r"\bprobability\b", r"\bgaussian\b", r"\bnormal\b",
        r"\bCLT\b", r"\bcentral.limit\b", r"\blaw.of.large\b",
        r"\bdelta.method\b", r"\bslutsky\b",
        r"\bcharacteristic.function\b",
        r"\bmoment\w*\b", r"\bMGF\b",
        r"\bconvergence.in.distribution\b",
        r"\bweak.convergence\b",
    ],
    "linear_algebra": [
        r"\bmatrix\b", r"\bmatrices\b", r"\bvector\b",
        r"\blinear.map\b", r"\blinear.transform\w*\b",
        r"\bdeterminant\b", r"\beigenvalue\b", r"\beigenvector\b",
        r"\brank\b", r"\bkernel\b", r"\bimage\b",
        r"\binner.product\b", r"\bnorm\b",
        r"\borthogon\w*\b", r"\btranspose\b",
        r"\bpositive.definite\b", r"\bsymmetric\b",
    ],
}


def detect_domains(proof_text: str) -> list[str]:
    """Detect relevant mathematical domains from proof text.

    Scans the proof text for domain trigger keywords and returns
    a list of matching domain names, ordered by relevance (number
    of keyword matches).

    Args:
        proof_text: The original natural language proof.

    Returns:
        List of domain names (e.g. ["elementary_number_theory", "real_analysis"]).
    """
    text_lower = proof_text.lower()
    scores: dict[str, int] = {}

    for domain, triggers in DOMAIN_TRIGGERS.items():
        count = 0
        for pattern in triggers:
            count += len(re.findall(pattern, text_lower))
        if count > 0:
            scores[domain] = count

    # Sort by match count descending
    return sorted(scores.keys(), key=lambda d: scores[d], reverse=True)


# ── Domain reference loading ──────────────────────────────


def load_domain_reference(domain: str) -> Optional[str]:
    """Load the Mathlib reference file for a domain.

    Args:
        domain: Domain name (e.g. "probability").

    Returns:
        Contents of the reference file, or None if not found.
    """
    path = DOMAINS_DIR / f"{domain}.md"
    if path.exists():
        return path.read_text()
    return None


# ── Translation pattern matching ──────────────────────────


def _load_patterns() -> list[dict]:
    """Load translation patterns from YAML."""
    if not PATTERNS_FILE.exists():
        return []
    try:
        data = yaml.safe_load(PATTERNS_FILE.read_text())
        return data.get("patterns", [])
    except Exception:
        return []


def match_patterns(proof_text: str) -> list[dict]:
    """Find translation patterns relevant to the proof text.

    Scans pattern triggers against the proof text and returns
    matching entries sorted by severity (high → medium → low).

    Args:
        proof_text: The original proof text.

    Returns:
        List of matching pattern dicts with trigger, guidance, severity.
    """
    text_lower = proof_text.lower()
    all_patterns = _load_patterns()
    matches = []

    severity_order = {"high": 0, "medium": 1, "low": 2}

    for pat in all_patterns:
        triggers = pat.get("trigger", [])
        if any(t.lower() in text_lower for t in triggers):
            matches.append(pat)

    matches.sort(key=lambda p: severity_order.get(p.get("severity", "low"), 3))
    return matches


# ── Main API ──────────────────────────────────────────────


def build_reference_context(proof_text: str, max_domains: int = 3) -> str:
    """Build a combined Mathlib reference + pattern warnings context.

    This is the main entry point. It detects domains from the proof text,
    loads corresponding reference files, and appends relevant translation
    patterns as warnings.

    Args:
        proof_text: The original natural language proof.
        max_domains: Maximum number of domain references to include.

    Returns:
        Formatted string ready to inject into the translator prompt.
        Empty string if no relevant domains are detected.
    """
    domains = detect_domains(proof_text)
    if not domains:
        return ""

    sections: list[str] = []

    # ── Domain references ──
    sections.append("## Lean 4 / Mathlib Quick Reference")
    sections.append("")
    sections.append(
        "The following Mathlib API references are relevant to this proof. "
        "Use these EXACT definition names — do NOT invent Lean API names."
    )
    sections.append("")

    for domain in domains[:max_domains]:
        ref = load_domain_reference(domain)
        if ref:
            sections.append(f"### Domain: {domain.replace('_', ' ').title()}")
            sections.append("")
            sections.append(ref.strip())
            sections.append("")

    # ── Translation patterns ──
    patterns = match_patterns(proof_text)
    if patterns:
        sections.append("## ⚠️ Translation Warnings (from known patterns)")
        sections.append("")
        for pat in patterns:
            severity = pat.get("severity", "medium").upper()
            guidance = pat.get("guidance", "").strip()
            category = pat.get("category", "").upper()
            triggers_str = ", ".join(pat.get("trigger", []))
            sections.append(f"**[{severity}] [{category}]** (triggered by: {triggers_str})")
            sections.append(guidance)
            sections.append("")

    return "\n".join(sections)
