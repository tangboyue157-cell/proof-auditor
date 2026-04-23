"""
Proof Structure Analyzer — Static Lean Code Parser.

Parses Lean 4 proof code WITHOUT compilation to extract:
  1. Proof skeleton: have/let/obtain/suffices nesting tree
  2. Hypothesis flow: which steps reference which hypotheses
  3. Static dependency DAG: derived from variable scoping
  4. Proof strategy detection: direct, induction, contradiction, etc.

This runs immediately after R1 (translation) and provides structural
context to ALL downstream rounds (R1.5, R2, R2.5, R3).

Usage:
    from core.proof_structure import analyze_proof_structure

    structure = analyze_proof_structure(lean_code)
    print(structure.summary())
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProofStep:
    """A single step in the proof tree."""
    name: str                    # Variable/hypothesis name (e.g. "step2_shared_k")
    step_type: str               # have | let | obtain | suffices | calc | show | sorry
    line: int                    # 1-indexed line number
    depth: int                   # Nesting depth (0 = top-level)
    end_line: int = 0            # Inclusive end line for this step block
    goal_snippet: str = ""       # Brief description of what's being proved
    has_sorry: bool = False      # Whether this step uses sorry
    sorry_id: str = ""           # Mapped SORRY_ID from comments
    claimed_reason: str = ""     # CLAIMED_REASON from comments
    references: list = field(default_factory=list)  # Hypothesis names referenced
    introduces: list = field(default_factory=list)  # Names introduced by this step
    parent: Optional[str] = None  # Enclosing step name


@dataclass
class StaticEdge:
    """A dependency edge derived from static analysis."""
    from_step: str               # Provider step
    to_step: str                 # Consumer step
    edge_type: str               # hypothesis_ref | variable_scope | structural | sequential
    evidence: str = ""           # Why this edge exists


@dataclass
class ProofStructure:
    """Complete static proof structure analysis."""
    theorem_name: str = ""
    theorem_statement: str = ""
    proof_strategy: str = "direct"   # direct | induction | contradiction | cases | calc
    steps: list = field(default_factory=list)     # list[ProofStep]
    edges: list = field(default_factory=list)     # list[StaticEdge]
    sorry_count: int = 0
    max_depth: int = 0

    # Derived analysis
    root_steps: list = field(default_factory=list)      # Steps with no upstream deps
    leaf_steps: list = field(default_factory=list)      # Steps with no downstream deps
    critical_chain: list = field(default_factory=list)  # Longest dependency chain
    hypothesis_names: set = field(default_factory=set)  # All theorem hypotheses

    def get_step(self, name: str) -> Optional[ProofStep]:
        """Find a step by name."""
        for s in self.steps:
            if s.name == name:
                return s
        return None

    def downstream_of(self, step_name: str) -> list:
        """Get all steps that depend on a given step (transitively)."""
        result = []
        visited = set()
        queue = [step_name]
        while queue:
            current = queue.pop(0)
            for e in self.edges:
                if e.from_step == current and e.to_step not in visited:
                    visited.add(e.to_step)
                    result.append(e.to_step)
                    queue.append(e.to_step)
        return result

    def upstream_of(self, step_name: str) -> list:
        """Get all steps that a given step depends on (transitively)."""
        result = []
        visited = set()
        queue = [step_name]
        while queue:
            current = queue.pop(0)
            for e in self.edges:
                if e.to_step == current and e.from_step not in visited:
                    visited.add(e.from_step)
                    result.append(e.from_step)
                    queue.append(e.from_step)
        return result

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            f"📐 Proof Structure: {self.theorem_name}",
            f"   Strategy: {self.proof_strategy}",
            f"   Steps: {len(self.steps)} ({self.sorry_count} with sorry)",
            f"   Max depth: {self.max_depth}",
            f"   Dependencies: {len(self.edges)} static edges",
        ]
        if self.root_steps:
            lines.append(f"   Root steps: {', '.join(self.root_steps)}")
        if self.leaf_steps:
            lines.append(f"   Leaf steps: {', '.join(self.leaf_steps)}")
        if self.critical_chain:
            lines.append(f"   Critical chain: {' → '.join(self.critical_chain)}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for JSON output / downstream consumption."""
        return {
            "theorem_name": self.theorem_name,
            "theorem_statement": self.theorem_statement,
            "proof_strategy": self.proof_strategy,
            "sorry_count": self.sorry_count,
            "max_depth": self.max_depth,
            "steps": [
                {
                    "name": s.name,
                    "type": s.step_type,
                    "line": s.line,
                    "end_line": s.end_line,
                    "depth": s.depth,
                    "has_sorry": s.has_sorry,
                    "sorry_id": s.sorry_id,
                    "claimed_reason": s.claimed_reason,
                    "references": s.references,
                    "introduces": s.introduces,
                    "parent": s.parent,
                }
                for s in self.steps
            ],
            "edges": [
                {
                    "from": e.from_step,
                    "to": e.to_step,
                    "type": e.edge_type,
                    "evidence": e.evidence,
                }
                for e in self.edges
            ],
            "root_steps": self.root_steps,
            "leaf_steps": self.leaf_steps,
            "critical_chain": self.critical_chain,
        }


