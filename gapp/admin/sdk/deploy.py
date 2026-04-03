"""gapp deploy — build container and terraform apply."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
from gapp.admin.sdk.manifest import (
    get_auth_config,
    get_entrypoint,
    get_name,
    get_paths,
    get_prerequisite_secrets,
    get_runtime_ref,
    get_service_config,
    load_manifest,
)


def deploy_solution(auto_approve: bool = False, ref: str | None = None, solution: str | None = None) -> dict:
    """Deploy the current solution.

    Steps:
    1. Resolve solution context and load manifest
    2. Validate entrypoint and clean git tree (skipped when ref is provided)
    3. Build container via Cloud Build (git archive, skip if image:sha exists)
    4. Stage static Terraform + write tfvars.json
    5. Terraform init with GCS backend + apply

    Args:
        auto_approve: Pass -auto-approve to terraform apply.
        ref: Git ref (commit, tag, branch) to deploy. When provided, the dirty
            tree check is skipped and the specified ref is used for both the
            image tag and git archive source. When omitted, HEAD is used and
            the working tree must be clean.

    Returns dict describing what was done.
    """
    ctx = resolve_solution(solution)
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run 'gapp init' first, or cd into a solution repo."
        )

    solution_name = ctx["name"]
    project_id = ctx.get("project_id")
    repo_path = ctx.get("repo_path")

    if not project_id:
        raise RuntimeError(
            "No GCP project attached. Run 'gapp setup <project-id>' first."
        )
    if not repo_path:
        # Fall back to cwd if it has a gapp.yaml (e.g., CI runner with checkout)
        from gapp.admin.sdk.context import get_git_root
        git_root = get_git_root()
        if git_root and (git_root / "gapp.yaml").is_file():
            repo_path = str(git_root)
        else:
            raise RuntimeError("No repo path found for this solution.")

    repo_path = Path(repo_path)
    manifest = load_manifest(repo_path)

    # Workspace pattern: if manifest has paths:, deploy each as a separate service
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
            # Derive service name: explicit name > repo-path-segments
            sub_name = get_name(sub_manifest)
            if not sub_name:
                sub_name = f"{repo_path.name}-{sub_path.replace('/', '-')}"
            # Recurse: deploy the sub-path as its own service
            # Re-resolve context with the sub-name and sub-manifest
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

    # Single-service deploy (no paths)
    return _deploy_single_service(
        solution_name=solution_name,
        project_id=project_id,
        repo_path=repo_path,
        manifest=manifest,
        auto_approve=auto_approve,
        ref=ref,
    )


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
    """Deploy a single service from a manifest.

    Args:
        solution_name: Cloud Run service name.
        project_id: GCP project ID.
        repo_path: Git repo root (always the full repo).
        manifest: Parsed gapp.yaml for this service.
        service_path: Subdirectory within repo (for multi-service repos).
        auto_approve: Skip Terraform confirmation.
        ref: Git ref to deploy.
    """
    # Resolve the service root for entrypoint detection
    service_root = repo_path / service_path if service_path else repo_path

    entrypoint = get_entrypoint(manifest)

    # Entrypoint detection priority:
    # 1. service.entrypoint or service.cmd in gapp.yaml (mutually exclusive, trumps all)
    # 2. Dockerfile in service root → use as-is
    # 3. mcp-app.yaml in service root → mcp-app serve
    # 4. Error
    from gapp.admin.sdk.manifest import get_cmd
    cmd = get_cmd(manifest)

    if entrypoint and cmd:
        raise RuntimeError("gapp.yaml has both service.entrypoint and service.cmd. Use one or the other.")

    if entrypoint:
        pass  # ASGI module:app for uvicorn
    elif cmd:
        entrypoint = f"__cmd__:{cmd}"
    elif (service_root / "Dockerfile").exists():
        entrypoint = "__dockerfile__"
    elif (service_root / "mcp-app.yaml").exists() or (repo_path / "mcp-app.yaml").exists():
        entrypoint = "__mcp_app__"
    else:
        raise RuntimeError(
            "Cannot determine how to run this service.\n"
            "  Options (in priority order):\n"
            "    1. Set service.entrypoint in gapp.yaml (ASGI module:app)\n"
            "    2. Set service.cmd in gapp.yaml (raw command)\n"
            "    3. Provide a Dockerfile\n"
            "    4. Add mcp-app.yaml (framework handles serving)"
        )

    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)
    auth_config = get_auth_config(manifest)
    runtime_ref = get_runtime_ref(manifest)

    # Resolve git ref to a short SHA for image tagging
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
    }

    # Get access token for consistent identity across gcloud and terraform
    token = _get_access_token()

    # Ensure Artifact Registry repo exists
    region = "us-central1"
    _ensure_artifact_registry(project_id, region)

    # Build and push container image (skip if image:sha already exists)
    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{deploy_sha}"
    if _image_exists(project_id, region, solution_name, deploy_sha):
        result["build_status"] = "skipped"
    else:
        _build_and_push(
            project_id, repo_path, image,
            entrypoint,
            ref=deploy_ref,
            auth_config=auth_config,
            runtime_ref=runtime_ref,
        )
        result["build_status"] = "built"
    result["image"] = image

    # Auto-generate secrets declared with generate: true
    from gapp.admin.sdk.manifest import get_env_vars
    for entry in get_env_vars(manifest):
        secret_cfg = entry.get("secret")
        if isinstance(secret_cfg, dict) and secret_cfg.get("generate"):
            secret_name = entry["name"].lower().replace("_", "-")
            if not _secret_exists(project_id, secret_name):
                import secrets as secrets_mod
                _create_and_set_secret(project_id, secret_name, secrets_mod.token_urlsafe(32))

    # Stage Terraform and apply
    bucket_name = f"gapp-{solution_name}-{project_id}"
    tf_result = _stage_and_apply(
        solution_name=solution_name,
        project_id=project_id,
        image=image,
        bucket_name=bucket_name,
        service_config=service_config,
        secrets=secrets,
        auth_config=auth_config,
        token=token,
        auto_approve=auto_approve,
        manifest=manifest,
    )
    result["terraform_status"] = tf_result["status"]
    result["service_url"] = tf_result.get("service_url")

    return result


def _get_access_token() -> str:
    """Get access token from gcloud for consistent identity."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get access token. Run 'gcloud auth login' first.")
    return result.stdout.strip()


