"""gapp deploy — build container and terraform apply."""

import os
import subprocess
import tempfile
from pathlib import Path

from gapp.sdk.context import resolve_solution
from gapp.sdk.manifest import get_entrypoint, get_service_config, load_manifest


def deploy_solution(auto_approve: bool = False) -> dict:
    """Deploy the current solution.

    Steps:
    1. Resolve solution context and load manifest
    2. Validate entrypoint is configured
    3. Generate Dockerfile from manifest
    4. Enable Artifact Registry + create repo
    5. Build and push container image via Cloud Build
    6. Generate Terraform config from manifest
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
            "No service entrypoint in deploy/manifest.yaml.\n"
            "  Add:\n"
            "    service:\n"
            "      entrypoint: your_package.mcp.server:mcp_app"
        )

    service_config = get_service_config(manifest)

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

    # Generate Terraform and apply
    bucket_name = f"gapp-{solution_name}-{project_id}"
    tf_result = _generate_and_apply(
        solution_name=solution_name,
        project_id=project_id,
        image=image,
        bucket_name=bucket_name,
        service_config=service_config,
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


def _generate_terraform(
    solution_name: str,
    project_id: str,
    image: str,
    service_config: dict,
) -> str:
    """Generate Terraform configuration from the service config."""
    env_block = ""
    if service_config.get("env"):
        env_entries = "\n".join(
            f'    {k} = "{v}"' for k, v in service_config["env"].items()
        )
        env_block = f"\n  env = {{\n{env_entries}\n  }}\n"

    return (
        'terraform {\n'
        '  required_providers {\n'
        '    google = {\n'
        '      source  = "hashicorp/google"\n'
        '      version = ">= 5.0"\n'
        '    }\n'
        '  }\n'
        '  backend "gcs" {}\n'
        '}\n'
        '\n'
        'module "service" {\n'
        f'  source       = "github.com/krisrowe/gapp//modules/cloud-run-service"\n'
        f'  project_id   = "{project_id}"\n'
        f'  service_name = "{solution_name}"\n'
        f'  image        = "{image}"\n'
        f'  memory       = "{service_config["memory"]}"\n'
        f'  cpu          = "{service_config["cpu"]}"\n'
        f'  max_instances = {service_config["max_instances"]}\n'
        f'  public       = {str(service_config["public"]).lower()}\n'
        f'{env_block}'
        '}\n'
        '\n'
        'output "service_url" {\n'
        '  value = module.service.service_url\n'
        '}\n'
    )


def _generate_and_apply(
    solution_name: str,
    project_id: str,
    image: str,
    bucket_name: str,
    service_config: dict,
    token: str,
    auto_approve: bool = False,
) -> dict:
    """Generate Terraform config in a temp dir and apply."""
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}
    tf_content = _generate_terraform(solution_name, project_id, image, service_config)

    with tempfile.TemporaryDirectory(prefix="gapp-tf-") as tf_dir:
        tf_path = Path(tf_dir) / "main.tf"
        tf_path.write_text(tf_content)

        # Terraform init with GCS backend
        init_result = subprocess.run(
            ["terraform", "init",
             f"-backend-config=bucket={bucket_name}",
             "-backend-config=prefix=terraform/state",
             "-input=false"],
            cwd=tf_dir,
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
            cwd=tf_dir,
            env=env,
            text=True,
        )
        if apply_result.returncode != 0:
            raise RuntimeError("Terraform apply failed. Check output above.")

        # Get service URL
        output_result = subprocess.run(
            ["terraform", "output", "-raw", "service_url"],
            cwd=tf_dir,
            env=env,
            capture_output=True,
            text=True,
        )

        return {
            "status": "applied",
            "service_url": output_result.stdout.strip() if output_result.returncode == 0 else None,
        }
