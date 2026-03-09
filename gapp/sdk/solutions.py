"""Solution listing and discovery."""

import json
import subprocess
from pathlib import Path

from gapp.sdk.config import load_solutions


def _display_path(path: str | None) -> str | None:
    """Shorten home-relative paths to ~/..."""
    if not path:
        return path
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def list_solutions(include_remote: bool = False) -> list[dict]:
    """List known solutions.

    Returns list of dicts with keys: name, project_id, repo_path, source.
    If include_remote, also queries GitHub for repos with gapp-solution topic.
    """
    solutions = load_solutions()
    results = []

    for name, entry in solutions.items():
        results.append({
            "name": name,
            "project_id": entry.get("project_id"),
            "repo_path": _display_path(entry.get("repo_path")),
            "source": "local",
        })

    if include_remote:
        remote = _discover_github_solutions()
        local_names = {r["name"] for r in results}
        for repo in remote:
            if repo["name"] not in local_names:
                results.append(repo)

    return results


def _discover_github_solutions() -> list[dict]:
    """Find GitHub repos with the gapp-solution topic."""
    try:
        result = subprocess.run(
            ["gh", "repo", "list", "--topic", "gapp-solution", "--json", "name,url"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []

        repos = json.loads(result.stdout)
        return [
            {
                "name": repo["name"],
                "project_id": None,
                "repo_path": None,
                "url": repo["url"],
                "source": "github",
            }
            for repo in repos
        ]
    except (FileNotFoundError, json.JSONDecodeError):
        return []
