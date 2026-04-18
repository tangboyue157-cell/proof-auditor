"""Proof Graph module — DAG-based forensic analysis of sorry dependencies.

Combines two layers to build a complete dependency graph:
  1. Static layer: syntactic hypothesis-reference analysis (scripts)
  2. AI layer: semantic mathematical causality analysis (LLM)

Then performs:
  - Topological sort for optimal diagnosis ordering
  - Impact score computation (how many downstream sorrys does each block?)
  - Root cause identification
  - Critical path analysis
  - Root cause tree generation for display

Usage:
    from core.proof_graph import ProofGraph, build_proof_graph

    graph = build_proof_graph(client, lean_code, sorry_diagnoses, original_proof)
    print(graph.root_cause_tree())
"""

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.ai_client import AIClient

AGENTS_DIR = Path(__file__).parent.parent / "agents"


@dataclass
class GraphEdge:
    """A directed dependency edge between two sorry nodes."""
    from_id: str            # upstream sorry
    to_id: str              # downstream sorry (depends on from_id)
    edge_type: str          # data_flow | logical_prerequisite | structural | transitive
    confidence: float       # 0.0 - 1.0
    explanation: str = ""
    source: str = ""        # "static" or "ai"


@dataclass
class GraphNode:
    """A sorry node in the proof graph."""
    sorry_id: str
    line: int
    goal: str = ""
    # Computed properties
    depth: int = 0                          # Distance from root (0 = root)
    impact_score: int = 0                   # Number of downstream nodes
    in_degree: int = 0                      # Number of upstream dependencies
    out_degree: int = 0                     # Number of downstream dependents
    is_root: bool = False                   # No upstream dependencies
    is_leaf: bool = False                   # No downstream dependents
    is_on_critical_path: bool = False       # Part of the longest chain
    independent_group: Optional[int] = None # Which connected component