# ══════════════════════════════════════════
#  Static Parser
# ══════════════════════════════════════════

_THEOREM_RE = re.compile(
    r'(?:theorem|lemma|def)\s+([\w.]+)\s*(?:\{[^}]*\})?\s*(?:\([^)]*\))?\s*(?:\[[^\]]*\])?\s*'
    r'(?:\([^)]*\))*\s*:\s*(.+?)(?::=|where)',
    re.DOTALL,
)

_SORRY_ID_RE = re.compile(r'--\s*SORRY_ID:\s*(\S+)')
_CLAIMED_REASON_RE = re.compile(r'--\s*CLAIMED_REASON:\s*(.+)')
_STEP_COMMENT_RE = re.compile(r'--\s*STEP\s+(\d+):\s*"?(.*?)"?\s*$')


def _detect_strategy(lean_code: str) -> str:
    """Detect the proof strategy from Lean keywords."""
    code = lean_code.lower()
    if re.search(r'\binduction\b', code) or re.search(r'\brec\b', code):
        return 'induction'
    if re.search(r'\bcontradiction\b', code) or re.search(r'\babsurd\b', code) or re.search(r'\bfalse\b', code):
        return 'contradiction'
    if re.search(r'\bcases\b', code) or re.search(r'\brcases\b', code) or re.search(r'\bmatch\b', code):
        return 'cases'
    if re.search(r'^\s*calc\b', code, re.MULTILINE):
        return 'calc_chain'
    return 'direct'


def _line_without_comment(line: str) -> str:
    """Drop trailing line comments for lightweight parsing."""
    return line.split('--', 1)[0]


def _find_references(code_block: str, known_names: set) -> list:
    """Find references to known hypothesis/variable names in a code block."""
    refs = []
    for name in known_names:
        pattern = rf'\b{re.escape(name)}\b'
        for m in re.finditer(pattern, code_block):
            line_start = code_block.rfind('\n', 0, m.start()) + 1
            line_end = code_block.find('\n', m.start())
            if line_end < 0:
                line_end = len(code_block)
            line = code_block[line_start:line_end]
            comment_pos = line.find('--')
            col = m.start() - line_start
            if comment_pos < 0 or col < comment_pos:
                if name not in refs:
                    refs.append(name)
                break
    return refs


def _compute_edges(steps: list, known_names: dict) -> list:
    """Compute static dependency edges from variable references."""
    edges = []
    seen = set()

    for step in steps:
        for ref in step.references:
            if ref in known_names:
                provider = known_names[ref]
                edge_key = (provider, step.name, "hypothesis_ref")
                if edge_key not in seen and provider != step.name:
                    seen.add(edge_key)
                    edges.append(StaticEdge(
                        from_step=provider,
                        to_step=step.name,
                        edge_type="hypothesis_ref",
                        evidence=f"{step.name} uses '{ref}' from {provider}",
                    ))

        if step.parent:
            edge_key = (step.parent, step.name, "structural")
            if edge_key not in seen:
                seen.add(edge_key)
                edges.append(StaticEdge(
                    from_step=step.parent,
                    to_step=step.name,
                    edge_type="structural",
                    evidence=f"{step.name} is nested inside {step.parent}",
                ))

    return edges


