"""GCP implementation of CloudProvider using gcloud and terraform CLI."""

import json
import os
import subprocess
from pathlib import Path
from typing import Optional, List, Dict

from gapp.admin.sdk.cloud.base import CloudProvider


class GCPProvider(CloudProvider):
    """Concrete provider that executes commands via local gcloud and terraform binaries."""

    def __init__(self, account: Optional[str] = None):
        self.account = account

    def _run_gcloud(self, args: List[str], **kwargs) -> subprocess.CompletedProcess:
        """Helper to run gcloud with optional account forcing."""
        cmd = ["gcloud"] + args
        env = kwargs.pop("env", os.environ.copy())
        if self.account:
            env["CLOUDSDK_CORE_ACCOUNT"] = self.account
        return subprocess.run(cmd, env=env, **kwargs)

    # -- GCP Foundation --

    def enable_api(self, project_id: str, api: str) -> None:
        self._run_gcloud(["services", "enable", api, "--project", project_id], capture_output=True, check=True)

    def bucket_exists(self, project_id: str, bucket_name: str) -> bool:
        res = self._run_gcloud(["storage", "buckets", "describe", f"gs://{bucket_name}", "--project", project_id], capture_output=True)
        return res.returncode == 0

    def create_bucket(self, project_id: str, bucket_name: str) -> None:
        self._run_gcloud(["storage", "buckets", "create", f"gs://{bucket_name}", "--project", project_id, "--location", "us", "--uniform-bucket-level-access"], capture_output=True, check=True)

    def ensure_build_permissions(self, project_id: str) -> None:
        resp = self._run_gcloud(["projects", "describe", project_id, "--format", "get(projectNumber)"], capture_output=True, text=True, check=True)
        project_number = resp.stdout.strip()
        build_domain = "developer.gserviceaccount.com"
        build_sa = f"{project_number}-compute@{build_domain}"
        for role in ["roles/storage.objectViewer", "roles/artifactregistry.writer"]:
            self._run_gcloud([
                "projects", "add-iam-policy-binding", project_id,
                "--member", f"serviceAccount:{build_sa}",
                "--role", role,
                "--condition=None"
            ], capture_output=True)

    def get_project_labels(self, project_id: str) -> Dict[str, str]:
        token = self.get_auth_token()
        # Fallback to direct API for label retrieval to ensure we get a fresh dictionary
        env = os.environ.copy()
        if self.account: env["CLOUDSDK_CORE_ACCOUNT"] = self.account
        res = subprocess.run(["curl", "-sf", "-H", f"Authorization: Bearer {token}", f"https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}"], capture_output=True, text=True, env=env)
        if res.returncode == 0:
            return json.loads(res.stdout).get("labels", {})
        return {}

    def set_project_labels(self, project_id: str, labels: Dict[str, str]) -> None:
        token = self.get_auth_token()
        env = os.environ.copy()
        if self.account: env["CLOUDSDK_CORE_ACCOUNT"] = self.account
        subprocess.run(["curl", "-sf", "-X", "PATCH", "-H", f"Authorization: Bearer {token}", "-H", "Content-Type: application/json", "-d", json.dumps({"labels": labels}), f"https://cloudresourcemanager.googleapis.com/v3/projects/{project_id}?updateMask=labels"], capture_output=True, env=env, check=True)

    def list_projects(self, filter_query: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        args = ["projects", "list", "--format", "json(projectId,labels)"]
        if filter_query:
            args.extend(["--filter", filter_query])
        if limit:
            args.extend(["--limit", str(limit)])
        
        res = self._run_gcloud(args, capture_output=True, text=True)
        if res.returncode == 0:
            return json.loads(res.stdout)
        return []

    # -- Cloud Build & Artifact Registry --

    def ensure_artifact_registry(self, project_id: str, region: str) -> None:
        self.enable_api(project_id, "artifactregistry.googleapis.com")
        check = self._run_gcloud(["artifacts", "repositories", "describe", "gapp", "--location", region, "--project", project_id], capture_output=True)
        if check.returncode != 0:
            self._run_gcloud(["artifacts", "repositories", "create", "gapp", "--repository-format", "docker", "--location", region, "--project", project_id], capture_output=True, check=True)

    def image_exists(self, project_id: str, region: str, solution_name: str, tag: str) -> bool:
        image_name = f"{region}-docker.pkg.dev/{project_id}/gapp/{solution_name}"
        res = self._run_gcloud(["artifacts", "docker", "images", "list", image_name, "--filter", f"tags:{tag}", "--format", "value(tags)", "--project", project_id], capture_output=True, text=True)
        return tag in res.stdout

    def submit_build_sync(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> None:
        self._run_gcloud([
            "builds", "submit", "--config", f"{build_dir}/cloudbuild.yaml",
            "--substitutions", f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
            "--project", project_id, str(build_dir)
        ], capture_output=False, check=True)

    def submit_build_async(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> str:
        res = self._run_gcloud([
            "builds", "submit", "--async", "--format=json",
            "--config", f"{build_dir}/cloudbuild.yaml",
            "--substitutions", f"_ENTRYPOINT={build_entrypoint},_IMAGE={image}",
            "--project", project_id, str(build_dir)
        ], capture_output=True, text=True, check=True)
        return json.loads(res.stdout)["id"]

    def check_build(self, project_id: str, build_id: str) -> Dict:
        res = self._run_gcloud(["builds", "describe", build_id, "--project", project_id, "--format=json"], capture_output=True, text=True, check=True)
        return json.loads(res.stdout)

    # -- Terraform --

    def apply_infrastructure(self, staging_dir: Path, bucket_name: str, state_prefix: str, auto_approve: bool, tfvars: Dict) -> Dict:
        token = self.get_auth_token()
        env = os.environ.copy()
        env["GOOGLE_OAUTH_ACCESS_TOKEN"] = token
        if self.account: env["CLOUDSDK_CORE_ACCOUNT"] = self.account

        (staging_dir / "terraform.tfvars.json").write_text(json.dumps(tfvars, indent=2))

        subprocess.run(["terraform", "init", f"-backend-config=bucket={bucket_name}", f"-backend-config=prefix={state_prefix}", "-input=false", "-upgrade"], cwd=staging_dir, env=env, check=True)
        
        apply_cmd = ["terraform", "apply", "-input=false"]
        if auto_approve: apply_cmd.append("-auto-approve")
        subprocess.run(apply_cmd, cwd=staging_dir, env=env, check=True)

        res = subprocess.run(["terraform", "output", "-json"], cwd=staging_dir, env=env, capture_output=True, text=True)
        outputs = json.loads(res.stdout) if res.returncode == 0 else {}
        return {k: v.get("value") for k, v in outputs.items()}

    def get_infrastructure_outputs(self, staging_dir: Path, bucket_name: str, state_prefix: str) -> Optional[Dict]:
        token = self.get_auth_token()
        env = os.environ.copy()
        env["GOOGLE_OAUTH_ACCESS_TOKEN"] = token
        if self.account: env["CLOUDSDK_CORE_ACCOUNT"] = self.account

        res = subprocess.run(["terraform", "init", f"-backend-config=bucket={bucket_name}", f"-backend-config=prefix={state_prefix}", "-input=false", "-upgrade"], cwd=staging_dir, env=env, capture_output=True)
        if res.returncode != 0: return None

        res = subprocess.run(["terraform", "output", "-json"], cwd=staging_dir, env=env, capture_output=True, text=True)
        if res.returncode != 0: return None
        raw = json.loads(res.stdout)
        return {k: v.get("value") for k, v in raw.items()}

    # -- Miscellaneous --

    def get_auth_token(self) -> str:
        res = self._run_gcloud(["auth", "print-access-token"], capture_output=True, text=True, check=True)
        return res.stdout.strip()

    def check_http_health(self, url: str) -> bool:
        res = subprocess.run(["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}", f"{url}/health"], capture_output=True, text=True, timeout=10)
        return res.stdout.strip() == "200"
