"""gapp deploy — build container and terraform apply."""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
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
    """Submit a Cloud Build and return immediately.

    Always async. Returns the build_id and project_id so the caller
    can poll with check_build() or pass build_ref to deploy_solution().
    """
    ctx = _require_solution(solution)
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
    entrypoint, ref_label = _resolve_entrypoint(manifest, service_root, repo_path)

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
    """Check the status of a Cloud Build by ID.

    Runs describe and log fetch in parallel. Returns status, image,
    log progress, and log URL. Does not modify any state.
    """
    # Fire both gcloud calls at once — they're independent read-only APIs
    describe_proc = subprocess.Popen(
        ["gcloud", "builds", "describe", build_id,
         "--project", project_id, "--format=json"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    log_proc = subprocess.Popen(
        ["gcloud", "builds", "log", build_id, "--project", project_id],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )

    describe_out, describe_err = describe_proc.communicate()
    log_out, _ = log_proc.communicate()

    if describe_proc.returncode != 0:
        return {"error": f"Failed to describe build {build_id}: {describe_err.strip()}"}

    result = describe_out

    build = json.loads(result)
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

    # Extract image from results if build succeeded
    results = build.get("results") or {}
    images = results.get("images") or []
    if images:
        out["image"] = images[0].get("name")
    elif build.get("images"):
        out["image"] = build["images"][0]

    # Attach log progress from parallel fetch
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
) -> dict:
    """Deploy the current solution.

    Args:
        auto_approve: Pass -auto-approve to terraform apply.
        ref: Git ref (commit, tag, branch) to deploy.
        solution: Solution name. Defaults to current directory.
        build_ref: Cloud Build ID from a prior start_build() call.
            When provided, polls for build completion then runs terraform.
        build_check_timeout: Max seconds to poll for build completion
            before returning a "running" status. Minimum 10.

    Returns dict describing what was done.
    """
    if build_ref:
        return _deploy_from_build(
            build_ref=build_ref,
            solution=solution,
            auto_approve=auto_approve,
            build_check_timeout=max(10, build_check_timeout),
        )

    # Full blocking deploy
    ctx = _require_solution(solution)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"])
    manifest = load_manifest(repo_path)

    paths = get_paths(manifest)
    if paths:
        results = []
        for sub_path in paths:
            sub_dir = repo_path / sub_path
            if not sub_dir.is_dir():
                raise RuntimeError(f"Path '{sub_path}' declared in gapp.yaml but directory does not exist.")
            sub_manifest = load_manifest(sub_dir)
            if not sub_manifest:
                raise RuntimeError(f"No gapp.yaml found in '{sub_path}'.")
            sub_name = get_name(sub_manifest)
            if not sub_name:
                sub_name = f"{repo_path.name}-{sub_path.replace('/', '-')}"
            sub_result = _deploy_single_service(
                solution_name=sub_name,
                project_id=project_id,
                repo_path=repo_path,
                manifest=sub_manifest,
                service_path=sub_path,
                auto_approve=auto_approve,
                ref=ref,
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
    )


# ---------------------------------------------------------------------------
# Build-ref deploy path
# ---------------------------------------------------------------------------

def _deploy_from_build(
    build_ref: str,
    solution: str | None,
    auto_approve: bool,
    build_check_timeout: int,
) -> dict:
    """Poll a Cloud Build and run terraform when it finishes."""
    ctx = _require_solution(solution)
    solution_name = ctx["name"]
    project_id = ctx["project_id"]
    repo_path = Path(ctx["repo_path"])

    # Poll loop: check immediately, then every 5s until timeout
    start = time.monotonic()
    status_info = check_build(build_ref, project_id)

    if "error" in status_info:
        return status_info

    while status_info["status"] == "running":
        elapsed = time.monotonic() - start
        if elapsed >= build_check_timeout:
            status_info["message"] = (
                f"Build still running after {int(elapsed)}s. "
                "Call gapp_deploy with the same build_ref to check again."
            )
            return status_info
        time.sleep(5)
        status_info = check_build(build_ref, project_id)

    if status_info["status"] == "failed":
        return {
            "status": "failed",
            "build_id": build_ref,
            "cloud_build_status": status_info.get("cloud_build_status"),
            "log_url": status_info.get("log_url"),
            "message": "Cloud Build failed. Check the log URL for details.",
        }

    # Build succeeded — run terraform
    image = status_info.get("image")
    if not image:
        return {"error": "Build succeeded but no image found in build output."}

    manifest = load_manifest(repo_path)
    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)

    # Validate non-generate declared secrets exist; auto-generate the rest.
    from gapp.admin.sdk.secrets import validate_declared_secrets
    validate_declared_secrets(project_id, solution_name, manifest)
    _generate_declared_secrets(project_id, solution_name, manifest)

    token = _get_access_token()
    bucket_name = f"gapp-{solution_name}-{project_id}"
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
    )

    return {
        "name": solution_name,
        "project_id": project_id,
        "image": image,
        "build_status": "built",
        "build_id": build_ref,
        "terraform_status": tf_result["status"],
        "service_url": tf_result.get("service_url"),
        "custom_domain": tf_result.get("custom_domain"),
    }


