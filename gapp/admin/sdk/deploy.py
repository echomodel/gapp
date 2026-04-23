"""gapp deploy — build container and terraform apply."""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from gapp.admin.sdk.context import resolve_full_context, run_gcloud, get_bucket_name, get_label_key
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

def start_build(solution: str | None = None) -> dict:
    """Submit a Cloud Build and return immediately."""
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
    _ensure_artifact_registry(project_id, region)

    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if _image_exists(project_id, region, solution_name, deploy_sha):
        return {
            "build_id": None,
            "project_id": project_id,
            "image": image,
            "status": "skipped",
            "message": "Image already exists in Artifact Registry.",
        }

    build_id = _submit_build_async(
        project_id, repo_path, image, entrypoint, ref="HEAD",
    )

    return {
        "build_id": build_id,
        "project_id": project_id,
        "image": image,
        "status": "queued",
    }


def check_build(build_id: str, project_id: str) -> dict:
    """Check the status of a Cloud Build by ID."""
    from gapp.admin.sdk.context import get_account
    account = get_account()
    env = os.environ.copy()
    if account:
        env["CLOUDSDK_CORE_ACCOUNT"] = account

    describe_proc = subprocess.Popen(
        ["gcloud", "builds", "describe", build_id,
         "--project", project_id, "--format=json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )
    log_proc = subprocess.Popen(
        ["gcloud", "builds", "log", build_id, "--project", project_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
    )

    describe_out, describe_err = describe_proc.communicate()
    log_out, _ = log_proc.communicate()

    if describe_proc.returncode != 0:
        return {"error": f"Failed to describe build {build_id}: {describe_err.strip()}"}

    build = json.loads(describe_out)
    raw_status = build.get("status", "UNKNOWN")

    if raw_status == "SUCCESS":
        status = "done"
    elif raw_status in ("FAILURE", "TIMEOUT", "CANCELLED", "EXPIRED", "INTERNAL_ERROR"):
        status = "failed"
    else:
        status = "running"

    out = {
        "build_id": build_id,
        "status": status,
        "cloud_build_status": raw_status,
        "log_url": build.get("logUrl"),
    }

    results = build.get("results") or {}
    images = results.get("images") or []
    if images:
        out["image"] = images[0].get("name")
    elif build.get("images"):
        out["image"] = build["images"][0]

    if log_out.strip():
        lines = log_out.strip().splitlines()
        out["log_lines"] = len(lines)
        out["log_tail"] = lines[-3:] if len(lines) >= 3 else lines

    return out


def deploy_solution(
    auto_approve: bool = False,
    ref: str | None = None,
    solution: str | None = None,
    build_ref: str | None = None,
    build_check_timeout: int = 10,
    dry_run: bool = False,
    env: str = "default",
) -> dict:
    """Deploy the current solution."""
    if build_ref:
        return _deploy_from_build(
            build_ref=build_ref,
            solution=solution,
            auto_approve=auto_approve,
            build_check_timeout=max(10, build_check_timeout),
            env=env,
        )

    # 1. Resolve full context upfront (ID, Location, Project)
    from gapp.admin.sdk.context import get_owner
    ctx = resolve_full_context(solution, env=env)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
    
    if not solution_name:
        raise RuntimeError("Could not determine solution name. Run 'gapp init' first.")

    # Prepare metadata object for return/dry-run
    preview = {
        "name": solution_name,
        "owner": get_owner(),
        "label": get_label_key(solution_name, env=env),
        "env": env,
        "project_id": project_id,
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
    )


# ---------------------------------------------------------------------------
# Internal Implementation
# ---------------------------------------------------------------------------

def _resolve_and_enforce_context(solution: str | None = None, env: str = "default") -> dict:
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
    solution: str | None,
    auto_approve: bool,
    build_check_timeout: int,
    env: str = "default",
) -> dict:
    """Poll a Cloud Build and run terraform when it finishes."""
    ctx = _resolve_and_enforce_context(solution, env=env)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"])

    # Poll loop
    start = time.monotonic()
    status_info = check_build(build_ref, project_id)

    if "error" in status_info:
        return status_info

    while status_info["status"] == "running":
        elapsed = time.monotonic() - start
        if elapsed >= build_check_timeout:
            status_info["message"] = f"Build still running after {int(elapsed)}s."
            return status_info
        time.sleep(5)
        status_info = check_build(build_ref, project_id)

    if status_info["status"] == "failed":
        return {"status": "failed", "message": "Cloud Build failed."}

    # Build succeeded — run terraform
    image = status_info.get("image")
    manifest = load_manifest(repo_path)
    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)

    token = _get_access_token()
    bucket_name = get_bucket_name(solution_name, project_id, env=env)
    
    tf_result = _stage_and_apply(
        solution_name=solution_name,
        project_id=project_id,
        image=image,
        bucket_name=bucket_name,
        service_config=service_config,
        secrets=secrets,
        token=token,
        auto_approve=auto_approve,
        manifest=manifest,
        env_name=env,
    )

    return {
        "name": solution_name,
        "project_id": project_id,
        "image": image,
        "terraform_status": tf_result["status"],
        "service_url": tf_result.get("service_url"),
        "env": env,
    }


def _deploy_single_service(
    solution_name: str,
    project_id: str,
    repo_path: Path,
    manifest: dict,
    *,
    service_path: str | None = None,
    auto_approve: bool = False,
    ref: str | None = None,
    env: str = "default",
    parent_solution: str | None = None,
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
    _ensure_artifact_registry(project_id, region)

    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if not _image_exists(project_id, region, solution_name, deploy_sha):
        print(f"  Building image {solution_name}:{deploy_sha}...")
        _submit_build_sync(project_id, repo_path, image, entrypoint, ref=ref or "HEAD")

    from gapp.admin.sdk.secrets import validate_declared_secrets
    validate_declared_secrets(project_id, solution_name, manifest)
    _generate_declared_secrets(project_id, solution_name, manifest)

    # Use Parent Solution Name for the bucket if in a workspace
    bucket_owner = parent_solution or solution_name
    bucket_name = get_bucket_name(bucket_owner, project_id, env=env)
    
    print(f"  Applying infrastructure for {solution_name} (env: {env})...")
    tf_result = _stage_and_apply(
        solution_name=solution_name,
        project_id=project_id,
        image=image,
        bucket_name=bucket_name,
        service_config=service_config,
        secrets=secrets,
        token=token_str := _get_access_token(),
        auto_approve=auto_approve,
        manifest=manifest,
        env_name=env,
        is_workspace=parent_solution is not None,
    )
    
    return {
        "name": solution_name,
        "project_id": project_id,
        "image": image,
        "terraform_status": tf_result["status"],
        "service_url": tf_result.get("service_url"),
        "env": env,
    }


def _stage_and_apply(
    solution_name: str,
    project_id: str,
    image: str,
    bucket_name: str,
    service_config: dict,
    secrets: dict | None = None,
    token: str = "",
    auto_approve: bool = False,
    manifest: dict | None = None,
    env_name: str = "default",
    is_workspace: bool = False,
) -> dict:
    """Copy static TF files to staging dir, write tfvars.json, and apply."""
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}
    from gapp.admin.sdk.context import get_account
    if account := get_account():
        env["CLOUDSDK_CORE_ACCOUNT"] = account

    # Scoping Logic: Shared bucket, isolated state paths
    state_prefix = f"terraform/state/{env_name}/{solution_name}" if is_workspace else f"terraform/state/{env_name}"

    staging_dir = _get_staging_dir(solution_name)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    tf_source = Path(__file__).resolve().parent.parent.parent / "terraform"
    for tf_file in tf_source.glob("*.tf"):
        shutil.copy2(tf_file, staging_dir)

    from gapp.admin.sdk.manifest import get_env_vars, get_public
    tfvars = _build_tfvars(
        solution_name, project_id, image, service_config, secrets,
        env_vars=get_env_vars(manifest or {}),
        public=get_public(manifest or {}),
        domain=get_domain(manifest or {}),
    )
    # Ensure bucket name in tfvars matches the shared solution bucket
    tfvars["data_bucket"] = bucket_name
    (staging_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    subprocess.run(
        ["terraform", "init", f"-backend-config=bucket={bucket_name}",
         f"-backend-config=prefix={state_prefix}", "-input=false", "-upgrade"],
        cwd=staging_dir, env=env, check=True, capture_output=False,
    )

    apply_cmd = ["terraform", "apply", "-input=false"]
    if auto_approve:
        apply_cmd.append("-auto-approve")
    subprocess.run(apply_cmd, cwd=staging_dir, env=env, check=True, capture_output=False)

    output_result = subprocess.run(["terraform", "output", "-json"], cwd=staging_dir, env=env, capture_output=True, text=True)
    
    result = {"status": "applied", "service_url": None}
    if output_result.returncode == 0:
        outputs = json.loads(output_result.stdout)
        result["service_url"] = outputs.get("service_url", {}).get("value")

    return result


# ---------------------------------------------------------------------------
# Cloud Build helpers
# ---------------------------------------------------------------------------

def _submit_build_sync(project_id: str, repo_path: Path, image: str, entrypoint: str, *, ref: str = "HEAD") -> None:
    build_dir, build_entrypoint = _prepare_build_dir(repo_path, image, entrypoint, ref=ref)
    try:
        run_gcloud(
            ["builds", "submit", "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions", f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
             "--project", project_id, build_dir],
            capture_output=False, check=True
        )
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

def _submit_build_async(project_id: str, repo_path: Path, image: str, entrypoint: str, *, ref: str = "HEAD") -> str:
    build_dir, build_entrypoint = _prepare_build_dir(repo_path, image, entrypoint, ref=ref)
    try:
        result = run_gcloud(
            ["builds", "submit", "--async", "--format=json",
             "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions", f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
             "--project", project_id, build_dir],
            capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout)["id"]
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)

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

# ---------------------------------------------------------------------------
# Utility Helpers
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    result = run_gcloud(["auth", "print-access-token"], capture_output=True, text=True, check=True)
    return result.stdout.strip()

def _ensure_artifact_registry(project_id: str, region: str) -> None:
    run_gcloud(["services", "enable", "artifactregistry.googleapis.com", "--project", project_id], capture_output=True)
    check = run_gcloud(["artifacts", "repositories", "describe", "gapp", "--location", region, "--project", project_id], capture_output=True)
    if check.returncode != 0:
        run_gcloud(["artifacts", "repositories", "create", "gapp", "--repository-format", "docker", "--location", region, "--project", project_id], capture_output=True, check=True)

def _resolve_ref(repo_path: Path, ref: str) -> str:
    return subprocess.run(["git", "rev-parse", "--short=12", ref], capture_output=True, text=True, cwd=repo_path, check=True).stdout.strip()

def _get_head_sha(repo_path: Path) -> str:
    return _resolve_ref(repo_path, "HEAD")

def _check_dirty_tree(repo_path: Path) -> None:
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_path).stdout.strip():
        raise RuntimeError("Working tree has uncommitted changes. Commit or stash before deploying.")

