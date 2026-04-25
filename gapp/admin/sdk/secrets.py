"""gapp secret management — store secrets in Secret Manager.

Every secret gapp creates or updates is stamped with the label
`gapp-solution=<solution-name>` so ownership is machine-readable.
Listing and pre-deploy validation use a single label-filtered
`gcloud secrets list` call instead of N per-secret describes.
See issue #27 for the full design rationale.
"""

import subprocess
from pathlib import Path

from gapp.admin.sdk.manifest import get_env_vars, load_manifest, save_manifest


def _resolve_solution(name: str | None = None) -> dict | None:
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().resolve_solution(name)


GAPP_SOLUTION_LABEL = "gapp-solution"


def add_secret(secret_name: str, description: str, value: str | None = None, solution: str | None = None) -> dict:
    """Add a secret declaration to gapp.yaml and optionally set its value."""
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

    if value is not None:
        project_id = ctx.get("project_id")
        if not project_id:
            result["value_status"] = "skipped (no project attached)"
        else:
            _ensure_secret(project_id, secret_name, ctx["name"])
            _add_secret_version(project_id, secret_name, value)
            result["value_status"] = "set"

    return result


def remove_secret(secret_name: str, solution: str | None = None) -> dict:
    """Remove a secret declaration from gapp.yaml. Does NOT delete from Secret Manager."""
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
    if not manifest["prerequisites"]["secrets"]:
        del manifest["prerequisites"]["secrets"]
    if not manifest["prerequisites"]:
        del manifest["prerequisites"]
    save_manifest(repo_path, manifest)

    return {"name": secret_name, "status": "removed"}


def set_secret(name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in Secret Manager, stamping the solution label.

    Returns dict with: name, secret_id, project_id, secret_status.
    """
    resolved = _find_secret(name, solution=solution)
    project_id = resolved["project_id"]
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    secret_id = resolved["secret_id"]
    solution_name = resolved["solution"]
    secret_status = _ensure_secret(project_id, secret_id, solution_name)
    _add_secret_version(project_id, secret_id, value)

    return {
        "name": name,
        "secret_id": secret_id,
        "project_id": project_id,
        "secret_status": secret_status,
    }


def list_secrets(solution: str | None = None) -> dict:
    """List secret-backed env vars and diff them against what exists in GCP.

    Uses a single label-filtered `gcloud secrets list` call to enumerate
    secrets owned by this solution, then diffs against what gapp.yaml
    declares. Reports each as:
        ready     — declared and present in GCP with our label
        missing   — declared but not present in GCP (must be set or generated)
        orphan    — present in GCP with our label but not declared in gapp.yaml
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

    present_ids = set()
    if project_id:
        present_ids = {s["id"] for s in list_secrets_by_label(project_id, ctx["name"])}

    secrets = []
    declared_ids = set()
    for entry in env_entries:
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict):
            continue
        secret_name = secret_cfg["name"]  # required by schema
        secret_id = f"{ctx['name']}-{secret_name}"
        declared_ids.add(secret_id)
        generate = secret_cfg.get("generate", False)

        if not project_id:
            status = "no project attached"
        elif secret_id in present_ids:
            status = "ready"
        elif generate:
            status = "missing (will be generated on deploy)"
        else:
            status = "missing (run `gapp secrets set`)"

        secrets.append({
            "name": secret_name,
            "env_var": entry["name"],
            "secret_id": secret_id,
            "generate": generate,
            "status": status,
        })

    orphans = sorted(present_ids - declared_ids)

    return {
        "solution": ctx["name"],
        "project_id": project_id,
        "secrets": secrets,
        "orphans": orphans,
    }


def list_secrets_by_label(project_id: str, solution_name: str) -> list[dict]:
    """Query Secret Manager for every secret labeled with this solution.

    Returns [{"id": "<secret-id>", "labels": {...}}]. On API failure,
    returns [] and logs a warning — the caller decides whether that's
    load-bearing.
    """
    filter_expr = f"labels.{GAPP_SOLUTION_LABEL}={solution_name}"
    result = subprocess.run(
        ["gcloud", "secrets", "list",
         "--project", project_id,
         "--filter", filter_expr,
         "--format", "value(name)"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        import logging
        logging.warning("Failed to list labeled secrets: %s", result.stderr.strip())
        return []

    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return [{"id": sid, "labels": {GAPP_SOLUTION_LABEL: solution_name}} for sid in ids]


def validate_declared_secrets(project_id: str, solution_name: str, manifest: dict) -> None:
    """Fast-fail before deploy if non-generate declared secrets are missing.

    Uses one label-filtered query to get the present set, then diffs against
    what gapp.yaml declares. Closes #24 using the same label query as
    `list_secrets`.
    """
    present_ids = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}

    missing = []
    for entry in get_env_vars(manifest):
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict):
            continue
        if secret_cfg.get("generate"):
            continue
        secret_name = secret_cfg["name"]
        secret_id = f"{solution_name}-{secret_name}"
        if secret_id not in present_ids:
            missing.append({"name": secret_name, "env_var": entry["name"], "secret_id": secret_id})

    if missing:
        lines = [
            f"{len(missing)} secret(s) declared in gapp.yaml are missing in GCP:",
        ]
        for m in missing:
            lines.append(f"  {m['env_var']} → {m['secret_id']}")
            lines.append(f"    Set it: gapp secrets set {m['name']} <value>")
        raise RuntimeError("\n".join(lines))