# ---------------------------------------------------------------------------
# Solution resolution
# ---------------------------------------------------------------------------

def _require_solution(solution: str | None) -> dict:
    """Resolve solution context, raising on missing fields."""
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError("Not inside a gapp solution. Run 'gapp init' first.")

    if not ctx.get("project_id"):
        raise RuntimeError("No GCP project attached. Run 'gapp setup <project-id>' first.")

    if not ctx.get("repo_path"):
        from gapp.admin.sdk.context import get_git_root
        git_root = get_git_root()
        if git_root and (git_root / "gapp.yaml").is_file():
            ctx["repo_path"] = str(git_root)
        else:
            raise RuntimeError("No repo path found for this solution.")

    return ctx


def _resolve_entrypoint(manifest: dict, service_root: Path, repo_path: Path) -> tuple[str, str]:
    """Detect entrypoint from manifest and filesystem. Returns (entrypoint, label)."""
    entrypoint = get_entrypoint(manifest)

    from gapp.admin.sdk.manifest import get_cmd
    cmd = get_cmd(manifest)

    if entrypoint and cmd:
        raise RuntimeError("gapp.yaml has both service.entrypoint and service.cmd. Use one or the other.")

    if entrypoint:
        return entrypoint, "explicit"
    elif cmd:
        return f"__cmd__:{cmd}", "cmd"
    elif (service_root / "Dockerfile").exists():
        return "__dockerfile__", "dockerfile"
    elif (service_root / "mcp-app.yaml").exists() or (repo_path / "mcp-app.yaml").exists():
        return "__mcp_app__", "mcp-app"
    else:
        raise RuntimeError(
            "Cannot determine how to run this service.\n"
            "  Options (in priority order):\n"
            "    1. Set service.entrypoint in gapp.yaml (ASGI module:app)\n"
            "    2. Set service.cmd in gapp.yaml (raw command)\n"
            "    3. Provide a Dockerfile\n"
            "    4. Add mcp-app.yaml (framework handles serving)"
        )


# ---------------------------------------------------------------------------
# Single-service deploy (blocking path)
# ---------------------------------------------------------------------------

def _deploy_single_service(
    solution_name: str,
    project_id: str,
    repo_path: Path,
    manifest: dict,
    *,
    service_path: str | None = None,
    auto_approve: bool = False,
    ref: str | None = None,
) -> dict:
    """Deploy a single service: build + terraform."""
    service_root = repo_path / service_path if service_path else repo_path
    entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)

    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)

    if ref:
        deploy_sha = _resolve_ref(repo_path, ref)
        deploy_ref = ref
    else:
        deploy_sha = _get_head_sha(repo_path)
        deploy_ref = "HEAD"
        _check_dirty_tree(repo_path)

    result = {
        "name": solution_name,
        "project_id": project_id,
        "image": None,
        "build_status": None,
        "terraform_status": None,
        "service_url": None,
        "custom_domain": None,
    }

    token = _get_access_token()
    region = "us-central1"
    _ensure_artifact_registry(project_id, region)

    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if _image_exists(project_id, region, solution_name, deploy_sha):
        result["build_status"] = "skipped"
    else:
        _submit_build_sync(
            project_id, repo_path, image, entrypoint, ref=deploy_ref,
        )
        result["build_status"] = "built"
    result["image"] = image

    # Validate non-generate declared secrets exist; auto-generate the rest.
    from gapp.admin.sdk.secrets import validate_declared_secrets
    validate_declared_secrets(project_id, solution_name, manifest)
    _generate_declared_secrets(project_id, solution_name, manifest)

    bucket_name = f"gapp-{solution_name}-{project_id}"
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
    )
    result["terraform_status"] = tf_result["status"]
    result["service_url"] = tf_result.get("service_url")
    result["custom_domain"] = tf_result.get("custom_domain")

    return result


# ---------------------------------------------------------------------------
# Cloud Build helpers
# ---------------------------------------------------------------------------

