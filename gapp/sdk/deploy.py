"""gapp deploy — build container and terraform apply."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from gapp.sdk.context import resolve_solution
from gapp.sdk.manifest import (
    get_entrypoint,
    get_prerequisite_secrets,
    get_service_config,
    load_manifest,
)


def deploy_solution(auto_approve: bool = False) -> dict:
    """Deploy the current solution.

    Steps:
    1. Resolve solution context and load manifest
    2. Validate entrypoint and clean git tree
    3. Build container via Cloud Build (git archive, skip if image:sha exists)
    4. Stage static Terraform + write tfvars.json
    5. Terraform init with GCS backend + apply

    Returns dict describing what was done.
    """
    ctx = resolve_solution()
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
        raise RuntimeError("No repo path found for this solution.")

    repo_path = Path(repo_path)
    manifest = load_manifest(repo_path)
    entrypoint = get_entrypoint(manifest)

    if not entrypoint:
        raise RuntimeError(
            "No service entrypoint in gapp.yaml.\n"
            "  Add:\n"
            "    service:\n"
            "      entrypoint: your_package.mcp.server:mcp_app"
        )

    service_config = get_service_config(manifest)
    secrets = get_prerequisite_secrets(manifest)

    # Block if working tree is dirty
    head_sha = _get_head_sha(repo_path)
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
    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:{head_sha}"
    if _image_exists(project_id, region, solution_name, head_sha):
        result["build_status"] = "skipped"
    else:
        _build_and_push(project_id, repo_path, image, service_config["entrypoint"])
        result["build_status"] = "built"
    result["image"] = image

    # Stage Terraform and apply
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


def _get_head_sha(repo_path: Path) -> str:
    """Get short SHA of HEAD commit."""
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo_path,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get HEAD SHA. Is this a git repo with commits?")
    return result.stdout.strip()


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
    return Path(__file__).resolve().parent.parent / "templates" / name


def _build_and_push(
    project_id: str, repo_path: Path, image: str, entrypoint: str,
) -> None:
    """Build container via Cloud Build using git archive for source integrity.

    Extracts git archive HEAD to a temp dir, copies gapp's Dockerfile
    template into it, and submits to Cloud Build.
    """
    with tempfile.TemporaryDirectory(prefix="gapp-build-") as build_dir:
        # Extract committed source into temp dir
        archive = subprocess.Popen(
            ["git", "archive", "--format=tar", "HEAD"],
            stdout=subprocess.PIPE,
            cwd=repo_path,
        )
        subprocess.run(
            ["tar", "xf", "-", "-C", build_dir],
            stdin=archive.stdout,
            check=True,
        )
        archive.wait()

        # Copy gapp's Dockerfile and cloudbuild config
        shutil.copy2(_get_template("Dockerfile"), Path(build_dir) / "Dockerfile")
        shutil.copy2(_get_template("cloudbuild.yaml"), Path(build_dir) / "cloudbuild.yaml")

        # Submit to Cloud Build with substitutions for build arg
        result = subprocess.run(
            ["gcloud", "builds", "submit",
             "--config", f"{build_dir}/cloudbuild.yaml",
             "--substitutions", f"_ENTRYPOINT={entrypoint},_IMAGE={image}",
             "--project", project_id,
             build_dir],
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Cloud Build failed. Check the build logs above.")


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
) -> dict:
    """Build the tfvars dict from manifest config."""
    tfvars = {
        "project_id": project_id,
        "service_name": solution_name,
        "image": image,
        "memory": service_config["memory"],
        "cpu": service_config["cpu"],
        "max_instances": service_config["max_instances"],
        "public": service_config["public"],
        "env": service_config.get("env", {}),
        "secrets": {
            _secret_name_to_env_var(name): name
            for name in (secrets or {})
        },
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
    token: str = "",
    auto_approve: bool = False,
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
    tfvars = _build_tfvars(solution_name, project_id, image, service_config, secrets)
    (staging_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

    # Terraform init with GCS backend
    init_result = subprocess.run(
        ["terraform", "init",
         f"-backend-config=bucket={bucket_name}",
         "-backend-config=prefix=terraform/state",
         "-input=false"],
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
