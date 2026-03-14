"""gapp user management — register, list, and revoke users via GCS credential files."""

import hashlib
import json
import secrets
import subprocess
from datetime import datetime, timezone

from gapp.admin.sdk.context import resolve_solution


def _get_bucket_name(ctx: dict) -> str:
    """Derive the GCS bucket name for a solution."""
    return f"gapp-{ctx['name']}-{ctx['project_id']}"


def _require_context(solution: str | None = None) -> dict:
    """Resolve solution context or raise."""
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )
    if not ctx.get("project_id"):
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")
    return ctx


def _email_hash(email: str) -> str:
    """SHA-256 hash of email address."""
    return hashlib.sha256(email.lower().strip().encode()).hexdigest()


def _gcs_path(bucket: str, email_hash: str) -> str:
    """GCS path for a user's credential file."""
    return f"gs://{bucket}/auth/{email_hash}.json"


def _object_exists(gcs_path: str) -> bool:
    """Check if a GCS object exists."""
    result = subprocess.run(
        ["gcloud", "storage", "ls", gcs_path],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def register_user(
    email: str,
    credential: str,
    strategy: str = "bearer",
    solution: str | None = None,
) -> dict:
    """Register a new user by writing a credential file to GCS.

    Generates a PAT, writes the credential file, and returns the PAT.
    Raises RuntimeError if the user already exists.
    """
    ctx = _require_context(solution)
    bucket = _get_bucket_name(ctx)
    eh = _email_hash(email)
    gcs_path = _gcs_path(bucket, eh)

    if _object_exists(gcs_path):
        raise RuntimeError(f"User '{email}' already registered. Use 'gapp users update' to change credentials.")

    now = datetime.now(timezone.utc).isoformat()
    credential_data = {
        "strategy": strategy,
        "credential": credential,
        "sub": email,
        "created": now,
    }

    _write_credential(gcs_path, credential_data)

    return {
        "email": email,
        "email_hash": eh,
        "strategy": strategy,
        "created": now,
    }


def list_users(*, limit: int = 10, start_index: int = 0, solution: str | None = None) -> dict:
    """List registered users from GCS object metadata (no file reads).

    Returns dict with solution info and list of users.
    """
    ctx = _require_context(solution)
    bucket = _get_bucket_name(ctx)
    prefix = f"gs://{bucket}/auth/"

    # List objects with JSON output to get custom metadata
    result = subprocess.run(
        ["gcloud", "storage", "ls", "--json", prefix],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return {"name": ctx["name"], "users": [], "total": 0}

    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"name": ctx["name"], "users": [], "total": 0}

    total = len(objects)
    page = objects[start_index:start_index + limit]

    users = []
    for obj in page:
        # gcloud storage ls --json nests everything under obj["metadata"]
        obj_meta = obj.get("metadata", {})
        custom_meta = obj_meta.get("metadata", {})
        name = obj_meta.get("name", "")
        email_hash = name.rsplit("/", 1)[-1].replace(".json", "") if name else ""

        users.append({
            "email_hash": email_hash,
            "sub": custom_meta.get("sub", ""),
            "strategy": custom_meta.get("strategy", ""),
            "created": obj_meta.get("timeCreated", ""),
            "updated": obj_meta.get("updated", ""),
        })

    return {
        "name": ctx["name"],
        "users": users,
        "total": total,
        "start_index": start_index,
        "limit": limit,
    }


def get_user(identifier: str, solution: str | None = None) -> dict:
    """Get full user details by email or email hash.

    Reads the credential file contents (excluding the raw credential value).
    """
    ctx = _require_context(solution)
    bucket = _get_bucket_name(ctx)

    # If it looks like an email, hash it
    if "@" in identifier:
        eh = _email_hash(identifier)
    else:
        eh = identifier

    gcs_path = _gcs_path(bucket, eh)
    data = _read_credential_full(gcs_path)
    if data is None:
        raise RuntimeError(f"User '{identifier}' not found.")

    return {
        "email_hash": eh,
        "sub": data.get("sub", ""),
        "strategy": data.get("strategy", ""),
        "created": data.get("created", ""),
        "revoke_before": data.get("revoke_before"),
    }


def update_user(
    email: str,
    *,
    credential: str | None = None,
    revoke_before: str | None = None,
    solution: str | None = None,
) -> dict:
    """Update a user's credential file in GCS.

    Can update the upstream credential, set revoke_before, or both.
    revoke_before is an ISO 8601 timestamp — all JWTs with iat before
    this time will be rejected.
    """
    ctx = _require_context(solution)
    bucket = _get_bucket_name(ctx)
    eh = _email_hash(email)
    gcs_path = _gcs_path(bucket, eh)

    if not _object_exists(gcs_path):
        raise RuntimeError(f"User '{email}' not found.")

    # Read existing credential
    existing = _read_credential_full(gcs_path)
    if existing is None:
        raise RuntimeError(f"Failed to read credential for '{email}'.")

    updated = dict(existing)
    changes = []

    if credential is not None:
        updated["credential"] = credential
        changes.append("credential")

    if revoke_before is not None:
        updated["revoke_before"] = revoke_before
        changes.append("revoke_before")

    if not changes:
        raise RuntimeError("Nothing to update. Specify --credential or --revoke-before.")

    _write_credential(gcs_path, updated)

    return {
        "email": email,
        "email_hash": eh,
        "changes": changes,
    }


def revoke_user(email: str, solution: str | None = None) -> dict:
    """Revoke a user by deleting their credential file from GCS."""
    ctx = _require_context(solution)
    bucket = _get_bucket_name(ctx)
    eh = _email_hash(email)
    gcs_path = _gcs_path(bucket, eh)

    if not _object_exists(gcs_path):
        raise RuntimeError(f"User '{email}' not found.")

    result = subprocess.run(
        ["gcloud", "storage", "rm", gcs_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to revoke user: {result.stderr.strip()}")

    return {"email": email, "email_hash": eh, "status": "revoked"}


def _write_credential(gcs_path: str, data: dict) -> None:
    """Write a credential JSON file to GCS via stdin with custom metadata."""
    payload = json.dumps(data)
    cmd = ["gcloud", "storage", "cp", "-", gcs_path]

    # Store email and strategy as GCS custom metadata so list can
    # read them without fetching file contents.
    metadata = {}
    if data.get("sub"):
        metadata["sub"] = data["sub"]
    if data.get("strategy"):
        metadata["strategy"] = data["strategy"]
    if metadata:
        pairs = ",".join(f"{k}={v}" for k, v in metadata.items())
        cmd += [f"--custom-metadata={pairs}"]

    result = subprocess.run(
        cmd,
        input=payload,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to write credential: {result.stderr.strip()}")


def _read_credential_full(gcs_path: str) -> dict | None:
    """Read a credential file from GCS and return the full dict."""
    result = subprocess.run(
        ["gcloud", "storage", "cat", gcs_path],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


