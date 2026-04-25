"""gapp setup — GCP foundation for a solution."""

import os
from pathlib import Path
from typing import Optional

from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.context import get_git_root, resolve_solution, get_label_key, get_bucket_name
from gapp.admin.sdk.manifest import get_required_apis, load_manifest
from gapp.admin.sdk.deployments import discover_project_from_label

# APIs that every gapp solution needs — enabled automatically
_FOUNDATION_APIS = [
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
]


def setup_solution(
    project_id: Optional[str] = None, 
    solution: Optional[str] = None, 
    env: str = "default",
    provider = None
) -> dict:
    """Set up GCP foundation for the current solution."""
    provider = provider or get_provider()
    
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError("Not inside a gapp solution.")

    solution_name = ctx["name"]
    git_root = ctx.get("repo_path")

    if not project_id:
        project_id = ctx.get("project_id")
    if not project_id:
        project_id = discover_project_from_label(solution_name, env=env)
    if not project_id:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError("No GCP project specified.")

    result = {
        "name": solution_name,
        "project_id": project_id,
        "env": env,
        "apis": [],
        "bucket": None,
        "bucket_status": None,
        "label_status": None,
    }

    # 1. Enable APIs
    manifest = load_manifest(Path(git_root)) if git_root else {}
    extra_apis = get_required_apis(manifest)
    apis = list(dict.fromkeys(_FOUNDATION_APIS + extra_apis))
    for api in apis:
        provider.enable_api(project_id, api)
    result["apis"] = apis

    # 2. Create bucket
    bucket_name = get_bucket_name(solution_name, project_id, env=env)
    result["bucket"] = bucket_name
    if provider.bucket_exists(project_id, bucket_name):
        result["bucket_status"] = "exists"
    else:
        provider.create_bucket(project_id, bucket_name)
        result["bucket_status"] = "created"

    # 3. Ensure Cloud Build permissions
    provider.ensure_build_permissions(project_id)

    # 4. Label project
    label_key = get_label_key(solution_name, env=env)
    
    current_labels = provider.get_project_labels(project_id)
    if current_labels.get(label_key) == env:
        result["label_status"] = "exists"
    else:
        current_labels[label_key] = env
        provider.set_project_labels(project_id, current_labels)
        result["label_status"] = "added"

    return result
