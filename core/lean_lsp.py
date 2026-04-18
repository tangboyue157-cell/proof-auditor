"""Lean LSP interface for Proof Auditor.

Wraps leanclient to provide high-level operations for sorry diagnosis:
  1. Get goal state at any sorry location
  2. Get structured diagnostics
  3. Try tactics (exact?, apply?, simp?) at sorry positions
  4. Search Mathlib via LeanSearch

Requires: .venv with leanclient installed (via lean-lsp-mcp).

Usage:
    from core.lean_lsp import LeanLSP

    with LeanLSP() as lsp:
        result = lsp.analyze_file("ProofAuditor/Workspace/Buggy.lean")
        for sorry in result.sorry_goals:
            print(f"Line {sorry.line}: {sorry.goal}")
"""

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Ensure elan binaries (lake, lean) are on PATH
_elan_bin = str(Path.home() / ".elan" / "bin")
if _elan_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = _elan_bin + os.pathsep + os.environ.get("PATH", "")

from leanclient import LeanLSPClient

# Suppress verbose leanclient logs
logging.getLogger("leanclient").setLevel(logging.WARNING)

# Project root
PROJECT_ROOT = Path(__file__).parent.parent


@dataclass
class SorryGoal:
    """A sorry location with its proof goal state."""
    file: str
    line: int          # 1-indexed
    column: int        # 0-indexed
    goal: str          # The proof goal at this sorry
    context: str = ""  # Local context (hypotheses)


@dataclass
class TacticResult:
    """Result of trying a tactic at a sorry position."""
    tactic: str
    success: bool
    result: str = ""   # The resulting code or error message


@dataclass
class FileAnalysis:
    """Complete analysis of a Lean file."""
    file_path: str
    compiles: bool
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    sorry_goals: list[SorryGoal] = field(default_factory=list)