def _prepare_build_dir(
    repo_path: Path, image: str, entrypoint: str,
    *, ref: str = "HEAD",
) -> tuple[str, str]:
    """Create temp dir with source archive and Dockerfile template.

    Returns (build_dir, build_entrypoint). Caller owns the temp dir lifetime.
    """
    build_dir = tempfile.mkdtemp(prefix="gapp-build-")

    archive = subprocess.Popen(
        ["git", "archive", "--format=tar", ref],
        stdout=subprocess.PIPE, cwd=repo_path,
    )
    subprocess.run(
        ["tar", "xf", "-", "-C", build_dir],
        stdin=archive.stdout, check=True,
    )
    archive.wait()

    if entrypoint == "__dockerfile__":
        if not (Path(build_dir) / "Dockerfile").exists():
            raise RuntimeError("Dockerfile sentinel set but no Dockerfile in repo.")
        shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
        build_entrypoint = ""
    elif entrypoint == "__mcp_app__":
        shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
        shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
        build_entrypoint = "__mcp_app_serve__"
    elif entrypoint.startswith("__cmd__:"):
        raw_cmd = entrypoint[len("__cmd__:"):]
        shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
        shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
        build_entrypoint = f"__cmd__:{raw_cmd}"
    else:
        shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
        shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
        build_entrypoint = entrypoint

    return build_dir, build_entrypoint


def _submit_build_sync(
    project_id: str, repo_path: Path, image: str, entrypoint: str,
    *, ref: str = "HEAD",
) -> None:
    """Build container via Cloud Build (blocking)."""
    build_dir, build_entrypoint = _prepare_build_dir(
        repo_path, image, entrypoint, ref=ref,
    )
    try:
        result = subprocess.run(
            ["gcloud", "builds", "submit",
             "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions",
             f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
             "--project", project_id,
             build_dir],
            text=True, capture_output=True,
        )
        if result.returncode != 0:
            check = subprocess.run(
                ["gcloud", "artifacts", "docker", "images", "list",
                 "--include-tags", "--filter", f"tags:{image.rsplit(':', 1)[-1]}",
                 "--project", project_id,
                 image.rsplit(":", 1)[0]],
                capture_output=True, text=True,
            )
            if check.returncode == 0 and image.rsplit(":", 1)[-1] in check.stdout:
                return
            raise RuntimeError(
                f"Cloud Build failed.\n  {result.stderr.strip() if result.stderr else 'Check Cloud Build logs in GCP Console.'}"
            )
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


def _submit_build_async(
    project_id: str, repo_path: Path, image: str, entrypoint: str,
    *, ref: str = "HEAD",
) -> str:
    """Submit Cloud Build with --async. Returns the build ID."""
    build_dir, build_entrypoint = _prepare_build_dir(
        repo_path, image, entrypoint, ref=ref,
    )
    try:
        result = subprocess.run(
            ["gcloud", "builds", "submit", "--async", "--format=json",
             "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions",
             f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
             "--project", project_id,
             build_dir],
            text=True, capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Cloud Build submit failed.\n  {result.stderr.strip() if result.stderr else 'Unknown error.'}"
            )
        build = json.loads(result.stdout)
        return build["id"]
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# GCP helpers
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get access token. Run 'gcloud auth login' first.")
    return result.stdout.strip()


