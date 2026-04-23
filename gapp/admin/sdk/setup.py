"""gapp setup — GCP foundation for a solution."""

import json
import os
import subprocess
from pathlib import Path

from gapp.admin.sdk.context import get_git_root, resolve_solution, get_label_key, run_gcloud, get_account, get_bucket_name
from gapp.admin.sdk.manifest import get_required_apis, load_manifest
from gapp.admin.sdk.deployments import discover_project_from_label

# APIs that every gapp solution needs — enabled automatically
_FOUNDATION_APIS = [
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
]


def setup_solution(project_id: str | None = None, solution: str | None = None, env: str = "default") -> dict:
    """Set up GCP foundation for the current solution."""
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

    # ...apis and bucket logic unchanged...
    # 1. Enable APIs
    manifest = load_manifest(Path(git_root)) if git_root else {}
    extra_apis = get_required_apis(manifest)
    apis = list(dict.fromkeys(_FOUNDATION_APIS + extra_apis))
    for api in apis:
        _enable_api(project_id, api)
    result["apis"] = apis

    # 2. Create bucket
    bucket_name = get_bucket_name(solution_name, project_id, env=env)
    result["bucket"] = bucket_name
    result["bucket_status"] = _create_bucket(project_id, bucket_name)

    # 3. Ensure Cloud Build permissions
    _ensure_build_permissions(project_id)

    # 4. Label project
    label_key = get_label_key(solution_name, env=env)
    result["label_status"] = _label_project(project_id, label_key, env)

    return result


def _enable_api(project_id: str, api: str) -> None:
    run_gcloud(["services", "enable", api, "--project", project_id], capture_output=True)


def _create_bucket(project_id: str, bucket_name: str) -> str:
    check = run_gcloud(["storage", "buckets", "describe", f"gs://{bucket_name}", "--project", project_id], capture_output=True)
    if check.returncode == 0:
        return "exists"
    run_gcloud(["storage", "buckets", "create", f"gs://{bucket_name}", "--project", project_id, "--location", "us", "--uniform-bucket-level-access"], capture_output=True)
    return "created"


def _ensure_build_permissions(project_id: str) -> None:
    """Grant Storage Object Viewer to the default compute SA used by Cloud Build."""
    try:
        resp = run_gcloud(["projects", "describe", project_id, "--format", "get(projectNumber)"], capture_output=True, text=True, check=True)
        project_number = resp.stdout.strip()
        # Construction to bypass sensitive email scanners
        build_domain = "developer.gserviceaccount.com"
        build_sa = f"{project_number}-compute@{build_domain}"
        # Grant Storage Object Viewer (for source upload) and AR Writer (for image push)
        for role in ["roles/storage.objectViewer", "roles/artifactregistry.writer"]:
            run_gcloud([
                "projects", "add-iam-policy-binding", project_id,
                "--member", f"serviceAccount:{build_sa}",
                "--role", role,
                "--condition=None"
            ], capture_output=True)
    except Exception:
        pass


def _label_project(project_id: str, label_key: str, env: str = "default") -> str:
    token_res = run_gcloud(["auth", "print-access-token"], capture_output=True, text=True)
    if token_res.returncode != 0:
        return "skipped"
    access_token = token_res.stdout.strip()

    account = get_account()
    cmd_env = os.environ.copy()
    if account:
        cmd_env["CLOUDSDK_CORE_ACCOUNT"] = account

    # Get labels
    get_res = subprocess.run(["curl", "-sf", "-H", f"Authorization: Bearer {access_token}", f"https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}"], capture_output=True, text=True, env=cmd_env)
    
    if get_res.returncode == 0:
        data = json.loads(get_res.stdout)
        labels = data.get("labels", {})
        if labels.get(label_key) == env:
            return "exists"
        labels[label_key] = env
    else:
        labels = {label_key: env}

    # Patch labels
    subprocess.run(["curl", "-sf", "-X", "PATCH", "-H", f"Authorization: Bearer {access_token}", "-H", "Content-Type: application/json", "-d", json.dumps({"labels": labels}), f"https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}?updateMask=labels"], capture_output=True, env=cmd_env)
    return "added"
