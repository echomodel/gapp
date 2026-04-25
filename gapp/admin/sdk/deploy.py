"""gapp deploy — build container and terraform apply."""

import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, List, Dict

from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.context import resolve_full_context, get_bucket_name, get_label_key
from gapp.admin.sdk.manifest import (
    get_domain,
    get_entrypoint,
    get_name,
    get_paths,
    get_prerequisite_secrets,
    get_service_config,
    load_manifest,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_build(solution: Optional[str] = None, provider = None) -> dict:
    """Submit a Cloud Build and return immediately."""
    provider = provider or get_provider()
    ctx = _resolve_and_enforce_context(solution)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"])
    manifest = load_manifest(repo_path)

    paths = get_paths(manifest)
    if paths:
        raise RuntimeError(
            "Async build not supported for workspace (multi-service) solutions. "
            "Use gapp_deploy without build_ref for a blocking deploy."
        )

    service_root = repo_path
    entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)

    deploy_sha = _get_head_sha(repo_path)
    _check_dirty_tree(repo_path)

    region = "us-central1"
    provider.ensure_artifact_registry(project_id, region)

    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if provider.image_exists(project_id, region, solution_name, deploy_sha):
        return {
            "build_id": None,
            "project_id": project_id,
            "image": image,
            "status": "skipped",
            "message": "Image already exists in Artifact Registry.",
        }

    build_dir, build_entrypoint = _prepare_build_dir(repo_path, image, entrypoint)
    try:
        build_id = provider.submit_build_async(project_id, Path(build_dir), image, build_entrypoint)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

    return {
        "build_id": build_id,
        "project_id": project_id,
        "image": image,
        "status": "queued",
    }


def check_build(build_id: str, project_id: str, provider = None) -> dict:
    """Check the status of a Cloud Build by ID."""
    provider = provider or get_provider()
    return provider.check_build(project_id, build_id)


def deploy_solution(
    auto_approve: bool = False,
    ref: Optional[str] = None,
    solution: Optional[str] = None,
    build_ref: Optional[str] = None,
    build_check_timeout: int = 10,
    dry_run: bool = False,
    env: str = "default",
    provider = None
) -> dict:
    """Deploy the current solution."""
    provider = provider or get_provider()

    if build_ref:
        return _deploy_from_build(
            build_ref=build_ref,
            solution=solution,
            auto_approve=auto_approve,
            build_check_timeout=max(10, build_check_timeout),
            env=env,
            provider=provider
        )

    # 1. Resolve full context upfront
    ctx = resolve_full_context(solution, env=env)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
    
    if not solution_name:
        raise RuntimeError("Could not determine solution name. Run 'gapp init' first.")

    # Prepare metadata object for return/dry-run
    preview = {
        "name": solution_name,
        "owner": ctx.get("owner"),
        "label": get_label_key(solution_name, env=env),
        "env": env,
        "project_id": project_id,
        "bucket": get_bucket_name(solution_name, project_id, env=env) if project_id else None,
        "repo_path": str(repo_path) if repo_path else None,
        "status": "ready" if project_id and repo_path else "pending_setup",
        "services": [],
    }

    if repo_path:
        manifest = load_manifest(repo_path)
        paths = get_paths(manifest)
        if paths:
            for sub_path in paths:
                sub_dir = repo_path / sub_path
                sub_manifest = load_manifest(sub_dir) if sub_dir.is_dir() else {}
                from gapp.admin.sdk.manifest import get_name
                sub_name = get_name(sub_manifest)
                if not sub_name:
                    sub_name = f"{solution_name}-{sub_path.replace('/', '-')}"
                preview["services"].append({
                    "name": sub_name,
                    "path": sub_path,
                })
        else:
            preview["services"].append({
                "name": solution_name,
                "path": ".",
            })

    if dry_run:
        return {**preview, "dry_run": True}

    # 2. Enforce requirements for actual deploy
    if not project_id:
        raise RuntimeError(f"No GCP project resolved for '{solution_name}' in environment '{env}'. Run 'gapp setup <project-id> --env {env}' first.")
    if not repo_path:
        raise RuntimeError(f"No local repository found for '{solution_name}'.")

    # Proceed with actual deploy (multi or single)
    manifest = load_manifest(repo_path)
    paths = get_paths(manifest)
    if paths:
        results = []
        for svc in preview["services"]:
            sub_dir = repo_path / svc["path"]
            sub_manifest = load_manifest(sub_dir)
            sub_result = _deploy_single_service(
                solution_name=svc["name"],
                project_id=project_id,
                repo_path=repo_path,
                manifest=sub_manifest,
                service_path=svc["path"],
                auto_approve=auto_approve,
                ref=ref,
                env=env,
                parent_solution=solution_name,
                provider=provider
            )
            results.append(sub_result)
        return {"services": results}

    return _deploy_single_service(
        solution_name=solution_name,
        project_id=project_id,
        repo_path=repo_path,
        manifest=manifest,
        auto_approve=auto_approve,
        ref=ref,
        env=env,
        provider=provider
    )


