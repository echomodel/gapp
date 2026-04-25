"""gapp deployments — discover GCP projects with gapp solutions."""

import json
from typing import Optional, List, Dict
from gapp.admin.sdk.cloud import get_provider


def list_deployments(wide: bool = False, project_limit: int = 50, provider = None) -> dict:
    """List GCP projects that have gapp solution labels."""
    provider = provider or get_provider()
    
    from gapp.admin.sdk.context import get_owner
    owner = get_owner()
    
    # Prefix for identifying solutions server-side
    # We check for both new format (gapp_) and legacy (gapp-)
    if not wide and owner:
        # Optimized for owner namespace
        label_filter = f"labels.keys:gapp_{owner}_*"
    else:
        # Catch all formats
        label_filter = "labels.keys:gapp-*,labels.keys:gapp_*"

    projects_data = provider.list_projects(filter_query=label_filter, limit=project_limit)
    
    gapp_projects = []
    total_solutions = 0
    
    is_global_namespace = not wide and not owner
    
    for project in projects_data:
        labels = project.get("labels", {})
        solutions = []
        
        for key, value in labels.items():
            if not key.startswith("gapp"):
                continue
                
            # 1. New Underscore Format (gapp_<owner>_<name>)
            if key.startswith("gapp_"):
                parts = key.split("_")
                # Parts: [gapp, owner_or_empty, name]
                
                label_owner = parts[1] if parts[1] else None
                label_name = "_".join(parts[2:]) # Handle names with underscores
                
                if is_global_namespace:
                    if label_owner is None:
                        name = label_name
                    else:
                        continue
                elif not wide and owner:
                    if label_owner == owner:
                        name = label_name
                    else:
                        continue
                else:
                    name = label_name

            # 2. Legacy Hyphen Format (gapp-<name>)
            elif key.startswith("gapp-"):
                if not is_global_namespace and not wide:
                    continue # Legacy is always global
                name = key[len("gapp-"):]
            
            else:
                continue

            solutions.append({
                "name": name.replace("--", "-"), # Reverse the dash protection
                "instance": value,
                "label": key,
            })
            total_solutions += 1

        if solutions:
            gapp_projects.append({
                "id": project["projectId"],
                "solutions": sorted(solutions, key=lambda s: s["name"]),
            })

    return {
        "projects": gapp_projects,
        "total_projects": len(gapp_projects),
        "total_solutions": total_solutions,
        "limit_reached": len(projects_data) >= project_limit,
        "filter_mode": "all" if wide else (f"owner:{owner}" if owner else "global"),
    }


def discover_project_from_label(solution_name: str, env: str = "default", provider = None) -> Optional[str]:
    """Find a GCP project with the gapp_<owner>_<app> label matching env."""
    provider = provider or get_provider()
    from gapp.admin.sdk.context import get_label_key, get_label_value
    
    # 1. Try current/configured label (Underscore format)
    label_key = get_label_key(solution_name)
    label_value = get_label_value(env)
    
    filter_query = f"labels.{label_key}={label_value}"
    projects = provider.list_projects(filter_query=filter_query, limit=1)
    if projects:
        return projects[0]["projectId"]

    # 2. Try legacy fallback (Hyphen format, value always 'default' or provided env)
    legacy_key = f"gapp-{solution_name}".replace("_", "-").lower()
    if legacy_key != label_key:
        filter_query = f"labels.{legacy_key}={env}"
        projects = provider.list_projects(filter_query=filter_query, limit=1)
        if projects:
            return projects[0]["projectId"]
        
    return None