class ProofGraph:
    """Complete proof dependency graph with analysis capabilities.

    The graph is a DAG where:
      - Nodes are sorry gaps
      - Edges point from upstream (dependency) to downstream (dependent)
      - Root nodes have no incoming edges (independent root causes)
      - Leaf nodes have no outgoing edges (final conclusions)
    """

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        # Adjacency lists
        self._children: dict[str, list[str]] = defaultdict(list)   # parent → children
        self._parents: dict[str, list[str]] = defaultdict(list)    # child → parents
        # Analysis results
        self.topo_order: list[str] = []
        self.critical_path: list[str] = []
        self.independent_groups: list[list[str]] = []

    def add_node(self, sorry_id: str, line: int, goal: str = "") -> None:
        """Add a sorry node."""
        if sorry_id not in self.nodes:
            self.nodes[sorry_id] = GraphNode(sorry_id=sorry_id, line=line, goal=goal)

    def add_edge(self, edge: GraphEdge) -> None:
        """Add a dependency edge. from_id → to_id means to_id depends on from_id."""
        # Deduplicate
        for existing in self.edges:
            if existing.from_id == edge.from_id and existing.to_id == edge.to_id:
                if edge.confidence > existing.confidence:
                    existing.confidence = edge.confidence
                    existing.explanation = edge.explanation
                    existing.source = edge.source
                return

        self.edges.append(edge)
        self._children[edge.from_id].append(edge.to_id)
        self._parents[edge.to_id].append(edge.from_id)

    def analyze(self) -> None:
        """Run full graph analysis: topo sort, impact, critical path, groups."""
        self._compute_degrees()
        self._topological_sort()
        self._compute_depth_and_impact()
        self._find_critical_path()
        self._find_independent_groups()

    # ── Core algorithms ───────────────────────────────

    def _compute_degrees(self) -> None:
        for node in self.nodes.values():
            node.in_degree = len(self._parents.get(node.sorry_id, []))
            node.out_degree = len(self._children.get(node.sorry_id, []))
            node.is_root = node.in_degree == 0
            node.is_leaf = node.out_degree == 0

    def _topological_sort(self) -> None:
        """Kahn's algorithm for topological ordering."""
        in_deg = {nid: len(self._parents.get(nid, [])) for nid in self.nodes}
        queue = deque(nid for nid, d in in_deg.items() if d == 0)
        order = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for child in self._children.get(nid, []):
                in_deg[child] -= 1
                if in_deg[child] == 0:
                    queue.append(child)

        if len(order) != len(self.nodes):
            # Cycle detected — fall back to line-number ordering
            remaining = set(self.nodes.keys()) - set(order)
            order.extend(sorted(remaining, key=lambda x: self.nodes[x].line))

        self.topo_order = order

    def _compute_depth_and_impact(self) -> None:
        """Compute depth from root and impact score (descendant count)."""
        # Forward pass: compute depth
        for nid in self.topo_order:
            node = self.nodes[nid]
            for parent_id in self._parents.get(nid, []):
                parent_node = self.nodes[parent_id]
                node.depth = max(node.depth, parent_node.depth + 1)

        # Backward pass: compute impact (number of reachable descendants)
        descendant_count: dict[str, set] = {nid: set() for nid in self.nodes}
        for nid in reversed(self.topo_order):
            for child_id in self._children.get(nid, []):
                descendant_count[nid].add(child_id)
                descendant_count[nid] |= descendant_count[child_id]
            self.nodes[nid].impact_score = len(descendant_count[nid])

    def _find_critical_path(self) -> None:
        """Find the longest path in the DAG (critical path)."""
        if not self.topo_order:
            return

        dist: dict[str, int] = {nid: 0 for nid in self.nodes}
        prev: dict[str, Optional[str]] = {nid: None for nid in self.nodes}

        for nid in self.topo_order:
            for child_id in self._children.get(nid, []):
                if dist[nid] + 1 > dist[child_id]:
                    dist[child_id] = dist[nid] + 1
                    prev[child_id] = nid

        # Find the endpoint of longest path
        end = max(dist, key=lambda x: dist[x]) if dist else None
        if end is None:
            return

        # Trace back
        path = []
        current = end
        while current is not None:
            path.append(current)
            current = prev[current]
        path.reverse()

        self.critical_path = path
        for nid in path:
            self.nodes[nid].is_on_critical_path = True

    def _find_independent_groups(self) -> None:
        """Find connected components (independent subgraphs)."""
        visited: set[str] = set()
        groups: list[list[str]] = []

        # Build undirected adjacency
        undirected: dict[str, set[str]] = defaultdict(set)
        for edge in self.edges:
            undirected[edge.from_id].add(edge.to_id)
            undirected[edge.to_id].add(edge.from_id)

        for nid in self.nodes:
            if nid in visited:
                continue
            # BFS
            group = []
            queue = deque([nid])
            while queue:
                current = queue.popleft()
                if current in visited:
                    continue
                visited.add(current)
                group.append(current)
                for neighbor in undirected.get(current, []):
                    if neighbor not in visited:
                        queue.append(neighbor)
            groups.append(sorted(group, key=lambda x: self.nodes[x].line))

        self.independent_groups = groups

        # Assign group IDs
        for i, group in enumerate(groups):
            for nid in group:
                self.nodes[nid].independent_group = i

    # ── Query methods ─────────────────────────────────

    @property
    def root_nodes(self) -> list[GraphNode]:
        """Nodes with no upstream dependencies (root causes)."""
        return [n for n in self.nodes.values() if n.is_root]

    @property
    def leaf_nodes(self) -> list[GraphNode]:
        """Nodes with no downstream dependents."""
        return [n for n in self.nodes.values() if n.is_leaf]

    def diagnosis_order(self) -> list[str]:
        """Optimal order for diagnosing sorrys.

        Roots first, then propagate. Within the same depth, higher impact first.
        """
        return sorted(
            self.topo_order,
            key=lambda nid: (self.nodes[nid].depth, -self.nodes[nid].impact_score)
        )

    def blocked_by(self, sorry_id: str) -> list[str]:
        """Get all upstream dependencies for a sorry."""
        return self._parents.get(sorry_id, [])

    def blocks(self, sorry_id: str) -> list[str]:
        """Get all downstream dependents of a sorry."""
        return self._children.get(sorry_id, [])

    def all_ancestors(self, sorry_id: str) -> set[str]:
        """Get ALL transitive ancestors (upstream)."""
        ancestors: set[str] = set()
        queue = deque(self._parents.get(sorry_id, []))
        while queue:
            parent = queue.popleft()
            if parent not in ancestors:
                ancestors.add(parent)
                queue.extend(self._parents.get(parent, []))
        return ancestors

    def all_descendants(self, sorry_id: str) -> set[str]:
        """Get ALL transitive descendants (downstream)."""
        descendants: set[str] = set()
        queue = deque(self._children.get(sorry_id, []))
        while queue:
            child = queue.popleft()
            if child not in descendants:
                descendants.add(child)
                queue.extend(self._children.get(child, []))
        return descendants

    # ── Display ───────────────────────────────────────

    def root_cause_tree(self, emoji_map: Optional[dict] = None) -> str:
        """Generate a root cause tree visualization.

        Example output:
            ROOT CAUSE TREE:
            ├── 🔴 [A1] sorry_L16 (impact: 3)
            │   ├── ⬜ [G] sorry_L23
            │   │   └── ⬜ [G] sorry_L38
            │   └── ⬜ [G] sorry_L28 → sorry_L38
            └── 🟢 [D] sorry_L52 (independent)
        """
        if not self.topo_order:
            self.analyze()

        lines = ["ROOT CAUSE TREE:"]
        roots = self.root_nodes
        if not roots:
            return "ROOT CAUSE TREE: (no roots found)"

        for i, root in enumerate(roots):
            is_last = i == len(roots) - 1
            prefix = "└── " if is_last else "├── "
            child_prefix = "    " if is_last else "│   "
            lines.append(f"{prefix}{root.sorry_id} (impact: {root.impact_score})")
            self._tree_children(root.sorry_id, child_prefix, lines)

        return "\n".join(lines)

    def _tree_children(self, node_id: str, prefix: str, lines: list[str]) -> None:
        children = self._children.get(node_id, [])
        for i, child_id in enumerate(children):
            is_last = i == len(children) - 1
            connector = "└── " if is_last else "├── "
            child_prefix = prefix + ("    " if is_last else "│   ")
            impact = self.nodes[child_id].impact_score
            impact_str = f" (impact: {impact})" if impact > 0 else ""
            lines.append(f"{prefix}{connector}{child_id}{impact_str}")
            self._tree_children(child_id, child_prefix, lines)

    def to_dict(self) -> dict:
        """Serialize graph for JSON report."""
        return {
            "nodes": [
                {
                    "sorry_id": n.sorry_id,
                    "line": n.line,
                    "depth": n.depth,
                    "impact_score": n.impact_score,
                    "is_root": n.is_root,
                    "is_leaf": n.is_leaf,
                    "is_on_critical_path": n.is_on_critical_path,
                    "independent_group": n.independent_group,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "from": e.from_id,
                    "to": e.to_id,
                    "type": e.edge_type,
                    "confidence": e.confidence,
                    "source": e.source,
                    "explanation": e.explanation,
                }
                for e in self.edges
            ],
            "topo_order": self.topo_order,
            "critical_path": self.critical_path,
            "independent_groups": self.independent_groups,
            "root_node_ids": [n.sorry_id for n in self.root_nodes],
        }


