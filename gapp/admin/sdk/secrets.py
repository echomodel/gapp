"""gapp secret management — store secrets in Secret Manager."""

import subprocess
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
from gapp.admin.sdk.manifest import get_env_vars, get_prerequisite_secrets, load_manifest, save_manifest


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


def set_secret(env_var_name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in Secret Manager by its env var name.

    Resolves the env var name (e.g. SIGNING_KEY) to the Secret Manager
    secret ID via gapp.yaml, creates the secret if needed, then adds
    a new version with the given value.

    Returns dict with: env_var, secret_id, solution, project_id, secret_status.
    """
    resolved = resolve_secret_name(env_var_name, solution=solution)
    project_id = resolved["project_id"]
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    secret_id = resolved["secret_id"]
    secret_status = _ensure_secret(project_id, secret_id)
    _add_secret_version(project_id, secret_id, value)

    return {
        "env_var": resolved["env_var"],
        "secret_id": secret_id,
        "solution": resolved["solution"],
        "project_id": project_id,
        "secret_status": secret_status,
    }


def list_secrets(solution: str | None = None) -> dict:
    """List secret-backed env vars and their status in Secret Manager.

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
    env_entries = get_env_vars(manifest)

    secrets = []
    for entry in env_entries:
        secret_cfg = entry.get("secret")
        if not secret_cfg:
            continue

        env_var = entry["name"]
        if isinstance(secret_cfg, dict):
            short_name = secret_cfg.get("name", env_var.lower().replace("_", "-"))
            generate = secret_cfg.get("generate", False)
        else:
            short_name = env_var.lower().replace("_", "-")
            generate = False

        secret_id = f"{ctx['name']}-{short_name}"
        status = "not set"
        if project_id:
            status = _check_secret_status(project_id, secret_id)
            # Fall back to unprefixed name for backwards compat
            if status == "not created":
                unprefixed_status = _check_secret_status(project_id, short_name)
                if unprefixed_status != "not created":
                    status = unprefixed_status
                    secret_id = short_name

        secrets.append({
            "env_var": env_var,
            "secret_id": secret_id,
            "generate": generate,
            "status": status,
        })

    return {
        "solution": ctx["name"],
        "project_id": project_id,
        "secrets": secrets,
    }


def resolve_secret_name(env_var_name: str, solution: str | None = None) -> dict:
    """Resolve an env var name to its Secret Manager secret ID.

    Looks up the env var in gapp.yaml's env section, reads the secret.name
    field, and prefixes with the solution name to produce the full Secret
    Manager ID: {solution}-{secret.name}.

    Returns dict with: env_var, short_name, secret_id, solution, generate.
    Raises RuntimeError if the env var isn't found or isn't secret-backed.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    repo_path = ctx.get("repo_path")
    if not repo_path:
        raise RuntimeError("No repo path found for this solution.")

    manifest = load_manifest(Path(repo_path).expanduser())
    env_entries = get_env_vars(manifest)

    for entry in env_entries:
        if entry["name"] == env_var_name:
            secret_cfg = entry.get("secret")
            if not secret_cfg:
                raise RuntimeError(
                    f"'{env_var_name}' is a plain env var, not secret-backed."
                )
            if isinstance(secret_cfg, dict):
                short_name = secret_cfg.get("name")
                generate = secret_cfg.get("generate", False)
            else:
                short_name = None
                generate = False

            if not short_name:
                # Fall back to legacy convention for existing deployments
                short_name = env_var_name.lower().replace("_", "-")

            secret_id = f"{ctx['name']}-{short_name}"
            return {
                "env_var": env_var_name,
                "short_name": short_name,
                "secret_id": secret_id,
                "solution": ctx["name"],
                "generate": generate,
                "project_id": ctx.get("project_id"),
            }

    raise RuntimeError(
        f"No env var '{env_var_name}' found in gapp.yaml. "
        f"Known env vars: {', '.join(e['name'] for e in env_entries) or '(none)'}"
    )


def get_secret(env_var_name: str, plaintext: bool = False, solution: str | None = None) -> dict:
    """Get a secret from Secret Manager by its env var name.

    Resolves the env var name (e.g. SIGNING_KEY) to the Secret Manager
    secret ID via gapp.yaml, then gets the latest version.

    By default returns a SHA-256 hash prefix and length — enough to
    confirm the secret exists and verify it matches without exposing
    the value. Pass plaintext=True to include the actual value.

    Returns dict with: env_var, secret_id, length, hash, solution,
    project_id. Includes 'value' only when plaintext=True.
    """
    import hashlib

    resolved = resolve_secret_name(env_var_name, solution=solution)
    project_id = resolved["project_id"]
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    secret_id = resolved["secret_id"]

    # Try the resolved name first
    value = _read_secret_version(project_id, secret_id)

    # Fall back to unprefixed name for backwards compat with existing deployments
    if value is None:
        unprefixed = resolved["short_name"]
        if unprefixed != secret_id:
            value = _read_secret_version(project_id, unprefixed)
            if value is not None:
                secret_id = unprefixed

    if value is None:
        raise RuntimeError(
            f"Secret '{resolved['secret_id']}' not found in Secret Manager "
            f"(project: {project_id}). Has 'gapp deploy' been run?"
        )

    result = {
        "name": resolved["env_var"],
        "secret_id": secret_id,
    }

    if plaintext:
        result["value"] = value
    else:
        result["hash"] = hashlib.sha256(value.encode()).hexdigest()[:16]
        result["length"] = len(value)

    return result


def _read_secret_version(project_id: str, secret_id: str) -> str | None:
    """Read the latest version of a secret. Returns None if not found."""
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         "--secret", secret_id,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


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