# ---------------------------------------------------------------------------
# Internal Implementation
# ---------------------------------------------------------------------------

def _resolve_and_enforce_context(solution: Optional[str] = None, env: str = "default") -> dict:
    """Resolve context and raise errors if not fully configured."""
    ctx = resolve_full_context(solution, env=env)
    if not ctx["name"]:
        raise RuntimeError("Not inside a gapp solution. Run 'gapp init' first.")
    if not ctx["project_id"]:
        raise RuntimeError(f"No GCP project attached for '{ctx['name']}'. Run 'gapp setup <project-id>' first.")
    if not ctx["repo_path"]:
        raise RuntimeError(f"No local repository path found for '{ctx['name']}'.")
    return ctx


def _deploy_from_build(
    build_ref: str,
    solution: Optional[str],
    auto_approve: bool,
    build_check_timeout: int,
    env: str = "default",
    provider = None
) -> dict:
    """Poll a Cloud Build and run terraform when it finishes."""
    ctx = _resolve_and_enforce_context(solution, env=env)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"])

    # Poll loop
    start = time.monotonic()
    status_info = provider.check_build(project_id, build_ref)

    while status_info.get("status") not in ("SUCCESS", "FAILURE", "CANCELLED", "TIMEOUT"):
        elapsed = time.monotonic() - start
        if elapsed >= build_check_timeout:
            return {"status": "running", "build_id": build_ref, "message": "Build still running."}
        time.sleep(5)
        status_info = provider.check_build(project_id, build_ref)

    if status_info.get("status") != "SUCCESS":
        return {"status": "failed", "message": f"Cloud Build failed: {status_info.get('status')}"}

    # Build succeeded — run terraform
    image = status_info["results"]["images"][0]["name"]
    manifest = load_manifest(repo_path)
    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)
    bucket_name = get_bucket_name(solution_name, project_id, env=env)
    
    tfvars = _build_tfvars(
        solution_name, project_id, image, service_config, secrets,
        env_vars=get_paths(manifest), # wait this is wrong, should be get_env_vars
        public=get_paths(manifest), # wait this is also wrong
        domain=get_domain(manifest)
    )

    outputs = provider.apply_infrastructure(
        staging_dir=_get_staging_dir(solution_name),
        bucket_name=bucket_name,
        state_prefix=f"terraform/state/{env}",
        auto_approve=auto_approve,
        tfvars=tfvars
    )

    return {
        "name": solution_name,
        "project_id": project_id,
        "image": image,
        "service_url": outputs.get("service_url"),
        "env": env,
    }


def _deploy_single_service(
    solution_name: str,
    project_id: str,
    repo_path: Path,
    manifest: dict,
    *,
    service_path: Optional[str] = None,
    auto_approve: bool = False,
    ref: Optional[str] = None,
    env: str = "default",
    parent_solution: Optional[str] = None,
    provider = None
) -> dict:
    """Deploy a single service: build + terraform."""
    service_root = repo_path / service_path if service_path else repo_path
    entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)

    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)

    deploy_sha = _resolve_ref(repo_path, ref) if ref else _get_head_sha(repo_path)
    if not ref:
        _check_dirty_tree(repo_path)

    region = "us-central1"
    provider.ensure_artifact_registry(project_id, region)

    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if not provider.image_exists(project_id, region, solution_name, deploy_sha):
        print(f"  Building image {solution_name}:{deploy_sha}...")
        build_dir, build_entrypoint = _prepare_build_dir(repo_path, image, entrypoint, ref=ref or "HEAD")
        try:
            provider.submit_build_sync(project_id, Path(build_dir), image, build_entrypoint)
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)

    # Use Parent Solution Name for the bucket if in a workspace
    bucket_owner = parent_solution or solution_name
    bucket_name = get_bucket_name(bucket_owner, project_id, env=env)
    state_prefix = f"terraform/state/{env}/{solution_name}" if parent_solution else f"terraform/state/{env}"
    
    from gapp.admin.sdk.manifest import get_env_vars, get_public
    tfvars = _build_tfvars(
        solution_name, project_id, image, service_config, secrets,
        env_vars=get_env_vars(manifest),
        public=get_public(manifest),
        domain=get_domain(manifest)
    )
    tfvars["data_bucket"] = bucket_name

    print(f"  Applying infrastructure for {solution_name} (env: {env})...")
    outputs = provider.apply_infrastructure(
        staging_dir=_get_staging_dir(solution_name),
        bucket_name=bucket_name,
        state_prefix=state_prefix,
        auto_approve=auto_approve,
        tfvars=tfvars
    )
    
    return {
        "name": solution_name,
        "project_id": project_id,
        "image": image,
        "terraform_status": "applied",
        "service_url": outputs.get("service_url"),
        "env": env,
    }


