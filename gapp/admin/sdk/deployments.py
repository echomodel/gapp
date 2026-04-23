"""gapp deployments — discover GCP projects with gapp solutions."""

import json
from gapp.admin.sdk.context import get_label_key, run_gcloud


def list_deployments(wide: bool = False) -> dict:
    """List GCP projects that have gapp solution labels."""
    projects = _find_gapp_projects(wide=wide)

    # Sort by number of solutions descending
    projects.sort(key=lambda p: len(p["solutions"]), reverse=True)

    default = projects[0]["id"] if projects else None

    return {
        "default": default,
        "projects": projects,
    }


def _find_gapp_projects(wide: bool = False) -> list[dict]:
    """Find GCP projects scoped by owner (if set)."""
    from gapp.admin.sdk.context import get_owner
    owner = get_owner()
    
    # Prefix for identifying solutions
    # If wide=True, we search for ALL 'gapp-' labels.
    # If wide=False and owner is set, we search only for 'gapp-<owner>-' labels.
    if not wide and owner:
        label_prefix = f"gapp-{owner}-"
    else:
        label_prefix = "gapp-"

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
            if key.startswith(label_prefix):
                # Robust extraction: strip the prefix to get the name
                name = key[len(label_prefix):]
                if not name:
                    continue # Should not happen if labels correct
                
                solutions.append({
                    "name": name,
                    "instance": value,
                    "label": key,
                })
            elif wide and key.startswith("gapp-"):
                # Even if it doesn't match our active owner prefix, 
                # include it if we are in wide mode.
                name = key[len("gapp-"):]
                solutions.append({
                    "name": name,
                    "instance": value,
                    "label": key,
                })

        if solutions:
            # Deduplicate (might overlap between wide match and specific match)
            unique_solutions = {s["label"]: s for s in solutions}.values()
            solutions_list = sorted(list(unique_solutions), key=lambda s: s["name"])
            
            gapp_projects.append({
                "id": project["projectId"],
                "solutions": solutions_list,
            })

    return gapp_projects


def discover_project_from_label(solution_name: str, env: str = "default") -> str | None:
    """Find a GCP project with the gapp-<owner>-<app> label matching env."""
    from gapp.admin.sdk.context import get_label_key
    
    # 1. Try current/configured label
    label_key = get_label_key(solution_name, env=env)
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