def _compute_critical_chain(steps: list, edges: list) -> list:
    """Find the longest dependency chain (critical path)."""
    step_names = {s.name for s in steps}
    children = {name: [] for name in step_names}
    parents = {name: [] for name in step_names}

    for e in edges:
        if e.from_step in step_names and e.to_step in step_names:
            children[e.from_step].append(e.to_step)
            parents[e.to_step].append(e.from_step)

    roots = [n for n in step_names if not parents[n]]

    longest = []
    for root in roots:
        queue = [(root, [root])]
        while queue:
            node, path = queue.pop(0)
            if not children[node]:
                if len(path) > len(longest):
                    longest = path
            for child in children[node]:
                if child not in path:
                    queue.append((child, path + [child]))

    return longest


def _compute_end_lines(steps: list[ProofStep], lines: list[str]) -> None:
    """Populate inclusive end_line for each step using indentation blocks.

    For `... := by` style steps, the block continues while subsequent nonblank,
    non-comment lines are strictly more indented than the step header.
    This prevents trailing sibling code from being absorbed into the nested step.
    """
    total_lines = len(lines)
    for step in steps:
        header = _line_without_comment(lines[step.line - 1]).rstrip()
        current_indent = len(lines[step.line - 1]) - len(lines[step.line - 1].lstrip())
        opens_block = bool(re.search(r':=\s*by\b', header) or header.strip().startswith('calc'))

        if not opens_block:
            step.end_line = step.line
            continue

        end_line = step.line
        for line_no in range(step.line + 1, total_lines + 1):
            raw = lines[line_no - 1]
            stripped = raw.strip()
            if not stripped or re.match(r'\s*--', raw):
                continue
            indent = len(raw) - len(raw.lstrip())
            if indent <= current_indent:
                break
            end_line = line_no
        step.end_line = end_line


def _descendant_ranges(step: ProofStep, steps: list[ProofStep]) -> list[tuple[int, int]]:
    """Return line ranges of descendant steps nested inside a step."""
    ranges = []
    for other in steps:
        if other is step:
            continue
        if other.line > step.line and other.end_line <= step.end_line and other.depth > step.depth:
            ranges.append((other.line, other.end_line))
    return ranges


