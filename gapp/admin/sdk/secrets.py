"""gapp secret management — store secrets in Secret Manager."""

import subprocess
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
from gapp.admin.sdk.manifest import get_prerequisite_secrets, load_manifest, save_manifest


def add_secret(secret_name: str, description: str, value: str | None = None, solution: str | None = None) -> dict:
    """Add a secret declaration to gapp.yaml and optionally set its value.

    Returns dict describing what was done.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    repo_path = ctx.get("repo_path")
    if not repo_path:
        raise RuntimeError("No repo path found for this solution.")

    repo_path = Path(repo_path)
    manifest = load_manifest(repo_path)

    # Add to manifest
    if "prerequisites" not in manifest:
        manifest["prerequisites"] = {}
    if "secrets" not in manifest["prerequisites"]:
        manifest["prerequisites"]["secrets"] = {}

    already_declared = secret_name in manifest["prerequisites"]["secrets"]
    manifest["prerequisites"]["secrets"][secret_name] = {"description": description}
    save_manifest(repo_path, manifest)

    result = {
        "name": secret_name,
        "manifest_status": "exists" if already_declared else "added",
        "value_status": None,
    }

    # Optionally set the value
    if value is not None:
        project_id = ctx.get("project_id")
        if not project_id:
            result["value_status"] = "skipped (no project attached)"
        else:
            _ensure_secret(project_id, secret_name)
            _add_secret_version(project_id, secret_name, value)
            result["value_status"] = "set"

    return result


def remove_secret(secret_name: str, solution: str | None = None) -> dict:
    """Remove a secret declaration from gapp.yaml.

    Does NOT delete the secret from Secret Manager.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    repo_path = ctx.get("repo_path")
    if not repo_path:
        raise RuntimeError("No repo path found for this solution.")

    repo_path = Path(repo_path)
    manifest = load_manifest(repo_path)
    secrets = manifest.get("prerequisites", {}).get("secrets", {})

    if secret_name not in secrets:
        raise RuntimeError(f"Secret '{secret_name}' not found in gapp.yaml.")

    del manifest["prerequisites"]["secrets"][secret_name]
    # Clean up empty sections
    if not manifest["prerequisites"]["secrets"]:
        del manifest["prerequisites"]["secrets"]
    if not manifest["prerequisites"]:
        del manifest["prerequisites"]
    save_manifest(repo_path, manifest)

    return {"name": secret_name, "status": "removed"}


def set_secret(secret_name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in Secret Manager.

    Creates the secret if it doesn't exist, then adds a new version.
    Returns dict describing what was done.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    project_id = ctx.get("project_id")
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    repo_path = ctx.get("repo_path")
    manifest = load_manifest(Path(repo_path).expanduser()) if repo_path else {}
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


def list_secrets(solution: str | None = None) -> dict:
    """List prerequisite secrets and their status in Secret Manager.

    Returns dict with solution info and list of secrets with status.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    project_id = ctx.get("project_id")
    repo_path = ctx.get("repo_path")
    manifest = load_manifest(Path(repo_path).expanduser()) if repo_path else {}
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
