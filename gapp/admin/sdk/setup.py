"""gapp setup — GCP foundation for a solution."""

import json
import os
import subprocess

from gapp.admin.sdk.config import load_solutions, save_solutions
from gapp.admin.sdk.context import get_git_root, resolve_solution
from gapp.admin.sdk.manifest import get_required_apis, load_manifest

# APIs that every gapp solution needs — enabled automatically
_FOUNDATION_APIS = [
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
]


def setup_solution(project_id: str | None = None, solution: str | None = None) -> dict:
    """Set up GCP foundation for the current solution.

    Steps (all idempotent):
    1. Resolve solution context
    2. Resolve project ID (explicit arg → local cache → GCP label → error)
    3. Enable required APIs
    4. Create per-solution GCS bucket
    5. Label GCP project
    6. Save project_id to solutions.yaml

    Returns dict describing what was done.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    solution_name = ctx["name"]
    git_root = ctx.get("repo_path")

    # Resolve project ID: explicit arg → local config → GCP labels → env var
    if not project_id:
        project_id = ctx.get("project_id")
    if not project_id:
        project_id = _discover_project_from_label(solution_name)
    if not project_id:
        # GOOGLE_CLOUD_PROJECT is a standard GCP env var set by gcloud, Cloud Run,
        # Cloud Shell, CI runners with WIF auth, and any GCP-aware environment.
        # Not GitHub-specific — works in any context where gcloud is configured.
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise RuntimeError(
            "No GCP project specified and none found via labels.\n"
            "  Run: gapp setup <project-id>"
        )

    result = {
        "name": solution_name,
        "project_id": project_id,
        "apis": [],
        "bucket": None,
        "bucket_status": None,
        "label_status": None,
    }

    # Enable APIs (foundation + any solution-specific extras)
    from pathlib import Path
    git_root = Path(git_root) if git_root else None
    manifest = load_manifest(git_root) if git_root else {}
    extra_apis = get_required_apis(manifest)
    apis = list(dict.fromkeys(_FOUNDATION_APIS + extra_apis))  # deduplicate, preserve order
    for api in apis:
        _enable_api(project_id, api)
    result["apis"] = apis

    # Create per-solution bucket
    bucket_name = f"gapp-{solution_name}-{project_id}"
    result["bucket"] = bucket_name
    result["bucket_status"] = _create_bucket(project_id, bucket_name)

    # Label project
    result["label_status"] = _label_project(project_id, solution_name)

    # Save to local cache
    solutions = load_solutions()
    if solution_name not in solutions:
        solutions[solution_name] = {}
    solutions[solution_name]["project_id"] = project_id
    if git_root:
        solutions[solution_name]["repo_path"] = str(git_root)
    save_solutions(solutions)

    return result


def _discover_project_from_label(solution_name: str) -> str | None:
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
            # Return first match
            return result.stdout.strip().splitlines()[0]
    except FileNotFoundError:
        pass
    return None


def _enable_api(project_id: str, api: str) -> None:
    """Enable a GCP API on the project. Idempotent, tolerant of permission errors."""
    result = subprocess.run(
        ["gcloud", "services", "enable", api, "--project", project_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Check if already enabled (common in CI where SA lacks serviceusage perms)
        check = subprocess.run(
            ["gcloud", "services", "list", "--project", project_id,
             "--filter", f"config.name={api}", "--format", "value(config.name)"],
            capture_output=True, text=True,
        )
        if check.returncode == 0 and api in check.stdout:
            return  # Already enabled, safe to continue
        raise RuntimeError(f"Failed to enable API {api}: {result.stderr.strip()}")


def _create_bucket(project_id: str, bucket_name: str) -> str:
    """Create a GCS bucket if it doesn't exist. Returns status."""
    # Check if bucket exists
    check = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}",
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create bucket
    result = subprocess.run(
        ["gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
         "--project", project_id,
         "--location", "us",
         "--uniform-bucket-level-access"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create bucket: {result.stderr.strip()}")
    return "created"


def _label_project(project_id: str, solution_name: str) -> str:
    """Add gapp-{name}=default label to the project. Idempotent."""
    label_key = f"gapp-{solution_name}"

    # Check current labels
    check = subprocess.run(
        ["gcloud", "alpha", "projects", "describe", project_id,
         "--format", "json(labels)"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        data = json.loads(check.stdout) or {}
        labels = data.get("labels") or {}
        if labels.get(label_key) == "default":
            return "exists"

    # Add label
    result = subprocess.run(
        ["gcloud", "alpha", "projects", "update", project_id,
         "--update-labels", f"{label_key}=default"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to label project: {result.stderr.strip()}")
    return "added"