def _image_exists(project_id: str, region: str, solution_name: str, tag: str) -> bool:
    image_name = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}"
    result = run_gcloud(["artifacts", "docker", "images", "list", image_name, "--filter", f"tags:{tag}", "--format", "value(tags)", "--project", project_id], capture_output=True, text=True)
    return tag in result.stdout

def _resolve_entrypoint(manifest: dict, service_root: Path, repo_path: Path) -> tuple[str, str]:
    entrypoint = manifest.get("service", {}).get("entrypoint")
    cmd = manifest.get("service", {}).get("cmd")
    if entrypoint and cmd: raise RuntimeError("Both entrypoint and cmd set.")
    if entrypoint: return entrypoint, "explicit"
    if cmd: return f"__cmd__:{cmd}", "cmd"
    if (service_root / "Dockerfile").exists(): return "__dockerfile__", "dockerfile"
    if (service_root / "mcp-app.yaml").exists() or (repo_path / "mcp-app.yaml").exists(): return "__mcp_app__", "mcp-app"
    raise RuntimeError("Cannot determine how to run service.")

def _generate_declared_secrets(project_id: str, solution_name: str, manifest: dict) -> None:
    from gapp.admin.sdk.manifest import get_env_vars
    from gapp.admin.sdk.secrets import _ensure_secret, _add_secret_version, list_secrets_by_label
    import secrets as secrets_mod
    present = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}
    for entry in get_env_vars(manifest):
        if isinstance(s_cfg := entry.get("secret"), dict) and s_cfg.get("generate"):
            s_id = f"{solution_name}-{s_cfg['name']}"
            if s_id not in present:
                _ensure_secret(project_id, s_id, solution_name)
                _add_secret_version(project_id, s_id, secrets_mod.token_urlsafe(32))

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
