"""Solution listing and discovery."""

from pathlib import Path


def _display_path(path: str | None) -> str | None:
    """Shorten home path for display."""
    if not path:
        return None
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def list_solutions(include_remote: bool = False, wide: bool = False, project_limit: int = 50) -> dict:
    """List known solutions from GCP deployments."""
    from gapp.admin.sdk.deployments import list_deployments
    
    results = list_deployments(wide=wide, project_limit=project_limit)
    
    solutions = []
    for project in results.get("projects", []):
        for sol in project.get("solutions", []):
            solutions.append({
                "name": sol["name"],
                "project_id": project["id"],
                "source": "gcp",
                "label": sol.get("label"),
            })

    return {
        "solutions": solutions,
        "total_projects": results["total_projects"],
        "total_solutions": results["total_solutions"],
        "limit_reached": results["limit_reached"],
        "filter_mode": results["filter_mode"],
    }