# ── Static layer: syntactic dependency extraction ──────────


def _extract_static_edges(
    lean_code: str,
    sorry_diagnoses: list[dict],
) -> list[GraphEdge]:
    """Extract dependency edges from Lean code syntax.

    Looks for:
      1. `have h : ... := by sorry` — sorry introduces hypothesis h
      2. Later sorry goals that reference h in their goal state
    """
    lines = lean_code.splitlines()
    sorry_lines = {d["line"]: d for d in sorry_diagnoses}

    # Step 1: Find hypotheses introduced by sorry-containing blocks
    sorry_hypotheses: dict[str, list[str]] = {}  # sorry_id → [hypothesis_names]
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        have_match = re.match(r'have\s+(\w+)\s*:', stripped)
        obtain_match = re.match(r'obtain\s+⟨([^⟩]+)⟩', stripped)

        hyp_names = []
        if have_match:
            hyp_names = [have_match.group(1)]
        elif obtain_match:
            hyp_names = [h.strip() for h in obtain_match.group(1).split(",")]

        if hyp_names:
            for j in range(i, min(i + 5, len(lines) + 1)):
                if j in sorry_lines:
                    sid = f"sorry_L{j}"
                    sorry_hypotheses[sid] = hyp_names
                    break

    # Step 2: Check which sorry goals reference hypotheses from earlier sorrys
    edges = []
    for diag in sorry_diagnoses:
        line = diag["line"]
        goal = diag.get("goal", "")
        current_id = f"sorry_L{line}"

        for upstream_id, hyp_names in sorry_hypotheses.items():
            upstream_line = int(upstream_id.split("_L")[1])
            if upstream_line >= line:
                continue
            for hyp in hyp_names:
                if hyp in goal:
                    edges.append(GraphEdge(
                        from_id=upstream_id,
                        to_id=current_id,
                        edge_type="data_flow",
                        confidence=0.9,
                        explanation=f"Goal references hypothesis '{hyp}' from {upstream_id}",
                        source="static",
                    ))

    return edges