def _ensure_artifact_registry(project_id: str, region: str) -> None:
    subprocess.run(
        ["gcloud", "services", "enable", "artifactregistry.googleapis.com",
         "--project", project_id],
        capture_output=True, text=True,
    )
    check = subprocess.run(
        ["gcloud", "artifacts", "repositories", "describe", "gapp",
         "--location", region, "--project", project_id],
        capture_output=True, text=True,
    )
    if check.returncode == 0:
        return
    result = subprocess.run(
        ["gcloud", "artifacts", "repositories", "create", "gapp",
         "--repository-format", "docker",
         "--location", region,
         "--project", project_id],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create Artifact Registry repo: {result.stderr.strip()}")


def _resolve_ref(repo_path: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", ref],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to resolve git ref '{ref}'. Is it a valid commit, tag, or branch?")
    return result.stdout.strip()


def _get_head_sha(repo_path: Path) -> str:
    return _resolve_ref(repo_path, "HEAD")


def _check_dirty_tree(repo_path: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.stdout.strip():
        raise RuntimeError("Working tree has uncommitted changes. Commit or stash before deploying.")


def _image_exists(project_id: str, region: str, solution_name: str, tag: str) -> bool:
    result = subprocess.run(
        ["gcloud", "artifacts", "docker", "images", "list",
         f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}",
         "--filter", f"tags:{tag}",
         "--format", "value(tags)",
         "--project", project_id],
        capture_output=True, text=True,
    )
    return tag in result.stdout


def _get_template(name: str) -> Path:
    return Path(__file__).resolve().parent.parent.parent / "templates" / name


def _generate_declared_secrets(project_id: str, solution_name: str, manifest: dict) -> None:
    """Auto-create secrets declared with `generate: true`, stamping the solution label.

    Idempotent: uses the SDK `_ensure_secret` which updates labels on
    existing secrets and only adds a version on first create.
    """
    from gapp.admin.sdk.manifest import get_env_vars
    from gapp.admin.sdk.secrets import _ensure_secret, _add_secret_version, list_secrets_by_label
    import secrets as secrets_mod

    present = {s["id"] for s in list_secrets_by_label(project_id, solution_name)}
    for entry in get_env_vars(manifest):
        secret_cfg = entry.get("secret")
        if not isinstance(secret_cfg, dict) or not secret_cfg.get("generate"):
            continue
        secret_id = f"{solution_name}-{secret_cfg['name']}"
        if secret_id in present:
            continue
        _ensure_secret(project_id, secret_id, solution_name)
        _add_secret_version(project_id, secret_id, secrets_mod.token_urlsafe(32))


def _secret_name_to_env_var(name: str) -> str:
    return name.upper().replace("-", "_")


def _get_tf_source_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "terraform"


def _build_tfvars(
    solution_name: str,
    project_id: str,
    image: str,
    service_config: dict,
    secrets: dict | None = None,
    env_vars: list[dict] | None = None,
    public: bool | None = None,
    domain: str | None = None,
) -> dict:
    from gapp.admin.sdk.manifest import resolve_env_vars

    bucket_name = f"gapp-{solution_name}-{project_id}"
    env = dict(service_config.get("env", {}))

    secret_env = {}
    if env_vars:
        gapp_vars = {
            "SOLUTION_DATA_PATH": "/mnt/data",
            "SOLUTION_NAME": solution_name,
        }
        resolved = resolve_env_vars(env_vars, gapp_vars)
        for entry in resolved:
            name = entry["name"]
            secret_cfg = entry.get("secret")
            if isinstance(secret_cfg, dict):
                secret_env[name] = f"{solution_name}-{secret_cfg['name']}"
            elif "value" in entry:
                env[name] = entry["value"]

    all_secrets = {
        _secret_name_to_env_var(name): name
        for name in (secrets or {})
    }
    all_secrets.update(secret_env)

    tfvars = {
        "project_id": project_id,
        "service_name": solution_name,
        "image": image,
        "memory": service_config["memory"],
        "cpu": service_config["cpu"],
        "max_instances": service_config["max_instances"],
        "env": env,
        "secrets": all_secrets,
        "data_bucket": bucket_name,
        "public": bool(public),
    }
    if domain:
        tfvars["custom_domain"] = domain
    return tfvars


def _get_staging_dir(solution_name: str) -> Path:
    cache_base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(cache_base) / "gapp" / solution_name / "terraform"


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
) -> dict:
    """Copy static TF files to staging dir, write tfvars.json, and apply."""
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}

    staging_dir = _get_staging_dir(solution_name)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    tf_source = _get_tf_source_dir()
    for tf_file in tf_source.glob("*.tf"):
        shutil.copy2(tf_file, staging_dir)

    from gapp.admin.sdk.manifest import get_env_vars, get_public
    env_vars = get_env_vars(manifest or {})
    public = get_public(manifest or {})
    domain = get_domain(manifest or {})
    tfvars = _build_tfvars(
        solution_name, project_id, image, service_config, secrets,
        env_vars=env_vars,
        public=public,
        domain=domain,
    )
    (staging_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    init_result = subprocess.run(
        ["terraform", "init",
         f"-backend-config=bucket={bucket_name}",
         "-backend-config=prefix=terraform/state",
         "-input=false",
         "-upgrade"],
        cwd=staging_dir, env=env, text=True,
    )
    if init_result.returncode != 0:
        raise RuntimeError("Terraform init failed. Check output above.")

    apply_cmd = ["terraform", "apply", "-input=false"]
    if auto_approve:
        apply_cmd.append("-auto-approve")

    apply_result = subprocess.run(apply_cmd, cwd=staging_dir, env=env, text=True)
    if apply_result.returncode != 0:
        raise RuntimeError("Terraform apply failed. Check output above.")

    output_result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=staging_dir, env=env, capture_output=True, text=True,
    )

    result = {"status": "applied", "service_url": None, "custom_domain": None}
    if output_result.returncode == 0:
        try:
            outputs = json.loads(output_result.stdout)
            result["service_url"] = outputs.get("service_url", {}).get("value")
            result["custom_domain"] = outputs.get("custom_domain", {}).get("value") or None
        except json.JSONDecodeError:
            pass

    return result