class LeanLSP:
    """High-level Lean LSP interface for sorry diagnosis.

    Usage as context manager:
        with LeanLSP() as lsp:
            analysis = lsp.analyze_file("path/to/file.lean")
    """

    def __init__(self, project_root: Optional[str] = None):
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        self._client: Optional[LeanLSPClient] = None

    def __enter__(self):
        self._client = LeanLSPClient(
            str(self.project_root),
            initial_build=False,
            prevent_cache_get=True,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._client:
            self._client.close()
            self._client = None

    @property
    def client(self) -> LeanLSPClient:
        if self._client is None:
            raise RuntimeError("LeanLSP not initialized. Use 'with LeanLSP() as lsp:'")
        return self._client

    # ------------------------------------------------------------------
    # Core: analyze a file and extract all sorry goals
    # ------------------------------------------------------------------

    def analyze_file(self, file_path: str) -> FileAnalysis:
        """Analyze a Lean file: compile, extract diagnostics and sorry goals.

        Args:
            file_path: Relative path to .lean file (from project root).

        Returns:
            FileAnalysis with compilation status, errors, and sorry goals.
        """
        self.client.open_file(file_path)

        # Get diagnostics
        diag_result = self.client.get_diagnostics(file_path)
        diagnostics = diag_result.diagnostics if hasattr(diag_result, 'diagnostics') else diag_result
        if isinstance(diagnostics, dict):
            raw_diags = diagnostics.get("diagnostics", [])
            compiles = diagnostics.get("success", True)
        elif isinstance(diagnostics, list):
            raw_diags = diagnostics
            compiles = True
        else:
            raw_diags = list(diagnostics) if diagnostics else []
            compiles = True

        errors = []
        warnings = []
        for d in raw_diags:
            severity = d.get("severity", 1)
            msg = d.get("message", "")
            r = d.get("fullRange", d.get("range", {}))
            start = r.get("start", {})
            info = {
                "line": start.get("line", 0) + 1,
                "column": start.get("character", 0),
                "message": msg,
            }
            if severity == 1:
                errors.append(info)
                compiles = False
            elif severity == 2:
                # sorry warnings indicate sorry usage
                if "declaration uses 'sorry'" in msg:
                    pass  # Expected, not a real error
                else:
                    warnings.append(info)

        # Find sorry locations by scanning the file
        sorry_goals = self._extract_sorry_goals(file_path)

        return FileAnalysis(
            file_path=file_path,
            compiles=compiles,
            errors=errors,
            warnings=warnings,
            sorry_goals=sorry_goals,
        )

    def _extract_sorry_goals(self, file_path: str) -> list[SorryGoal]:
        """Find all sorry locations and get their goal states."""
        full_path = self.project_root / file_path
        if not full_path.exists():
            return []

        sorry_positions = []
        with open(full_path) as f:
            for i, line in enumerate(f):
                for match in re.finditer(r'\bsorry\b', line):
                    # Check not in comment
                    comment_start = line.find("--")
                    if comment_start < 0 or match.start() < comment_start:
                        sorry_positions.append((i, match.start()))  # 0-indexed

        goals = []
        for line_0, col_0 in sorry_positions:
            goal_text = self.get_goal(file_path, line_0 + 1, col_0)
            goals.append(SorryGoal(
                file=file_path,
                line=line_0 + 1,
                column=col_0,
                goal=goal_text,
            ))

        return goals

    # ------------------------------------------------------------------
    # Goal state at a position
    # ------------------------------------------------------------------

    def get_goal(self, file_path: str, line: int, column: int) -> str:
        """Get the proof goal at a specific position.

        Args:
            file_path: Relative path to .lean file.
            line: 1-indexed line number.
            column: 0-indexed column number.

        Returns:
            Goal state as a string. Empty string if no goal found.
        """
        try:
            self.client.open_file(file_path)
            result = self.client.get_goal(file_path, line - 1, column)
            if result and isinstance(result, dict):
                goals = result.get("goals", [])
                if goals:
                    return "\n".join(goals)
                rendered = result.get("rendered", "")
                if rendered:
                    return rendered
            elif result and isinstance(result, str):
                return result
            return ""
        except Exception as e:
            return f"[LSP error: {e}]"

    # ------------------------------------------------------------------
    # Hover info (type checking)
    # ------------------------------------------------------------------

    def get_hover(self, file_path: str, line: int, column: int) -> str:
        """Get hover information (type, docstring) at a position.

        Useful for Diagnostician to verify translation correctness (Type B).

        Args:
            file_path: Relative path.
            line: 1-indexed.
            column: 0-indexed.

        Returns:
            Hover info as string.
        """
        try:
            self.client.open_file(file_path)
            result = self.client.get_hover(file_path, line - 1, column)
            if result and isinstance(result, dict):
                contents = result.get("contents", {})
                if isinstance(contents, dict):
                    return contents.get("value", "")
                return str(contents)
            return str(result) if result else ""
        except Exception as e:
            return f"[LSP error: {e}]"

    # ------------------------------------------------------------------
    # Try tactics at a sorry position
    # ------------------------------------------------------------------

    def try_tactics(
        self,
        file_path: str,
        line: int,
        column: int,
        tactics: Optional[list[str]] = None,
    ) -> list[TacticResult]:
        """Try multiple tactics at a sorry position.

        This is crucial for sorry classification:
          - If exact?/apply? solves it → Type D (API miss)
          - If simp? solves it → Type D
          - If nothing works → could be Type A, C, or E

        Args:
            file_path: Relative path.
            line: 1-indexed line of the sorry.
            column: 0-indexed column.
            tactics: List of tactics to try. Defaults to standard search tactics.

        Returns:
            List of TacticResult.
        """
        if tactics is None:
            tactics = ["exact?", "apply?", "simp?", "omega", "norm_num", "decide"]

        results = []
        full_path = self.project_root / file_path

        # Read the file
        with open(full_path) as f:
            lines = f.readlines()

        sorry_line_idx = line - 1
        if sorry_line_idx >= len(lines):
            return results

        original_line = lines[sorry_line_idx]

        for tactic in tactics:
            # Replace sorry with the tactic
            modified_line = original_line.replace("sorry", tactic, 1)
            modified_lines = lines.copy()
            modified_lines[sorry_line_idx] = modified_line
            modified_content = "".join(modified_lines)

            try:
                # Update the file in LSP and check diagnostics
                self.client.update_file_content(file_path, modified_content)
                diag_result = self.client.get_diagnostics(file_path)
                diagnostics = diag_result.diagnostics if hasattr(diag_result, 'diagnostics') else diag_result
                if isinstance(diagnostics, dict):
                    raw_diags = diagnostics.get("diagnostics", [])
                elif isinstance(diagnostics, list):
                    raw_diags = diagnostics
                else:
                    raw_diags = list(diagnostics) if diagnostics else []

                # Check if there are errors at or near this line
                has_error_at_line = False
                suggestion = ""
                for d in raw_diags:
                    sev = d.get("severity", 1)
                    msg = d.get("message", "")
                    r = d.get("fullRange", d.get("range", {}))
                    d_line = r.get("start", {}).get("line", -1)

                    # If there's a "Try this:" suggestion, capture it
                    if "Try this:" in msg and abs(d_line - sorry_line_idx) <= 2:
                        suggestion = msg
                    # Error at or near this line means the tactic didn't work
                    if sev == 1 and abs(d_line - sorry_line_idx) <= 2:
                        has_error_at_line = True

                results.append(TacticResult(
                    tactic=tactic,
                    success=not has_error_at_line,
                    result=suggestion if suggestion else ("✅ solved" if not has_error_at_line else "❌ failed"),
                ))

            except Exception as e:
                results.append(TacticResult(
                    tactic=tactic,
                    success=False,
                    result=f"[error: {e}]",
                ))

        # Restore original file content
        try:
            original_content = "".join(lines)
            self.client.update_file_content(file_path, original_content)
        except Exception:
            pass

        return results

    # ------------------------------------------------------------------
    # Convenience: full sorry diagnosis
    # ------------------------------------------------------------------

    def diagnose_sorry(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> dict:
        """Complete diagnosis of a single sorry.

        Combines goal extraction + tactic search to provide
        preliminary classification signals.

        Returns:
            Dict with goal, tactic_results, and suggested_type.
        """
        goal = self.get_goal(file_path, line, column)
        tactic_results = self.try_tactics(file_path, line, column)

        # Preliminary classification based on tactic results
        any_solved = any(r.success for r in tactic_results)

        if any_solved:
            suggested_type = "D"  # API miss — a tactic solved it
        else:
            suggested_type = "unknown"  # Needs Diagnostician Agent

        return {
            "file": file_path,
            "line": line,
            "column": column,
            "goal": goal,
            "tactic_results": [
                {"tactic": r.tactic, "success": r.success, "result": r.result}
                for r in tactic_results
            ],
            "any_tactic_solved": any_solved,
            "suggested_type": suggested_type,
        }


if __name__ == "__main__":
    import json
    import sys

    file = sys.argv[1] if len(sys.argv) > 1 else "ProofAuditor/Workspace/Buggy.lean"
    print(f"Analyzing {file} with Lean LSP...")

    with LeanLSP() as lsp:
        analysis = lsp.analyze_file(file)

        print(f"\nCompiles: {analysis.compiles}")
        print(f"Errors: {len(analysis.errors)}")
        print(f"Sorry goals: {len(analysis.sorry_goals)}")
        print()

        for sg in analysis.sorry_goals:
            print(f"--- Line {sg.line} ---")
            print(f"Goal: {sg.goal}")

            # Try tactics
            diagnosis = lsp.diagnose_sorry(file, sg.line, sg.column)
            print(f"Suggested type: {diagnosis['suggested_type']}")
            for tr in diagnosis["tactic_results"]:
                status = "✅" if tr["success"] else "❌"
                print(f"  {status} {tr['tactic']}: {tr['result']}")
            print()
