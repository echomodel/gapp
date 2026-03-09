"""gapp init — local project setup."""

import subprocess
from pathlib import Path

from gapp.sdk.config import load_solutions, save_solutions
from gapp.sdk.context import get_git_root
from gapp.sdk.manifest import get_solution_name, load_manifest


def init_solution(repo_path: Path | None = None) -> dict:
    """Initialize a gapp solution in the current repo.

    Returns a dict describing what was done:
        name: solution name
        manifest_status: "exists" | "created"
        topic_status: "added" | "already_set" | "skipped"
        registered: bool
    """
    if repo_path:
        git_root = get_git_root(repo_path)
    else:
        git_root = get_git_root()
    if not git_root:
        raise RuntimeError("Not inside a git repository.")

    result = {"name": None, "manifest_status": None, "topic_status": None, "registered": False}

    # Ensure gapp.yaml exists
    manifest_path = git_root / "gapp.yaml"
    if manifest_path.exists():
        result["manifest_status"] = "exists"
    else:
        manifest_path.write_text(
            "service:\n"
            "  entrypoint: PACKAGE.mcp.server:mcp_app  # REQUIRED: update this\n"
        )
        result["manifest_status"] = "created"

    manifest = load_manifest(git_root)
    solution_name = get_solution_name(manifest, git_root)
    result["name"] = solution_name

    # Add GitHub topic
    result["topic_status"] = _add_github_topic(git_root)

    # Register in solutions.yaml
    solutions = load_solutions()
    if solution_name not in solutions:
        solutions[solution_name] = {}
    solutions[solution_name]["repo_path"] = str(git_root)
    save_solutions(solutions)
    result["registered"] = True

    return result


def _add_github_topic(repo_path: Path) -> str:
    """Add gapp-solution topic to the GitHub repo."""
    try:
        # Check current topics
        check = subprocess.run(
            ["gh", "repo", "view", "--json", "repositoryTopics"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        if check.returncode != 0:
            return "skipped"

        import json
        data = json.loads(check.stdout)
        topics = [t["name"] for t in (data.get("repositoryTopics") or [])]

        if "gapp-solution" in topics:
            return "already_set"

        # Add topic
        subprocess.run(
            ["gh", "repo", "edit", "--add-topic", "gapp-solution"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        return "added"
    except FileNotFoundError:
        return "skipped"