def _ensure_artifact_registry(project_id: str, region: str) -> None:
    """Ensure Artifact Registry repo 'gapp' exists. Idempotent."""
    subprocess.run(
        ["gcloud", "services", "enable", "artifactregistry.googleapis.com",
         "--project", project_id],
        capture_output=True,
        text=True,
    )

    check = subprocess.run(
        ["gcloud", "artifacts", "repositories", "describe", "gapp",
         "--location", region, "--project", project_id],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return

    result = subprocess.run(
        ["gcloud", "artifacts", "repositories", "create", "gapp",
         "--repository-format", "docker",
         "--location", region,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create Artifact Registry repo: {result.stderr.strip()}")


def _resolve_ref(repo_path: Path, ref: str) -> str:
    """Resolve a git ref (commit, tag, branch) to a short SHA."""
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", ref],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to resolve git ref '{ref}'. Is it a valid commit, tag, or branch?")
    return result.stdout.strip()


def _get_head_sha(repo_path: Path) -> str:
    """Get short SHA of HEAD commit."""
    return _resolve_ref(repo_path, "HEAD")


def _check_dirty_tree(repo_path: Path) -> None:
    """Block deploy if working tree has uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.stdout.strip():
        raise RuntimeError(
            "Working tree has uncommitted changes. Commit or stash before deploying."
        )


def _image_exists(
    project_id: str, region: str, solution_name: str, tag: str,
) -> bool:
    """Check if image:tag already exists in Artifact Registry."""
    result = subprocess.run(
        ["gcloud", "artifacts", "docker", "images", "list",
         f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}",
         "--filter", f"tags:{tag}",
         "--format", "value(tags)",
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    return tag in result.stdout


def _get_template(name: str) -> Path:
    """Get the path to a gapp template file."""
    return Path(__file__).resolve().parent.parent.parent / "templates" / name


def _build_and_push(
    project_id: str, repo_path: Path, image: str, entrypoint: str,
    *, ref: str = "HEAD", auth_config: dict | None = None,
    runtime_ref: str | None = None,
) -> None:
    """Build container via Cloud Build using git archive for source integrity.

    Extracts git archive of the specified ref to a temp dir, copies gapp's
    Dockerfile template into it, and submits to Cloud Build.

    When auth is enabled and runtime_ref is set, the Dockerfile installs
    gapp_run from the gapp GitHub repo at the specified ref.
    """
    with tempfile.TemporaryDirectory(prefix="gapp-build-") as build_dir:
        # Extract committed source into temp dir
        archive = subprocess.Popen(
            ["git", "archive", "--format=tar", ref],
            stdout=subprocess.PIPE,
            cwd=repo_path,
        )
        subprocess.run(
            ["tar", "xf", "-", "-C", build_dir],
            stdin=archive.stdout,
            check=True,
        )
        archive.wait()

        # Determine Dockerfile source and entrypoint
        build_runtime_ref = ""

        if entrypoint == "__dockerfile__":
            # Solution has its own Dockerfile — use it, skip gapp templates
            if not (Path(build_dir) / "Dockerfile").exists():
                raise RuntimeError("Dockerfile sentinel set but no Dockerfile in repo.")
            shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
            build_entrypoint = ""  # not used — Dockerfile has its own CMD
        elif entrypoint == "__mcp_app__":
            # mcp-app.yaml detected — use mcp-app serve as CMD
            shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
            shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
            build_entrypoint = "__mcp_app_serve__"
        elif entrypoint.startswith("__cmd__:"):
            # Raw command from service.cmd
            raw_cmd = entrypoint[len("__cmd__:"):]
            shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
            shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
            build_entrypoint = f"__cmd__:{raw_cmd}"
        else:
            # Explicit ASGI entrypoint from gapp.yaml
            shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
            shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")
            build_entrypoint = entrypoint

            # When auth enabled: swap entrypoint to the gapp_run wrapper
            if auth_config:
                if not runtime_ref:
                    raise RuntimeError(
                        "Auth is enabled but no runtime version specified.\n"
                        "  Add to gapp.yaml:\n"
                        "    service:\n"
                        "      runtime: main  # gapp git ref (tag, branch, or commit)"
                    )
                build_entrypoint = "gapp_run.wrapper:app"
                build_runtime_ref = runtime_ref

        # Submit to Cloud Build with substitutions
        result = subprocess.run(
            ["gcloud", "builds", "submit",
             "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions",
             f"_ENTRYPOINT={build_entrypoint},_IMAGE={image},_RUNTIME_REF={build_runtime_ref}",
             "--project", project_id,
             build_dir],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            # Cloud Build may succeed but gcloud fails to stream logs (e.g., CI
            # runner lacks log bucket access). Check if the image was pushed.
            check = subprocess.run(
                ["gcloud", "artifacts", "docker", "images", "list",
                 "--include-tags", "--filter", f"tags:{image.rsplit(':', 1)[-1]}",
                 "--project", project_id,
                 image.rsplit(":", 1)[0]],
                capture_output=True, text=True,
            )
            if check.returncode == 0 and image.rsplit(":", 1)[-1] in check.stdout:
                return  # Build succeeded despite log streaming failure
            raise RuntimeError(
                f"Cloud Build failed.\n  {result.stderr.strip() if result.stderr else 'Check Cloud Build logs in GCP Console.'}"
            )


def _secret_exists(project_id: str, secret_name: str) -> bool:
    """Check if a secret exists in Secret Manager."""
    result = subprocess.run(
        ["gcloud", "secrets", "describe", secret_name, "--project", project_id],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _create_and_set_secret(project_id: str, secret_name: str, value: str) -> None:
    """Create a secret in Secret Manager and set its initial value."""
    subprocess.run(
        ["gcloud", "secrets", "create", secret_name, "--project", project_id,
         "--replication-policy=automatic"],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["gcloud", "secrets", "versions", "add", secret_name, "--project", project_id,
         "--data-file=-"],
        input=value, capture_output=True, text=True,
    )


def _secret_name_to_env_var(name: str) -> str:
    """Convert kebab-case secret name to UPPER_SNAKE env var name."""
    return name.upper().replace("-", "_")


def _get_tf_source_dir() -> Path:
    """Get the path to gapp's static Terraform files."""
    # Walk up from this file to find the repo root's terraform/ directory
    return Path(__file__).resolve().parent.parent.parent / "terraform"


def _build_tfvars(
    solution_name: str,
    project_id: str,
    image: str,
    service_config: dict,
    secrets: dict | None = None,
    auth_config: dict | None = None,
    env_vars: list[dict] | None = None,
    public: bool | None = None,
) -> dict:
    """Build the tfvars dict from manifest config."""
    from gapp.admin.sdk.manifest import resolve_env_vars

    bucket_name = f"gapp-{solution_name}-{project_id}"

    # Start with legacy service.env (dict format)
    env = dict(service_config.get("env", {}))

    # Resolve new-format env vars (list with {{VARIABLE}} substitution)
    if env_vars:
        gapp_vars = {
            "SOLUTION_DATA_PATH": "/mnt/data",
            "SOLUTION_NAME": solution_name,
        }
        resolved = resolve_env_vars(env_vars, gapp_vars)
        secret_env = {}
        for entry in resolved:
            name = entry["name"]
            secret_cfg = entry.get("secret")
            if secret_cfg:
                # Secret-backed — derive secret manager name from env var name
                secret_name = name.lower().replace("_", "-")
                secret_env[name] = secret_name
            elif "value" in entry:
                env[name] = entry["value"]

    # When auth enabled, set GAPP_APP so the wrapper knows what to import
    if auth_config:
        env["GAPP_APP"] = service_config["entrypoint"]

    # Merge legacy prerequisite secrets with new env-declared secrets
    all_secrets = {
        _secret_name_to_env_var(name): name
        for name in (secrets or {})
    }
    if env_vars:
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
        "public": bool(public) if public is not None else bool(auth_config),
        "auth_enabled": bool(auth_config),
    }
    return tfvars


def _get_staging_dir(solution_name: str) -> Path:
    """Get the staging directory for a solution's Terraform files."""
    cache_base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
    return Path(cache_base) / "gapp" / solution_name / "terraform"


def _stage_and_apply(
    solution_name: str,
    project_id: str,
    image: str,
    bucket_name: str,
    service_config: dict,
    secrets: dict | None = None,
    auth_config: dict | None = None,
    token: str = "",
    auto_approve: bool = False,
    manifest: dict | None = None,
) -> dict:
    """Copy static TF files to staging dir, write tfvars.json, and apply."""
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}

    # Stage: wipe and copy static TF files
    staging_dir = _get_staging_dir(solution_name)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    tf_source = _get_tf_source_dir()
    for tf_file in tf_source.glob("*.tf"):
        shutil.copy2(tf_file, staging_dir)

    # Write tfvars.json
    from gapp.admin.sdk.manifest import get_env_vars, get_public
    env_vars = get_env_vars(manifest or {})
    public = get_public(manifest or {})
    tfvars = _build_tfvars(
        solution_name, project_id, image, service_config, secrets, auth_config,
        env_vars=env_vars,
        public=public,
    )
    (staging_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    # Terraform init with GCS backend (upgrade ensures latest module versions)
    init_result = subprocess.run(
        ["terraform", "init",
         f"-backend-config=bucket={bucket_name}",
         "-backend-config=prefix=terraform/state",
         "-input=false",
         "-upgrade"],
        cwd=staging_dir,
        env=env,
        text=True,
    )
    if init_result.returncode != 0:
        raise RuntimeError("Terraform init failed. Check output above.")

    # Terraform apply
    apply_cmd = [
        "terraform", "apply",
        "-input=false",
    ]
    if auto_approve:
        apply_cmd.append("-auto-approve")

    apply_result = subprocess.run(
        apply_cmd,
        cwd=staging_dir,
        env=env,
        text=True,
    )
    if apply_result.returncode != 0:
        raise RuntimeError("Terraform apply failed. Check output above.")

    # Get service URL
    output_result = subprocess.run(
        ["terraform", "output", "-raw", "service_url"],
        cwd=staging_dir,
        env=env,
        capture_output=True,
        text=True,
    )

    return {
        "status": "applied",
        "service_url": output_result.stdout.strip() if output_result.returncode == 0 else None,
    }
