"""Lean compiler interface for Proof Auditor.

Provides functions to:
  1. Compile a Lean file and collect diagnostics
  2. Extract sorry locations and goal states
  3. Run tactic search (exact?, apply?, simp?)

Uses the `lake env lean` command and parses Lean's output.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Path to the Lean project root (proof-auditor/)
PROJECT_ROOT = Path(__file__).parent.parent


@dataclass
class LeanDiagnostic:
    """A single Lean diagnostic message."""
    file: str
    line: int
    column: int
    severity: str  # "error", "warning", "info"
    message: str


@dataclass
class SorryLocation:
    """Location of a sorry in a Lean file."""
    file: str
    line: int
    column: int
    goal_state: str = ""  # The proof goal at this point


@dataclass
class CompilationResult:
    """Result of compiling a Lean file."""
    success: bool
    diagnostics: list[LeanDiagnostic]
    sorry_locations: list[SorryLocation]
    output: str = ""
    error: str = ""


def _find_lean_bin() -> str:
    """Find the lean binary, checking elan paths."""
    import shutil
    from pathlib import Path

    # Check elan path first
    elan_lean = Path.home() / ".elan" / "bin" / "lean"
    if elan_lean.exists():
        return str(elan_lean)

    # Check PATH
    lean_path = shutil.which("lean")
    if lean_path:
        return lean_path

    raise FileNotFoundError(
        "Lean not found. Install via: curl https://elan.lean-lang.org/elan-init.sh -sSf | sh"
    )


def _find_lake_bin() -> str:
    """Find the lake binary."""
    import shutil
    from pathlib import Path

    elan_lake = Path.home() / ".elan" / "bin" / "lake"
    if elan_lake.exists():
        return str(elan_lake)

    lake_path = shutil.which("lake")
    if lake_path:
        return lake_path

    raise FileNotFoundError("lake not found.")


def compile_lean_file(
    file_path: str,
    project_root: Optional[str] = None,
    timeout: int = 120,
) -> CompilationResult:
    """Compile a Lean file and collect diagnostics.

    Args:
        file_path: Path to the .lean file (relative to project root).
        project_root: Path to the Lean project root. Defaults to proof-auditor/.
        timeout: Maximum compilation time in seconds.

    Returns:
        CompilationResult with success status, diagnostics, and sorry locations.
    """
    root = Path(project_root) if project_root else PROJECT_ROOT
    lake = _find_lake_bin()

    try:
        result = subprocess.run(
            [lake, "env", "lean", file_path],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CompilationResult(
            success=False,
            diagnostics=[],
            sorry_locations=[],
            error=f"Compilation timed out after {timeout}s",
        )

    diagnostics = _parse_diagnostics(result.stderr + result.stdout)
    sorry_locs = _extract_sorry_locations(file_path, root)

    return CompilationResult(
        success=result.returncode == 0,
        diagnostics=diagnostics,
        sorry_locations=sorry_locs,
        output=result.stdout,
        error=result.stderr,
    )


def _parse_diagnostics(output: str) -> list[LeanDiagnostic]:
    """Parse Lean compiler output into structured diagnostics."""
    diagnostics = []
    # Lean diagnostic format: file:line:col: severity: message
    pattern = re.compile(r"^(.+?):(\d+):(\d+):\s*(error|warning|info):\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(output):
        diagnostics.append(LeanDiagnostic(
            file=match.group(1),
            line=int(match.group(2)),
            column=int(match.group(3)),
            severity=match.group(4),
            message=match.group(5).strip(),
        ))
    return diagnostics


def _extract_sorry_locations(file_path: str, project_root: Path) -> list[SorryLocation]:
    """Find all sorry occurrences in a Lean file."""
    full_path = project_root / file_path
    if not full_path.exists():
        return []

    locations = []
    with open(full_path) as f:
        for i, line in enumerate(f, 1):
            # Match 'sorry' as a standalone tactic/term
            for match in re.finditer(r'\bsorry\b', line):
                if not _is_in_comment(line, match.start()):
                    locations.append(SorryLocation(
                        file=file_path,
                        line=i,
                        column=match.start(),
                    ))
    return locations


def _is_in_comment(line: str, pos: int) -> bool:
    """Check if position is inside a line comment."""
    comment_start = line.find("--")
    if comment_start >= 0 and pos > comment_start:
        return True
    return False


def count_sorry(file_path: str, project_root: Optional[str] = None) -> int:
    """Count sorry occurrences in a Lean file.

    Args:
        file_path: Path to .lean file.
        project_root: Lean project root.

    Returns:
        Number of sorry occurrences.
    """
    root = Path(project_root) if project_root else PROJECT_ROOT
    locations = _extract_sorry_locations(file_path, root)
    return len(locations)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m core.compiler <file.lean>")
        sys.exit(1)

    file = sys.argv[1]
    print(f"Compiling {file}...")
    result = compile_lean_file(file)

    print(f"Success: {result.success}")
    print(f"Diagnostics: {len(result.diagnostics)}")
    for d in result.diagnostics:
        print(f"  {d.file}:{d.line}:{d.column} [{d.severity}] {d.message}")

    print(f"Sorry locations: {len(result.sorry_locations)}")
    for s in result.sorry_locations:
        print(f"  {s.file}:{s.line}:{s.column}")