# ── AI layer: semantic dependency analysis ─────────────────


def _extract_ai_edges(
    client: AIClient,
    original_proof: str,
    lean_code: str,
    sorry_diagnoses: list[dict],
    static_edges: list[GraphEdge],
) -> list[GraphEdge]:
    """Use AI to identify semantic dependencies between sorrys.

    The AI sees the original proof, Lean code, all sorry goals,
    and the already-detected static edges, then identifies
    additional mathematical causality relationships.
    """
    system_prompt_path = AGENTS_DIR / "graph_analyst.md"
    system_prompt = system_prompt_path.read_text() if system_prompt_path.exists() else ""

    # Format sorry list
    sorry_list = ""
    for d in sorry_diagnoses:
        goal_preview = d.get("goal", "N/A").replace("\n", " ")[:100]
        sorry_list += f"  - sorry_L{d['line']}: {goal_preview}\n"

    # Format static edges
    static_summary = ""
    for e in static_edges:
        static_summary += f"  - {e.from_id} → {e.to_id}: {e.explanation}\n"
    if not static_summary:
        static_summary = "  (none detected)\n"

    user_prompt = f"""{system_prompt}

## Original Proof
{original_proof}

## Lean Translation (with sorrys)
{lean_code}

## All Sorry Gaps
{sorry_list}

## Already-Detected Syntactic Dependencies
{static_summary}

Analyze the SEMANTIC dependencies between these sorrys.
Only add edges that are NOT already in the syntactic list above.
Respond with ONLY a JSON object.
"""

    try:
        from core.ai_client import get_cost_tracker
        get_cost_tracker().set_round("R2.5_graph_analysis")

        resp = client.chat(user_prompt)
        result = _parse_json(resp.content)
    except Exception as e:
        return []  # Fail-open: no AI edges on failure

    # Parse edges from AI response
    ai_edges = []
    for edge_data in result.get("edges", []):
        from_id = edge_data.get("from", "")
        to_id = edge_data.get("to", "")
        if not from_id or not to_id:
            continue

        ai_edges.append(GraphEdge(
            from_id=from_id,
            to_id=to_id,
            edge_type=edge_data.get("type", "logical_prerequisite"),
            confidence=float(edge_data.get("confidence", 0.7)),
            explanation=edge_data.get("explanation", ""),
            source="ai",
        ))

    return ai_edges


# ── Graph builder ──────────────────────────────────────────


def build_proof_graph(
    client: AIClient,
    lean_code: str,
    sorry_diagnoses: list[dict],
    original_proof: str,
) -> ProofGraph:
    """Build a complete proof dependency graph.

    Combines static (syntactic) and AI (semantic) analysis.

    Args:
        client: AI client for semantic analysis.
        lean_code: The Lean 4 code with sorrys.
        sorry_diagnoses: List of sorry diagnosis dicts.
        original_proof: Original natural language proof.

    Returns:
        Fully analyzed ProofGraph.
    """
    graph = ProofGraph()

    # Add nodes
    for diag in sorry_diagnoses:
        sorry_id = f"sorry_L{diag['line']}"
        graph.add_node(sorry_id, diag["line"], diag.get("goal", ""))

    # Layer 1: Static edges
    static_edges = _extract_static_edges(lean_code, sorry_diagnoses)
    for edge in static_edges:
        graph.add_edge(edge)

    # Layer 2: AI edges
    ai_edges = _extract_ai_edges(
        client, original_proof, lean_code, sorry_diagnoses, static_edges
    )
    for edge in ai_edges:
        graph.add_edge(edge)

    # Run full analysis
    graph.analyze()

    return graph


def _parse_json(text: str) -> dict:
    """Parse JSON from AI response."""
    json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])
        except json.JSONDecodeError:
            pass
    return {"edges": []}
