"""gapp secret management — store secrets in Secret Manager."""

import subprocess

from gapp.sdk.context import resolve_solution
from gapp.sdk.manifest import get_prerequisite_secrets, load_manifest
from pathlib import Path


def set_secret(secret_name: str, value: str) -> dict:
    """Store a secret value in Secret Manager.

    Creates the secret if it doesn't exist, then adds a new version.
    Returns dict describing what was done.
    """
    ctx = resolve_solution()
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    project_id = ctx.get("project_id")
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    repo_path = ctx.get("repo_path")
    manifest = load_manifest(Path(repo_path)) if repo_path else {}
    known_secrets = get_prerequisite_secrets(manifest)

    if secret_name not in known_secrets:
        raise RuntimeError(
            f"Unknown secret '{secret_name}'. "
            f"Known secrets: {', '.join(known_secrets) or '(none)'}"
        )

    # Create secret if it doesn't exist
    secret_status = _ensure_secret(project_id, secret_name)

    # Add new version
    _add_secret_version(project_id, secret_name, value)

    return {
        "name": secret_name,
        "project_id": project_id,
        "secret_status": secret_status,
    }


def list_secrets() -> dict:
    """List prerequisite secrets and their status in Secret Manager.

    Returns dict with solution info and list of secrets with status.
    """
    ctx = resolve_solution()
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    project_id = ctx.get("project_id")
    repo_path = ctx.get("repo_path")
    manifest = load_manifest(Path(repo_path)) if repo_path else {}
    known_secrets = get_prerequisite_secrets(manifest)

    secrets = []
    for name, info in known_secrets.items():
        status = "not set"
        if project_id:
            status = _check_secret_status(project_id, name)
        secrets.append({
            "name": name,
            "description": info.get("description", ""),
            "status": status,
        })

    return {
        "name": ctx["name"],
        "project_id": project_id,
        "secrets": secrets,
    }


def _ensure_secret(project_id: str, secret_name: str) -> str:
    """Create a Secret Manager secret if it doesn't exist."""
    check = subprocess.run(
        ["gcloud", "secrets", "describe", secret_name,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    result = subprocess.run(
        ["gcloud", "secrets", "create", secret_name,
         "--replication-policy", "automatic",
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create secret: {result.stderr.strip()}")
    return "created"


def _add_secret_version(project_id: str, secret_name: str, value: str) -> None:
    """Add a new version to a secret."""
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "add", secret_name,
         "--data-file=-",
         "--project", project_id],
        input=value,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to set secret value: {result.stderr.strip()}")


def _check_secret_status(project_id: str, secret_name: str) -> str:
    """Check if a secret exists and has a version."""
    check = subprocess.run(
        ["gcloud", "secrets", "describe", secret_name,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return "not created"

    # Check if it has any versions
    versions = subprocess.run(
        ["gcloud", "secrets", "versions", "list", secret_name,
         "--project", project_id,
         "--limit", "1",
         "--format", "value(name)"],
        capture_output=True,
        text=True,
    )
    if versions.returncode == 0 and versions.stdout.strip():
        return "set"
    return "empty"