def _line_in_ranges(line_no: int, ranges: list[tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start <= line_no <= end:
            return True
    return False


def _scanable_block_for_step(step: ProofStep, steps: list[ProofStep], lines: list[str]) -> str:
    """Build a code block for one step without descendant step blocks.

    This avoids attributing child-step references or later sibling references
    to the current step.
    """
    blocked = _descendant_ranges(step, steps)
    kept_lines = []
    for line_no in range(step.line, step.end_line + 1):
        if _line_in_ranges(line_no, blocked):
            continue
        kept_lines.append(lines[line_no - 1])
    return "\n".join(kept_lines)


def _has_sorry_in_block(step: ProofStep, steps: list[ProofStep], lines: list[str]) -> bool:
    """Check whether a step's *own* block contains sorry outside child steps."""
    blocked = _descendant_ranges(step, steps)
    for line_no in range(step.line, step.end_line + 1):
        if _line_in_ranges(line_no, blocked):
            continue
        line = _line_without_comment(lines[line_no - 1])
        if re.search(r'\bsorry\b', line):
            return True
    return False


# ══════════════════════════════════════════
#  Main Entry Point
# ══════════════════════════════════════════

def analyze_proof_structure(lean_code: str) -> ProofStructure:
    """Statically analyze Lean 4 proof code to extract structure."""
    structure = ProofStructure()
    lines = lean_code.splitlines()

    thm_match = _THEOREM_RE.search(lean_code)
    if thm_match:
        structure.theorem_name = thm_match.group(1).strip()
        structure.theorem_statement = thm_match.group(2).strip()[:200]

    structure.proof_strategy = _detect_strategy(lean_code)

    if thm_match:
        sig_text = lean_code[:thm_match.end()]
        for m in re.finditer(r'\((\w+)\s*:', sig_text):
            structure.hypothesis_names.add(m.group(1))

    known_names: dict[str, str] = {}
    available_names_by_step: dict[str, set[str]] = {}

    for h in structure.hypothesis_names:
        known_names[h] = "__theorem__"

    pending_sorry_id = None
    pending_claimed_reason = None
    stack: list[ProofStep] = []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        depth = indent // 2

        sid_match = _SORRY_ID_RE.search(stripped)
        if sid_match:
            pending_sorry_id = sid_match.group(1)
            continue

        cr_match = _CLAIMED_REASON_RE.search(stripped)
        if cr_match:
            pending_claimed_reason = cr_match.group(1).strip().strip('"')
            continue

        if _STEP_COMMENT_RE.search(stripped):
            continue

        step = None

        have_match = re.match(r'\s*(have)\s+([\w_]+)\s*:', line)
        if have_match:
            name = have_match.group(2)
            goal = line[have_match.end():].strip()
            goal = re.sub(r':=.*', '', goal).strip()
            step = ProofStep(
                name=name,
                step_type="have",
                line=i,
                depth=depth,
                goal_snippet=goal[:120],
            )
            step.introduces = [name]

        let_match = re.match(r'\s*(let)\s+([\w_]+)', line)
        if not step and let_match:
            name = let_match.group(2)
            step = ProofStep(
                name=name,
                step_type="let",
                line=i,
                depth=depth,
            )
            step.introduces = [name]

        obtain_match = re.match(r'\s*(obtain)\s*[⟨<]([^⟩>]+)[⟩>]\s*:=\s*([\w_]+)', line)
        if not step and obtain_match:
            vars_str = obtain_match.group(2)
            rhs_name = obtain_match.group(3)
            var_names = [v.strip() for v in vars_str.split(',') if v.strip()]
            name = f"obtain_L{i}"
            step = ProofStep(
                name=name,
                step_type="obtain",
                line=i,
                depth=depth,
            )
            step.introduces = var_names
            if rhs_name in known_names:
                step.references = [rhs_name]

        suffices_match = re.match(r'\s*(suffices)\s+([\w_]+)\s*:', line)
        if not step and suffices_match:
            name = suffices_match.group(2)
            step = ProofStep(
                name=name,
                step_type="suffices",
                line=i,
                depth=depth,
            )
            step.introduces = [name]

        if not step and stripped.startswith('calc'):
            step = ProofStep(
                name=f"calc_L{i}",
                step_type="calc",
                line=i,
                depth=depth,
            )

        if step:
            while stack and stack[-1].depth >= step.depth:
                stack.pop()
            step.parent = stack[-1].name if stack else None

            if pending_sorry_id:
                step.sorry_id = pending_sorry_id
                pending_sorry_id = None
            if pending_claimed_reason:
                step.claimed_reason = pending_claimed_reason
                pending_claimed_reason = None

            available_names_by_step[step.name] = set(known_names.keys())

            for intro_name in step.introduces:
                known_names[intro_name] = step.name

            structure.steps.append(step)
            stack.append(step)

    _compute_end_lines(structure.steps, lines)

    for step in structure.steps:
        block = _scanable_block_for_step(step, structure.steps, lines)
        found_refs = _find_references(block, available_names_by_step.get(step.name, set()))
        for ref in found_refs:
            if ref not in step.references:
                step.references.append(ref)
        step.has_sorry = _has_sorry_in_block(step, structure.steps, lines)

    structure.edges = _compute_edges(structure.steps, known_names)

    has_incoming = {e.to_step for e in structure.edges}
    for idx, step in enumerate(structure.steps):
        if step.name not in has_incoming and idx > 0 and step.has_sorry:
            prev_step = structure.steps[idx - 1]
            structure.edges.append(StaticEdge(
                from_step=prev_step.name,
                to_step=step.name,
                edge_type="sequential",
                evidence=f"{step.name} follows {prev_step.name} in proof order",
            ))

    structure.sorry_count = sum(1 for s in structure.steps if s.has_sorry)
    structure.max_depth = max((s.depth for s in structure.steps), default=0)

    has_incoming = {e.to_step for e in structure.edges}
    structure.root_steps = [s.name for s in structure.steps if s.name not in has_incoming]

    has_outgoing = {e.from_step for e in structure.edges}
    structure.leaf_steps = [s.name for s in structure.steps if s.name not in has_outgoing]

    structure.critical_chain = _compute_critical_chain(structure.steps, structure.edges)

    return structure
