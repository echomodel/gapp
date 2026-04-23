"""gapp status — infrastructure health check."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from gapp.admin.sdk.context import resolve_full_context, get_bucket_name
from gapp.admin.sdk.deploy import _get_staging_dir
from gapp.admin.sdk.manifest import get_domain, load_manifest, get_paths
from gapp.admin.sdk.models import DeploymentInfo, DomainStatus, NextStep, ServiceStatus, StatusResult


class TerraformNotFoundError(RuntimeError):
    """Raised when terraform CLI is not installed."""
    pass


class GcloudNotFoundError(RuntimeError):
    """Raised when gcloud CLI is not installed or not authenticated."""
    pass


def get_status(name: str | None = None, env: str = "default") -> StatusResult:
    """Infrastructure status check for a solution."""
    ctx = resolve_full_context(name, env=env)
    
    if not ctx["name"]:
        return StatusResult(
            initialized=False,
            next_step=NextStep(action="init"),
        )

    solution_name = ctx["name"]
    project_id = ctx.get("project_id")
    repo_path = ctx.get("repo_path")

    result = StatusResult(
        initialized=True,
        name=solution_name,
        repo_path=repo_path,
        deployment=DeploymentInfo(
            project=project_id,
            pending=True,
        ),
    )

    if not project_id:
        result.next_step = NextStep(
            action="setup",
            hint=f"No GCP project attached for solution '{solution_name}' in env '{env}'.",
        )
        return result

    # 1. Resolve all services in the solution
    services_to_check = []
    domain = None
    
    if repo_path:
        path = Path(repo_path).expanduser()
        manifest = load_manifest(path)
        domain = get_domain(manifest)
        paths = get_paths(manifest)
        
        if paths:
            for sub_path in paths:
                sub_dir = path / sub_path
                sub_manifest = load_manifest(sub_dir) if sub_dir.is_dir() else {}
                from gapp.admin.sdk.manifest import get_name
                sub_name = get_name(sub_manifest) or f"{solution_name}-{sub_path.replace('/', '-')}"
                services_to_check.append({"name": sub_name, "is_workspace": True})
        else:
            services_to_check.append({"name": solution_name, "is_workspace": False})
    else:
        # If no local repo, we only check the main solution service
        services_to_check.append({"name": solution_name, "is_workspace": False})

    # 2. Get status for each service
    for svc in services_to_check:
        try:
            # Workspace root (solution_name) owns the bucket
            bucket_name = get_bucket_name(solution_name, project_id, env=env)
            tf_outputs = _get_tf_outputs(svc["name"], project_id, bucket_name, env, svc["is_workspace"])
            
            if tf_outputs:
                url = tf_outputs.get("service_url")
                if url:
                    result.deployment.services.append(ServiceStatus(
                        name=svc["name"],
                        url=url,
                        healthy=_check_health(url)
                    ))
                    result.deployment.pending = False
        except (TerraformNotFoundError, GcloudNotFoundError):
            continue

    if domain:
        result.domain = _check_domain_status(domain)

    return result


def _get_tf_outputs(service_name: str, project_id: str, bucket_name: str, env_name: str, is_workspace: bool) -> dict | None:
    """Read Terraform outputs from remote state."""
    staging_dir = _get_staging_dir(service_name)
    state_prefix = f"terraform/state/{env_name}/{service_name}" if is_workspace else f"terraform/state/{env_name}"

    if not staging_dir.exists():
        staging_dir.mkdir(parents=True, exist_ok=True)
        # We need the static TF files to run 'terraform output'
        tf_source = Path(__file__).resolve().parent.parent.parent / "terraform"
        for tf_file in tf_source.glob("*.tf"):
            shutil.copy2(tf_file, staging_dir)

    try:
        from gapp.admin.sdk.context import run_gcloud
        token_result = run_gcloud(["auth", "print-access-token"], capture_output=True, text=True)
        if token_result.returncode != 0:
            raise GcloudNotFoundError("gcloud is not authenticated.")
        token = token_result.stdout.strip()
    except FileNotFoundError:
        raise GcloudNotFoundError("gcloud CLI is not installed.")

    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}

    try:
        init_result = subprocess.run(
            ["terraform", "init", f"-backend-config=bucket={bucket_name}",
             f"-backend-config=prefix={state_prefix}", "-input=false", "-upgrade"],
            cwd=staging_dir, env=env, capture_output=True, text=True,
        )
        if init_result.returncode != 0:
            return None

        output_result = subprocess.run(["terraform", "output", "-json"], cwd=staging_dir, env=env, capture_output=True, text=True)
        if output_result.returncode != 0:
            return None

        raw = json.loads(output_result.stdout)
        return {k: v.get("value") for k, v in raw.items()}
    except FileNotFoundError:
        raise TerraformNotFoundError("terraform CLI is not installed.")
    except Exception:
        return None


def _check_domain_status(domain: str) -> DomainStatus:
    # ... logic unchanged ...
    cname_target = "ghs.googlehosted.com"
    try:
        result = subprocess.run(["dig", "+short", "CNAME", domain], capture_output=True, text=True, timeout=10)
        cname = result.stdout.strip().rstrip(".")
        if not cname: return DomainStatus(name=domain, status="pending_dns", detail=f"No CNAME record found.")
        if cname == cname_target: return DomainStatus(name=domain, status="active")
        return DomainStatus(name=domain, status="pending_dns", detail=f"CNAME points to {cname}")
    except Exception:
        return DomainStatus(name=domain, status="pending_dns", detail="DNS check failed.")

def _check_health(service_url: str) -> bool:
    try:
        result = subprocess.run(["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}", f"{service_url}/health"], capture_output=True, text=True, timeout=10)
        return result.stdout.strip() == "200"
    except Exception: return False
