"""gapp deployments — discover GCP projects with gapp solutions."""

import json
import subprocess


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
        result = subprocess.run(
            ["gcloud", "projects", "list",
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
                solution_name = key[len("gapp-"):]
                solutions.append({
                    "name": solution_name,
                    "instance": value,
                })

        if solutions:
            solutions.sort(key=lambda s: s["name"])
            gapp_projects.append({
                "id": project["projectId"],
                "solutions": solutions,
            })

    return gapp_projects


def discover_project_from_label(solution_name: str) -> str | None:
    """Find a GCP project with the gapp-{name} label."""
    label_filter = f"labels.gapp-{solution_name}=default"
    try:
        result = subprocess.run(
            ["gcloud", "projects", "list",
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