def _find_secret(name: str, solution: str | None = None) -> dict:
    """Look up a secret by its short name as declared in gapp.yaml."""
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

    known = []
    for entry in env_entries:
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict):
            continue
        secret_name = secret_cfg["name"]
        known.append(secret_name)

        if secret_name == name:
            return {
                "name": name,
                "env_var": entry["name"],
                "secret_id": f"{ctx['name']}-{name}",
                "solution": ctx["name"],
                "generate": secret_cfg.get("generate", False),
                "project_id": ctx.get("project_id"),
            }

    raise RuntimeError(
        f"No secret '{name}' found in gapp.yaml. "
        f"Known secrets: {', '.join(known) or '(none)'}"
    )


def get_secret(name: str, plaintext: bool = False, solution: str | None = None) -> dict:
    """Get a secret from Secret Manager by its short name."""
    import hashlib

    resolved = _find_secret(name, solution=solution)
    project_id = resolved["project_id"]
    if not project_id:
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    secret_id = resolved["secret_id"]
    value = _read_secret_version(project_id, secret_id)

    if value is None:
        raise RuntimeError(
            f"Secret '{secret_id}' not found in Secret Manager "
            f"(project: {project_id}). Has 'gapp deploy' been run?"
        )

    result = {"name": name, "secret_id": secret_id}

    if plaintext:
        result["value"] = value
    else:
        result["hash"] = hashlib.sha256(value.encode()).hexdigest()[:16]
        result["length"] = len(value)

    return result


def _read_secret_version(project_id: str, secret_id: str) -> str | None:
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


def _ensure_secret(project_id: str, secret_id: str, solution_name: str) -> str:
    """Create a Secret Manager secret if absent, stamping the solution label.

    Returns "created" or "exists".

    If a secret with the target ID already exists but is NOT labeled
    `gapp-solution=<solution_name>`, this raises. gapp refuses to
    implicitly take over pre-existing secrets: every secret gapp
    manages is labeled, so an unlabeled or differently-labeled secret
    at this ID means something else put it there. The caller is
    expected to investigate and decide manually.
    """
    from gapp import __version__

    describe = subprocess.run(
        ["gcloud", "secrets", "describe", secret_id,
         "--project", project_id,
         "--format", f"value(labels.{GAPP_SOLUTION_LABEL})"],
        capture_output=True, text=True,
    )
    if describe.returncode == 0:
        owner = describe.stdout.strip()
        if owner == solution_name:
            return "exists"
        why = f"owned by solution '{owner}'" if owner else "has no gapp-solution label"
        raise RuntimeError(
            f"Secret '{secret_id}' already exists in project '{project_id}' and {why}.\n"
            f"gapp v{__version__} labels every secret it manages with "
            f"`gapp-solution=<solution>`. For security, pre-existing secrets "
            f"are never implicitly taken over — they must be investigated manually.\n"
            f"  Investigate: gcloud secrets describe {secret_id} --project {project_id}\n"
            f"  If no longer in use, delete so gapp can reclaim the name:\n"
            f"    gcloud secrets delete {secret_id} --project {project_id}"
        )

    label_arg = f"{GAPP_SOLUTION_LABEL}={solution_name}"
    result = subprocess.run(
        ["gcloud", "secrets", "create", secret_id,
         "--replication-policy", "automatic",
         "--labels", label_arg,
         "--project", project_id],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create secret: {result.stderr.strip()}")
    return "created"


def _add_secret_version(project_id: str, secret_id: str, value: str) -> None:
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "add", secret_id,
         "--data-file=-",
         "--project", project_id],
        input=value,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to set secret value: {result.stderr.strip()}")
