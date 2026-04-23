"""Solution listing and discovery."""

import subprocess
from pathlib import Path


def _display_path(path: str | None) -> str | None:
    """Shorten home path for display."""
    if not path:
        return None
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def list_solutions(include_remote: bool = False) -> list[dict]:
    """List known solutions from GCP deployments."""
    from gapp.admin.sdk.deployments import list_deployments
    
    deployments = list_deployments()
    results = []

    for project in deployments.get("projects", []):
        for sol in project.get("solutions", []):
            results.append({
                "name": sol["name"],
                "project_id": project["id"],
                "source": "gcp",
                "label": sol.get("label"),
            })

    return results
