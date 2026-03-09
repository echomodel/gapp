"""Solution context resolution — determine which solution to operate on."""

import subprocess
from pathlib import Path

from gapp.sdk.config import load_solutions
from gapp.sdk.manifest import get_solution_name, load_manifest


def get_git_root(path: Path | None = None) -> Path | None:
    """Find the git root directory from the given path or cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=path or Path.cwd(),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def resolve_solution(name: str | None = None) -> dict | None:
    """Resolve solution context.

    Priority:
    1. Explicit name on command line
    2. Current directory (git repo with gapp.yaml)
    3. None (caller decides what to do)

    Returns dict with keys: name, project_id (may be None), repo_path (may be None)
    """
    solutions = load_solutions()

    if name:
        entry = solutions.get(name, {})
        return {
            "name": name,
            "project_id": entry.get("project_id"),
            "repo_path": entry.get("repo_path"),
        }

    git_root = get_git_root()
    if git_root and (git_root / "gapp.yaml").is_file():
        manifest = load_manifest(git_root)
        solution_name = get_solution_name(manifest, git_root)
        entry = solutions.get(solution_name, {})
        return {
            "name": solution_name,
            "project_id": entry.get("project_id"),
            "repo_path": str(git_root),
        }

    return None