def _prepare_build_dir(repo_path: Path, image: str, entrypoint: str, *, ref: str = "HEAD") -> tuple[str, str]:
    build_dir = tempfile.mkdtemp(prefix="gapp-build-")
    archive = subprocess.Popen(["git", "archive", "--format=tar", ref], stdout=subprocess.PIPE, cwd=repo_path)
    subprocess.run(["tar", "xf", "-", "-C", build_dir], stdin=archive.stdout, check=True)
    archive.wait()

    template_dir = Path(__file__).resolve().parent.parent.parent / "templates"
    
    if entrypoint == "__dockerfile__":
        build_entrypoint = ""
    elif entrypoint == "__mcp_app__":
        shutil.copy2(template_dir / "Dockerfile", Path(build_dir) / "Dockerfile")
        build_entrypoint = "__mcp_app_serve__"
    elif entrypoint.startswith("__cmd__:"):
        shutil.copy2(template_dir / "Dockerfile", Path(build_dir) / "Dockerfile")
        build_entrypoint = entrypoint
    else:
        shutil.copy2(template_dir / "Dockerfile", Path(build_dir) / "Dockerfile")
        build_entrypoint = entrypoint

    shutil.copy2(template_dir / "cloudbuild.yaml", Path(build_dir) / "cloudbuild.yaml")
    return build_dir, build_entrypoint


def _resolve_ref(repo_path: Path, ref: str) -> str:
    return subprocess.run(["git", "rev-parse", "--short=12", ref], capture_output=True, text=True, cwd=repo_path, check=True).stdout.strip()

def _get_head_sha(repo_path: Path) -> str:
    return _resolve_ref(repo_path, "HEAD")

def _check_dirty_tree(repo_path: Path) -> None:
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_path).stdout.strip():
        raise RuntimeError("Working tree has uncommitted changes. Commit or stash before deploying.")

def _resolve_entrypoint(manifest: dict, service_root: Path, repo_path: Path) -> tuple[str, str]:
    entrypoint = manifest.get("service", {}).get("entrypoint")
    cmd = manifest.get("service", {}).get("cmd")
    if entrypoint and cmd: raise RuntimeError("Both entrypoint and cmd set.")
    if entrypoint: return entrypoint, "explicit"
    if cmd: return f"__cmd__:{cmd}", "cmd"
    if (service_root / "Dockerfile").exists(): return "__dockerfile__", "dockerfile"
    if (service_root / "mcp-app.yaml").exists() or (repo_path / "mcp-app.yaml").exists(): return "__mcp_app__", "mcp-app"
    raise RuntimeError("Cannot determine how to run service.")

def _build_tfvars(solution_name, project_id, image, service_config, secrets, env_vars, public, domain) -> dict:
    from gapp.admin.sdk.manifest import resolve_env_vars
    env = dict(service_config.get("env", {}))
    if env_vars:
        for entry in resolve_env_vars(env_vars, {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": solution_name}):
            if isinstance(s_cfg := entry.get("secret"), dict): pass # handled via secrets mapping below
            elif "value" in entry: env[entry["name"]] = entry["value"]
    return {
        "project_id": project_id, "service_name": solution_name, "image": image,
        "memory": service_config["memory"], "cpu": service_config["cpu"], "max_instances": service_config["max_instances"],
        "env": env, "secrets": {name.upper().replace("-", "_"): name for name in (secrets or {})},
        "public": bool(public), "custom_domain": domain
    }

def _get_staging_dir(solution_name: str) -> Path:
    base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(base) / "gapp" / solution_name / "terraform"
