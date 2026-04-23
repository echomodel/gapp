"""gapp deployments — discover GCP projects with gapp solutions."""

import json
from gapp.admin.sdk.context import get_label_key, run_gcloud


def list_deployments() -> dict:
    """List all GCP projects that have gapp solution labels.

    Queries GCP for projects accessible to the current user, filters for
    those with gapp-* labels, and returns a structured result with the
    default project (most solutions) highlighted.

    Returns dict with keys: default (project id), projects (list).
    """
    projects = _find_gapp_projects()

    # Sort by number of solutions descending
    projects.sort(key=lambda p: len(p["solutions"]), reverse=True)

    default = projects[0]["id"] if projects else None

    return {
        "default": default,
        "projects": projects,
    }


def _find_gapp_projects() -> list[dict]:
    """Find GCP projects with gapp-* labels."""
    try:
        result = run_gcloud(
            ["projects", "list",
             "--format", "json(projectId,labels)"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []

        all_projects = json.loads(result.stdout)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    gapp_projects = []
    for project in all_projects:
        labels = project.get("labels", {})
        if not labels:
            continue

        solutions = []
        for key, value in labels.items():
            if key.startswith("gapp-"):
                # Handle gapp-<owner>-<name> or legacy gapp-<name>
                parts = key.split("-")
                if len(parts) > 2:
                    # Scoped: gapp-<owner>-<name>
                    name = "-".join(parts[2:])
                else:
                    # Legacy: gapp-<name>
                    name = parts[1]
                
                solutions.append({
                    "name": name,
                    "instance": value,
                    "label": key,
                })

        if solutions:
            solutions.sort(key=lambda s: s["name"])
            gapp_projects.append({
                "id": project["projectId"],
                "solutions": solutions,
            })

    return gapp_projects


def discover_project_from_label(solution_name: str, env: str = "default") -> str | None:
    """Find a GCP project with the gapp-<owner>-<app> label matching env."""
    from gapp.admin.sdk.context import get_label_key
    
    # 1. Try current/configured label
    label_key = get_label_key(solution_name)
    project = _query_project_by_label(label_key, env=env)
    if project:
        return project

    # 2. Try legacy fallback
    legacy_key = f"gapp-{solution_name}".replace("_", "-").lower()
    if legacy_key != label_key:
        return _query_project_by_label(legacy_key, env=env)
        
    return None

def _query_project_by_label(label_key: str, env: str = "default") -> str | None:
    """Helper to query gcloud for a specific label key=env."""
    label_filter = f"labels.{label_key}={env}"
    try:
        result = run_gcloud(
            ["projects", "list",
             "--filter", label_filter,
             "--format", "value(projectId)"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except FileNotFoundError:
        pass
    return None
