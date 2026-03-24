"""gapp status — infrastructure health check."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
from gapp.admin.sdk.deploy import _get_staging_dir, _get_tf_source_dir
from gapp.admin.sdk.manifest import get_auth_config, get_mcp_path, load_manifest
from gapp.admin.sdk.models import DeploymentInfo, NextStep, ServiceStatus, StatusResult


class TerraformNotFoundError(RuntimeError):
    """Raised when terraform CLI is not installed."""
    pass


class GcloudNotFoundError(RuntimeError):
    """Raised when gcloud CLI is not installed or not authenticated."""
    pass


def get_status(name: str | None = None) -> StatusResult:
    """Infrastructure status check for a solution.

    Reports on the attached project and deployment state.
    Use gapp_deployments_list for cross-project discovery.

    Raises:
        TerraformNotFoundError: terraform CLI is not installed.
        GcloudNotFoundError: gcloud CLI is not installed or not authenticated.
    """
    ctx = resolve_solution(name)
    if not ctx:
        return StatusResult(
            initialized=False,
            next_step=NextStep(action="init"),
        )

    project_id = ctx.get("project_id")

    result = StatusResult(
        initialized=True,
        name=ctx["name"],
        repo_path=ctx.get("repo_path"),
        deployment=DeploymentInfo(
            project=project_id,
            pending=True,
        ),
    )

    if not project_id:
        result.next_step = NextStep(
            action="setup",
            hint="No GCP project attached.",
        )
        return result

    mcp_path = None
    auth_enabled = False
    if ctx.get("repo_path"):
        manifest = load_manifest(Path(ctx["repo_path"]).expanduser())
        mcp_path = get_mcp_path(manifest)
        auth_enabled = bool(get_auth_config(manifest))

    tf_outputs = _get_tf_outputs(ctx["name"], project_id)
    if tf_outputs is None:
        result.next_step = NextStep(
            action="deploy",
            hint="Not deployed (no Terraform state found).",
        )
        return result

    result.deployment.pending = False

    service_url = tf_outputs.get("service_url")
    if service_url:
        service = ServiceStatus(
            name=ctx["name"],
            url=service_url,
            healthy=_check_health(service_url),
            auth_enabled=auth_enabled,
            mcp_path=mcp_path,
        )
        result.deployment.services.append(service)

    return result


def _get_tf_outputs(solution_name: str, project_id: str) -> dict | None:
    """Read Terraform outputs from remote state without applying.

    Raises:
        TerraformNotFoundError: terraform CLI is not installed.
        GcloudNotFoundError: gcloud CLI is not installed or not authenticated.
    """
    staging_dir = _get_staging_dir(solution_name)
    bucket_name = f"gapp-{solution_name}-{project_id}"

    if not staging_dir.exists() or not (staging_dir / "main.tf").exists():
        staging_dir.mkdir(parents=True, exist_ok=True)
        tf_source = _get_tf_source_dir()
        for tf_file in tf_source.glob("*.tf"):
            shutil.copy2(tf_file, staging_dir)

    try:
        token_result = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise GcloudNotFoundError("gcloud CLI is not installed.")
    if token_result.returncode != 0:
        raise GcloudNotFoundError("gcloud is not authenticated. Run: gcloud auth login")
    token = token_result.stdout.strip()
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}

    try:
        init_result = subprocess.run(
            ["terraform", "init",
             f"-backend-config=bucket={bucket_name}",
             "-backend-config=prefix=terraform/state",
             "-input=false", "-upgrade"],
            cwd=staging_dir, env=env,
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise TerraformNotFoundError("terraform CLI is not installed.")
    if init_result.returncode != 0:
        return None

    output_result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=staging_dir, env=env,
        capture_output=True, text=True,
    )
    if output_result.returncode != 0:
        return None

    try:
        raw = json.loads(output_result.stdout)
    except json.JSONDecodeError:
        return None

    if not raw:
        return None

    return {k: v.get("value") for k, v in raw.items()}


def _check_health(service_url: str) -> bool:
    """Hit /health and return True if 200."""
    try:
        result = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
             f"{service_url}/health"],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() == "200"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
