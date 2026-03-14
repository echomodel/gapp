"""Solution context resolution — determine which solution to operate on."""

import subprocess
from pathlib import Path

from gapp.admin.sdk.config import load_solutions
from gapp.admin.sdk.manifest import get_solution_name, load_manifest


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


def resolve_full_context(solution: str | None = None) -> dict:
    """Resolve solution context with remote fallbacks.

    Like resolve_solution, but fills in missing fields by querying
    GCP labels and GitHub. Returns a dict with:
        name, project_id, repo_path, github_repo
    Any field may be None if it can't be resolved.
    """
    ctx = resolve_solution(solution)
    if not ctx and solution:
        ctx = {"name": solution, "project_id": None, "repo_path": None}
    if not ctx:
        return {"name": None, "project_id": None, "repo_path": None, "github_repo": None}

    result = {**ctx, "github_repo": None}

    # Fill project_id from GCP labels if missing
    if not result.get("project_id"):
        from gapp.admin.sdk.setup import _discover_project_from_label
        result["project_id"] = _discover_project_from_label(result["name"])

    # Fill github_repo from local git remote
    repo_path = result.get("repo_path")
    if repo_path:
        expanded = Path(repo_path).expanduser()
        if expanded.exists():
            try:
                gh = subprocess.run(
                    ["gh", "repo", "view", "--json", "nameWithOwner",
                     "--jq", ".nameWithOwner"],
                    capture_output=True, text=True, cwd=expanded,
                )
                if gh.returncode == 0 and gh.stdout.strip():
                    result["github_repo"] = gh.stdout.strip()
            except FileNotFoundError:
                pass

    # Fall back to GitHub topic search
    if not result.get("github_repo"):
        try:
            gh = subprocess.run(
                ["gh", "search", "repos", "--topic", "gapp-solution",
                 "--owner", "@me", "--json", "fullName",
                 "--jq", f'[.[] | select(.fullName | endswith("/{result["name"]}"))] | .[0].fullName'],
                capture_output=True, text=True,
            )
            if gh.returncode == 0 and gh.stdout.strip():
                result["github_repo"] = gh.stdout.strip()
        except FileNotFoundError:
            pass

    # Last fallback: owner/name convention
    if not result.get("github_repo"):
        try:
            gh = subprocess.run(
                ["gh", "api", "user", "--jq", ".login"],
                capture_output=True, text=True,
            )
            if gh.returncode == 0 and gh.stdout.strip():
                result["github_repo"] = f"{gh.stdout.strip()}/{result['name']}"
        except FileNotFoundError:
            pass

    return result
