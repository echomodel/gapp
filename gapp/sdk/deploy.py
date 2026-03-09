"""gapp deploy — build container and terraform apply."""

import json
import os
import shutil
import subprocess
from importlib import resources
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
    2. Validate entrypoint is configured
    3. Generate Dockerfile from manifest
    4. Enable Artifact Registry + create repo
    5. Build and push container image via Cloud Build
    6. Stage static Terraform + write tfvars.json
    7. Terraform init with GCS backend + apply

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

    result = {
        "name": solution_name,
        "project_id": project_id,
        "image": None,
        "terraform_status": None,
        "service_url": None,
    }

    # Get access token for consistent identity across gcloud and terraform
    token = _get_access_token()

    # Ensure Artifact Registry repo exists
    region = "us-central1"
    _ensure_artifact_registry(project_id, region)

    # Generate Dockerfile and build
    image = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}:latest"
    _generate_and_build(project_id, repo_path, image, service_config)
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


def _generate_dockerfile(service_config: dict) -> str:
    """Generate a Dockerfile from the service config."""
    entrypoint = service_config["entrypoint"]
    port = service_config["port"]

    return (
        "FROM python:3.11-slim-bookworm\n"
        "\n"
        "WORKDIR /app\n"
        "\n"
        "RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*\n"
        "\n"
        "RUN curl -LsSf https://astral.sh/uv/install.sh | sh \\\n"
        "    && mv /root/.local/bin/uv /usr/local/bin/uv\n"
        "\n"
        "COPY . /app\n"
        "\n"
        'RUN uv pip install --system "mcp[cli]" uvicorn \\\n'
        "    && uv pip install --system -e .\n"
        "\n"
        f"EXPOSE {port}\n"
        "\n"
        f'CMD ["uvicorn", "{entrypoint}", "--host", "0.0.0.0", "--port", "{port}"]\n'
    )


def _generate_and_build(
    project_id: str, repo_path: Path, image: str, service_config: dict,
) -> None:
    """Generate Dockerfile and build via Cloud Build."""
    dockerfile_content = _generate_dockerfile(service_config)
    dockerfile_path = repo_path / "Dockerfile"

    # Write generated Dockerfile (Cloud Build needs it in the repo root)
    existing_dockerfile = None
    if dockerfile_path.exists():
        existing_dockerfile = dockerfile_path.read_text()

    try:
        dockerfile_path.write_text(dockerfile_content)

        result = subprocess.run(
            ["gcloud", "builds", "submit",
             "--tag", image,
             "--project", project_id],
            cwd=repo_path,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Cloud Build failed. Check the build logs above.")
    finally:
        # Restore original Dockerfile if there was one, otherwise clean up
        if existing_dockerfile is not None:
            dockerfile_path.write_text(existing_dockerfile)
        else:
            dockerfile_path.unlink(missing_ok=True)


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
