"""gapp secret management — store secrets in Secret Manager.

Every secret gapp creates or updates is stamped with the label
`gapp-solution=<solution-name>` so ownership is machine-readable.
Listing and pre-deploy validation use a single label-filtered
`gcloud secrets list` call instead of N per-secret describes.
See issue #27 for the full design rationale.
"""

import subprocess
from pathlib import Path

from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.manifest import get_env_vars, load_manifest, save_manifest

GAPP_SOLUTION_LABEL = "gapp-solution"


def add_secret(secret_name: str, description: str, value: str | None = None, solution: str | None = None) -> dict:
    """Add a secret declaration to gapp.yaml and optionally set its value."""
    ctx = GappSDK().resolve_solution(solution)
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
    ctx = GappSDK().resolve_solution(solution)
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

    First does a single label-filtered `gcloud secrets list` to enumerate
    secrets gapp manages for this solution. For each declared secret that
    didn't appear in the labeled set, follows up with one `gcloud secrets
    describe` to distinguish:
        ready       — declared and present, labeled for this solution
        missing     — declared but no secret with that ID exists in GCP
        unattached  — secret exists at the expected ID but has no gapp label
        conflict    — secret exists labeled for a different solution
        orphan      — present in GCP with our label but not in gapp.yaml

    Each non-ready scenario produces a structured hint with concrete
    `gcloud` commands the operator can run to resolve. Hints are surfaced
    in the top-level `hints` array; CLI renderers can emit them as
    footnotes after the table.
    """
    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    solution_name = ctx["name"]
    project_id = ctx.get("project_id")
    repo_path = ctx.get("repo_path")
    manifest = load_manifest(Path(repo_path).expanduser()) if repo_path else {}
    env_entries = get_env_vars(manifest)

    present_ids = set()
    if project_id:
        present_ids = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}

    secrets = []
    hints = []
    declared_ids = set()
    for entry in env_entries:
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict):
            continue
        secret_name = secret_cfg["name"]
        secret_id = f"{solution_name}-{secret_name}"
        declared_ids.add(secret_id)
        generate = secret_cfg.get("generate", False)

        if not project_id:
            status = "no-project"
        elif secret_id in present_ids:
            status = "ready"
        else:
            classification = _classify_unlabeled(project_id, secret_id)
            kind = classification["kind"]
            if kind == "missing":
                status = "missing-generate" if generate else "missing"
            elif kind == "unattached":
                status = "unattached"
                hints.append(_hint_unattached(project_id, solution_name, secret_id))
            else:  # conflict
                status = "conflict"
                hints.append(_hint_conflict(
                    project_id, solution_name, secret_id, classification["owner"], secret_name,
                ))

        secrets.append({
            "name": secret_name,
            "env_var": entry["name"],
            "secret_id": secret_id,
            "generate": generate,
            "status": status,
        })

    orphans = sorted(present_ids - declared_ids)
    for orphan_id in orphans:
        hints.append(_hint_orphan(project_id, solution_name, orphan_id))

    return {
        "solution": solution_name,
        "project_id": project_id,
        "secrets": secrets,
        "orphans": orphans,
        "hints": hints,
    }


def _classify_unlabeled(project_id: str, secret_id: str) -> dict:
    """For a secret_id NOT in our labeled set, determine why.

    Returns {"kind": "missing"|"unattached"|"conflict", "owner": str|None}.
    `owner` is set only for `conflict`.
    """
    result = subprocess.run(
        ["gcloud", "secrets", "describe", secret_id,
         "--project", project_id,
         "--format", f"value(labels.{GAPP_SOLUTION_LABEL})"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {"kind": "missing", "owner": None}
    owner = result.stdout.strip()
    if not owner:
        return {"kind": "unattached", "owner": None}
    return {"kind": "conflict", "owner": owner}


def _hint_unattached(project_id: str, solution_name: str, secret_id: str) -> dict:
    return {
        "secret_id": secret_id,
        "issue": "unattached",
        "message": (
            f"Secret '{secret_id}' exists in project '{project_id}' but has no "
            f"`{GAPP_SOLUTION_LABEL}` label. gapp will not modify it until ownership "
            f"is established."
        ),
        "options": [
            {
                "label": f"Adopt for solution '{solution_name}' (gapp manages it going forward)",
                "command": (
                    f"gcloud secrets update {secret_id} "
                    f"--update-labels={GAPP_SOLUTION_LABEL}={solution_name} "
                    f"--project={project_id}"
                ),
            },
            {
                "label": "Delete and let gapp recreate it on next deploy or `gapp secrets set`",
                "command": f"gcloud secrets delete {secret_id} --project={project_id}",
            },
        ],
    }


def _hint_conflict(project_id: str, solution_name: str, secret_id: str,
                   owner: str, secret_short_name: str) -> dict:
    return {
        "secret_id": secret_id,
        "issue": "conflict",
        "message": (
            f"Secret '{secret_id}' is labeled for solution '{owner}', not '{solution_name}'. "
            f"gapp will not modify another solution's secret."
        ),
        "options": [
            {
                "label": f"Use a different secret name in this solution's gapp.yaml (rename '{secret_short_name}')",
                "command": "(edit gapp.yaml; gapp constructs the secret_id as <solution>-<name>)",
            },
            {
                "label": f"Re-label for '{solution_name}' if '{owner}' is gone (manual takeover)",
                "command": (
                    f"gcloud secrets update {secret_id} "
                    f"--update-labels={GAPP_SOLUTION_LABEL}={solution_name} "
                    f"--project={project_id}"
                ),
            },
        ],
    }


def _hint_orphan(project_id: str, solution_name: str, secret_id: str) -> dict:
    return {
        "secret_id": secret_id,
        "issue": "orphan",
        "message": (
            f"Secret '{secret_id}' is labeled for solution '{solution_name}' but no "
            f"matching declaration exists in gapp.yaml. It is not consumed by any "
            f"deployed env var."
        ),
        "options": [
            {
                "label": "Delete it (recommended if no longer needed)",
                "command": f"gcloud secrets delete {secret_id} --project={project_id}",
            },
            {
                "label": "Re-add the declaration to gapp.yaml under env: with a matching name",
                "command": "(edit gapp.yaml)",
            },
        ],
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
    """Fast-fail before deploy if non-generate declared secrets are not deployable.

    Uses one label-filtered query to get the present set, then for each
    yaml-declared secret that is absent, follows up with a single describe
    call to distinguish missing / unattached / conflict so the error
    message points at the actual scenario, not the lowest-common-denominator
    "missing".
    """
    present_ids = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}

    problems = []
    for entry in get_env_vars(manifest):
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict):
            continue
        if secret_cfg.get("generate"):
            continue
        secret_name = secret_cfg["name"]
        secret_id = f"{solution_name}-{secret_name}"
        if secret_id in present_ids:
            continue
        classification = _classify_unlabeled(project_id, secret_id)
        problems.append({
            "name": secret_name,
            "env_var": entry["name"],
            "secret_id": secret_id,
            "kind": classification["kind"],
            "owner": classification.get("owner"),
        })

    if not problems:
        return

    lines = [f"{len(problems)} secret(s) declared in gapp.yaml are not deployable:"]
    for p in problems:
        lines.append(f"  {p['env_var']} → {p['secret_id']}")
        if p["kind"] == "missing":
            lines.append(f"    Status: missing in GCP")
            lines.append(f"    Resolve: gapp secrets set {p['name']} <value>")
        elif p["kind"] == "unattached":
            lines.append(f"    Status: exists in GCP but has no `{GAPP_SOLUTION_LABEL}` label")
            lines.append(
                f"    Resolve (adopt): gcloud secrets update {p['secret_id']} "
                f"--update-labels={GAPP_SOLUTION_LABEL}={solution_name} --project={project_id}"
            )
            lines.append(
                f"    Resolve (recreate): gcloud secrets delete {p['secret_id']} "
                f"--project={project_id}  # then `gapp secrets set {p['name']} <value>`"
            )
        else:  # conflict
            lines.append(f"    Status: labeled for solution '{p['owner']}', not '{solution_name}'")
            lines.append(
                f"    Resolve: rename in gapp.yaml, or if '{p['owner']}' is gone, "
                f"re-label: gcloud secrets update {p['secret_id']} "
                f"--update-labels={GAPP_SOLUTION_LABEL}={solution_name} --project={project_id}"
            )
    raise RuntimeError("\n".join(lines))


def materialize_generated_secrets(project_id: str, solution_name: str, manifest: dict) -> list[dict]:
    """Idempotently create + version-set every env-declared secret with generate: true.

    Walks every env entry whose `secret:` block declares `generate: true`. For
    any such secret missing from Secret Manager, creates it with the standard
    gapp-solution label and writes a freshly generated 32-char alphanumeric
    value as its first version. Already-present secrets are left untouched
    (no rotation on redeploy).

    Returns a list of {"name", "secret_id", "status": "created"|"exists"} for
    every generate:true declaration encountered, in declaration order.
    """
    import secrets as _secrets
    import string

    present_ids = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}
    alphabet = string.ascii_letters + string.digits
    results = []
    for entry in get_env_vars(manifest):
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict) or not secret_cfg.get("generate"):
            continue
        name = secret_cfg["name"]
        secret_id = f"{solution_name}-{name}"
        if secret_id in present_ids:
            results.append({"name": name, "secret_id": secret_id, "status": "exists"})
            continue
        _ensure_secret(project_id, secret_id, solution_name)
        value = "".join(_secrets.choice(alphabet) for _ in range(32))
        _add_secret_version(project_id, secret_id, value)
        results.append({"name": name, "secret_id": secret_id, "status": "created"})
    return results


def _find_secret(name: str, solution: str | None = None) -> dict:
    """Look up a secret by its short name as declared in gapp.yaml."""
    ctx = GappSDK().resolve_solution(solution)
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
