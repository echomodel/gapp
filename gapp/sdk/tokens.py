"""gapp token management — create and revoke JWTs for solution users."""

import subprocess
import time
from datetime import datetime, timezone

import jwt

from gapp.sdk.context import resolve_solution
from gapp.sdk.users import _email_hash, _gcs_path, _object_exists, _read_credential_full, _write_credential

DEFAULT_DURATION_DAYS = 3650  # 10 years


def _require_context() -> dict:
    """Resolve solution context or raise."""
    ctx = resolve_solution()
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )
    if not ctx.get("project_id"):
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")
    return ctx


def _get_signing_key(project_id: str, solution_name: str) -> str:
    """Read the signing key from Secret Manager."""
    secret_id = f"{solution_name}-signing-key"
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         "--secret", secret_id,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to read signing key. Has 'gapp deploy' been run?\n"
            f"  {result.stderr.strip()}"
        )
    return result.stdout.strip()


def create_status_token(solution_name: str, project_id: str) -> str:
    """Create a short-lived JWT with scope=status for service probing.

    No registered user required — the middleware skips credential
    mediation for status-scoped tokens.
    """
    signing_key = _get_signing_key(project_id, solution_name)
    now = int(time.time())
    payload = {
        "sub": "_gapp_status",
        "aud": solution_name,
        "scope": "status",
        "iat": now,
        "exp": now + 60,  # 1 minute
    }
    return jwt.encode(payload, signing_key, algorithm="HS256")


def create_token(email: str, *, duration_days: int = DEFAULT_DURATION_DAYS) -> dict:
    """Create a signed JWT for a registered user.

    The user must already exist (credential file in GCS).
    Returns the JWT string and metadata.
    """
    ctx = _require_context()
    bucket = f"gapp-{ctx['name']}-{ctx['project_id']}"
    eh = _email_hash(email)
    gcs_path = _gcs_path(bucket, eh)

    if not _object_exists(gcs_path):
        raise RuntimeError(f"User '{email}' not registered. Run 'gapp users register' first.")

    signing_key = _get_signing_key(ctx["project_id"], ctx["name"])

    now = int(time.time())
    exp = now + (duration_days * 86400)

    payload = {
        "sub": email,
        "aud": ctx["name"],
        "iat": now,
        "exp": exp,
    }

    token = jwt.encode(payload, signing_key, algorithm="HS256")

    return {
        "token": token,
        "email": email,
        "solution": ctx["name"],
        "issued_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "expires_at": datetime.fromtimestamp(exp, tz=timezone.utc).isoformat(),
        "duration_days": duration_days,
    }


def revoke_tokens(email: str) -> dict:
    """Revoke all tokens for a user by setting revoke_before to now.

    All JWTs issued before this moment will be rejected.
    """
    ctx = _require_context()
    bucket = f"gapp-{ctx['name']}-{ctx['project_id']}"
    eh = _email_hash(email)
    gcs_path = _gcs_path(bucket, eh)

    if not _object_exists(gcs_path):
        raise RuntimeError(f"User '{email}' not found.")

    existing = _read_credential_full(gcs_path)
    if existing is None:
        raise RuntimeError(f"Failed to read credential for '{email}'.")

    now = datetime.now(timezone.utc).isoformat()
    updated = {**existing, "revoke_before": now}
    _write_credential(gcs_path, updated)

    return {
        "email": email,
        "revoke_before": now,
    }
