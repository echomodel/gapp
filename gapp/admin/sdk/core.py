"""Core SDK implementation for gapp."""

import os
import shutil
import tempfile
import time
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict

from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.cloud.base import CloudProvider
from gapp.admin.sdk.config import load_config, save_config, get_active_config
from gapp.admin.sdk.manifest import (
    get_solution_name, load_manifest, save_manifest, get_required_apis,
    get_domain, get_entrypoint, get_name, get_paths,
    get_prerequisite_secrets, get_service_config, resolve_env_vars, get_public, get_env_vars
)
from gapp.admin.sdk.models import StatusResult, DeploymentInfo, NextStep, ServiceStatus, DomainStatus


class GappSDK:
    """The central management unit for gapp solutions."""

    def __init__(self, provider: Optional[CloudProvider] = None):
        self.provider = provider or get_provider()

    # -- Configuration & Identity --

    def get_active_profile(self) -> str:
        return load_config().get("active", "default")

    def set_active_profile(self, name: str) -> None:
        config = load_config()
        name = name.strip().lower()
        config["active"] = name
        if name not in config["profiles"]:
            config["profiles"][name] = {"discovery": "on"}
        save_config(config)

    def get_owner(self) -> str | None:
        return get_active_config().get("owner")

    def set_owner(self, name: str | None) -> None:
        config = load_config()
        active = config["active"]
        profile = config["profiles"][active]
        profile["owner"] = name.strip().lower() if name else None
        save_config(config)

    def get_account(self) -> str | None:
        return get_active_config().get("account")

    def set_account(self, account: str | None) -> None:
        config = load_config()
        active = config["active"]
        profile = config["profiles"][active]
        profile["account"] = account.strip().lower() if account else None
        save_config(config)

    def is_discovery_on(self) -> bool:
        return get_active_config().get("discovery", "on") == "on"

    def set_discovery(self, state: str) -> None:
        state = state.strip().lower()
        if state not in ("on", "off"):
            raise ValueError("Discovery must be 'on' or 'off'.")
        config = load_config()
        active = config["active"]
        config["profiles"][active]["discovery"] = state
        save_config(config)

    # -- Naming Logic --

    def get_bucket_name(self, solution_name: str, project_id: str) -> str:
        """Bucket name is Environment-Blind. Isolation is handled by project_id."""
        owner = self._resolve_effective_owner(project_id, solution_name)
        parts = ["gapp"]
        if owner: parts.append(owner)
        parts.append(solution_name)
        parts.append(project_id)
        return "-".join(parts).lower()

    def get_label_key(self, solution_name: str, env: str = "default") -> str:
        """Label key includes the env suffix for unique identification."""
        owner = self.get_owner()
        parts = ["gapp", owner if owner else "", solution_name]
        if env != "default":
            parts.append(env)
        return "_".join(parts).lower()

    def get_label_value(self, env: str = "default") -> str:
        value = "v-2"
        if env != "default": value += f"_env-{env}"
        return value

    def get_role_key(self) -> str:
        owner = self.get_owner()
        return f"gapp-env_{owner}" if owner else "gapp-env"

    # -- Context Resolution --

    def resolve_solution(self, name: str | None = None) -> dict | None:
        if name:
            return {"name": name, "project_id": None, "repo_path": None}
        git_root = self._get_git_root()
        if git_root and (git_root / "gapp.yaml").is_file():
            manifest = load_manifest(git_root)
            solution_name = get_solution_name(manifest, git_root)
            return {"name": solution_name, "project_id": None, "repo_path": str(git_root)}
        return None

    def resolve_full_context(self, solution: str | None = None, env: str = "default") -> dict:
        ctx = self.resolve_solution(solution)
        if not ctx and solution:
            ctx = {"name": solution, "project_id": None, "repo_path": None}
        if not ctx:
            return {"name": None, "project_id": None, "repo_path": None, "github_repo": None}

        result = {**ctx, "github_repo": None, "owner": self.get_owner()}
        if not result.get("project_id") and self.is_discovery_on():
            pid = self.discover_project_from_label(result["name"], env=env)
            if not pid: pid = self._discover_project_from_role(env=env)
            result["project_id"] = pid
        return result

    def discover_project_from_label(self, solution_name: str, env: str = "default") -> Optional[str]:
        label_key = self.get_label_key(solution_name, env=env)
        label_value = self.get_label_value(env)
        projects = self.provider.list_projects(filter_query=f"labels.{label_key}={label_value}", limit=1)
        if projects: return projects[0]["projectId"]
        legacy_key = f"gapp-{solution_name}".replace("_", "-").lower()
        projects = self.provider.list_projects(filter_query=f"labels.{legacy_key}={env}", limit=1)
        if projects: return projects[0]["projectId"]
        return None

    def _discover_project_from_role(self, env: str = "default") -> Optional[str]:
        projects = self.provider.list_projects(filter_query=f"labels.{self.get_role_key()}={env}", limit=1)
        if projects: return projects[0]["projectId"]
        return None

    # -- Fleet Operations --

    def set_project_env(self, project_id: str, env: str = "default") -> str:
        key = self.get_role_key()
        labels = self.provider.get_project_labels(project_id)
        if labels.get(key) == env: return "exists"
        labels[key] = env
        self.provider.set_project_labels(project_id, labels)
        return "updated"

    def list_projects(self, wide: bool = False) -> dict:
        owner = self.get_owner()
        role_key = self.get_role_key()
        filter_query = f"labels.keys:gapp-env*" if wide else f"labels.keys:{role_key}"
        projects_data = self.provider.list_projects(filter_query=filter_query)
        projects = []
        for p in projects_data:
            roles = {k: v for k, v in p.get("labels", {}).items() if k.startswith("gapp-env")}
            if roles: projects.append({"id": p["projectId"], "roles": roles})
        return {"projects": sorted(projects, key=lambda x: x["id"]), "owner": owner, "mode": "all" if wide else "scoped"}

    # -- Infrastructure Operations --

    def setup(self, project_id: Optional[str] = None, solution: Optional[str] = None, env: str = "default") -> dict:
        ctx = self.resolve_solution(solution)
        if not ctx: raise RuntimeError("Not inside a gapp solution.")
        solution_name = ctx["name"]
        target_project = project_id
        if not target_project and self.is_discovery_on():
            target_project = self.discover_project_from_label(solution_name, env=env) or self._discover_project_from_role(env=env)
        if not target_project: raise RuntimeError("No project specified or discovered.")

        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
        manifest = load_manifest(repo_path) if repo_path else {}
        for api in ["run.googleapis.com", "secretmanager.googleapis.com", "artifactregistry.googleapis.com", "cloudbuild.googleapis.com"] + get_required_apis(manifest):
            self.provider.enable_api(target_project, api)

        bucket_name = self.get_bucket_name(solution_name, target_project)
        bucket_status = "exists" if self.provider.bucket_exists(target_project, bucket_name) else "created"
        if bucket_status == "created": self.provider.create_bucket(target_project, bucket_name)

        self.provider.ensure_build_permissions(target_project)
        label_key, label_value = self.get_label_key(solution_name, env=env), self.get_label_value(env)
        labels = self.provider.get_project_labels(target_project)
        if labels.get(label_key) != label_value:
            labels[label_key] = label_value
            self.provider.set_project_labels(target_project, labels)
            label_status = "added"
        else: label_status = "exists"
        return {"name": solution_name, "project_id": target_project, "env": env, "bucket": bucket_name, "bucket_status": bucket_status, "label_status": label_status}

    def deploy(self, ref: Optional[str] = None, solution: Optional[str] = None, env: str = "default", dry_run: bool = False, project_id: Optional[str] = None) -> dict:
        ctx = self.resolve_full_context(solution, env=env)
        solution_name, target_project = ctx["name"], project_id or ctx["project_id"]
        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
        if not solution_name: raise RuntimeError("Could not determine solution name.")
        
        preview = {"name": solution_name, "owner": self.get_owner(), "env": env, "project_id": target_project, "label": self.get_label_key(solution_name, env=env), "bucket": self.get_bucket_name(solution_name, target_project) if target_project else None, "repo_path": str(repo_path) if repo_path else None, "status": "ready" if target_project and repo_path else "pending_setup", "services": []}
        if repo_path:
            manifest = load_manifest(repo_path)
            if paths := get_paths(manifest):
                for p in paths:
                    sub_m = load_manifest(repo_path / p) if (repo_path / p).is_dir() else {}
                    preview["services"].append({"name": get_name(sub_m) or f"{solution_name}-{p.replace('/', '-')}", "path": p})
            else: preview["services"].append({"name": solution_name, "path": "."})

        if dry_run: return {**preview, "dry_run": True}
        if not target_project: raise RuntimeError(f"No GCP project resolved for '{solution_name}'.")

        # Confirm Environment Safety
        labels = self.provider.get_project_labels(target_project)
        label_key = self.get_label_key(solution_name, env=env)
        label_value = self.get_label_value(env)
        if labels.get(label_key) != label_value:
            legacy_key = f"gapp-{solution_name}".replace("_", "-").lower()
            if labels.get(legacy_key) != env:
                raise RuntimeError(f"Project '{target_project}' is not designated for environment '{env}'.")

        bucket_name = self.get_bucket_name(solution_name, target_project)
        if not self.provider.bucket_exists(target_project, bucket_name): raise RuntimeError(f"Foundation missing. Run 'gapp setup'")
        
        if paths := get_paths(load_manifest(repo_path)):
            return {"services": [self._deploy_single_service(s["name"], target_project, repo_path, load_manifest(repo_path / s["path"]), service_path=s["path"], env=env, parent_solution=solution_name) for s in preview["services"]]}
        return self._deploy_single_service(solution_name, target_project, repo_path, load_manifest(repo_path), env=env)

    def status(self, name: str | None = None, env: str = "default") -> StatusResult:
        ctx = self.resolve_full_context(name, env=env)
        if not ctx["name"]: return StatusResult(initialized=False, next_step=NextStep(action="init"))
        solution_name, project_id, repo_path = ctx["name"], ctx.get("project_id"), ctx.get("repo_path")
        result = StatusResult(initialized=True, name=solution_name, repo_path=repo_path, deployment=DeploymentInfo(project=project_id, pending=True))
        if not project_id:
            result.next_step = NextStep(action="setup", hint=f"No GCP project attached for '{solution_name}' in '{env}'.")
            return result
        services_to_check = []
        if repo_path:
            manifest = load_manifest(Path(repo_path))
            if paths := get_paths(manifest):
                for p in paths:
                    sub_m = load_manifest(Path(repo_path) / p) if (Path(repo_path) / p).is_dir() else {}
                    services_to_check.append({"name": get_name(sub_m) or f"{solution_name}-{p.replace('/', '-')}", "is_workspace": True})
            else: services_to_check.append({"name": solution_name, "is_workspace": False})
        else: services_to_check.append({"name": solution_name, "is_workspace": False})
        for svc in services_to_check:
            bucket_name = self.get_bucket_name(solution_name, project_id)
            state_prefix = f"terraform/state/{svc['name']}" if svc["is_workspace"] else "terraform/state"
            outputs = self.provider.get_infrastructure_outputs(_get_staging_dir(svc["name"]), bucket_name, state_prefix)
            if outputs and (url := outputs.get("service_url")):
                result.deployment.services.append(ServiceStatus(name=svc["name"], url=url, healthy=self.provider.check_http_health(url)))
                result.deployment.pending = False
        return result

    def list(self, wide: bool = False, project_limit: int = 50) -> dict:
        owner = self.get_owner()
        label_filter = f"labels.keys:gapp_{owner}_*" if not wide and owner else "labels.keys:gapp-*,labels.keys:gapp_*"
        projects_data = self.provider.list_projects(filter_query=label_filter, limit=project_limit)
        gapp_projects = []
        total_solutions = 0
        is_global = not wide and not owner
        for project in projects_data:
            solutions = []
            for key, value in project.get("labels", {}).items():
                if not key.startswith("gapp"): continue
                if key.startswith("gapp_"):
                    parts = key.split("_")
                    l_owner, l_name = parts[1] if parts[1] else None, "_".join(parts[2:])
                    if (is_global and l_owner is None) or (not wide and owner and l_owner == owner) or wide:
                        solutions.append({"name": l_name, "instance": value, "label": key})
                        total_solutions += 1
                elif key.startswith("gapp-") and (is_global or wide):
                    solutions.append({"name": key[len("gapp-"):], "instance": value, "label": key})
                    total_solutions += 1
            if solutions: gapp_projects.append({"id": project["projectId"], "solutions": sorted(solutions, key=lambda s: s["name"])})
        return {"projects": gapp_projects, "total_projects": len(gapp_projects), "total_solutions": total_solutions, "limit_reached": len(projects_data) >= project_limit, "filter_mode": "all" if wide else (f"owner:{owner}" if owner else "global")}

    # -- Internal Helpers --

    def _resolve_effective_owner(self, project_id: str, solution_name: str) -> Optional[str]:
        labels = self.provider.get_project_labels(project_id)
        owner = self.get_owner()
        if owner and f"gapp_{owner}_{solution_name}" in labels: return owner
        if f"gapp__{solution_name}" in labels or f"gapp-{solution_name}".replace("_", "-").lower() in labels: return None
        return owner

    def _deploy_single_service(self, name, project_id, repo_path, manifest, service_path=".", env="default", parent_solution=None):
        service_root = repo_path / service_path
        entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)
        sha = self._resolve_ref(repo_path, "HEAD")
        self.provider.ensure_artifact_registry(project_id, "us-central1")
        image = f"us-central1-docker.pkg.dev/{project_id}/gapp/{name}:{sha}"
        if not self.provider.image_exists(project_id, "us-central1", name, sha):
            build_dir, build_ep = _prepare_build_dir(repo_path, image, entrypoint)
            try: self.provider.submit_build_sync(project_id, Path(build_dir), image, build_ep)
            finally: shutil.rmtree(build_dir, ignore_errors=True)

        bucket_name = self.get_bucket_name(parent_solution or name, project_id)
        state_prefix = f"terraform/state/{name}" if parent_solution else "terraform/state"
        
        # Build tfvars ensuring custom_domain is null if empty
        tfvars = _build_tfvars(
            name, project_id, image, 
            get_service_config(manifest), 
            get_prerequisite_secrets(manifest), 
            Path(repo_path) / service_path, 
            get_public(manifest), 
            get_domain(manifest)
        )
        
        outputs = self.provider.apply_infrastructure(staging_dir=_get_staging_dir(name), bucket_name=bucket_name, state_prefix=state_prefix, auto_approve=True, tfvars=tfvars)
        return {"name": name, "project_id": project_id, "image": image, "terraform_status": "applied", "service_url": outputs.get("service_url"), "env": env}

    def _get_git_root(self) -> Optional[Path]:
        try:
            res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if res.returncode == 0: return Path(res.stdout.strip())
        except Exception: pass
        return None

    def _resolve_ref(self, path, ref):
        return subprocess.run(["git", "rev-parse", "--short=12", ref], capture_output=True, text=True, cwd=path, check=True).stdout.strip()

def _resolve_entrypoint(manifest, root, repo):
    ep, cmd = manifest.get("service", {}).get("entrypoint"), manifest.get("service", {}).get("cmd")
    if ep: return ep, "explicit"
    if cmd: return f"__cmd__:{cmd}", "cmd"
    if (root / "Dockerfile").exists(): return "__dockerfile__", "dockerfile"
    return "__mcp_app__", "mcp-app"

def _prepare_build_dir(path, image, ep):
    d = tempfile.mkdtemp(prefix="gapp-build-")
    subprocess.run(["tar", "xf", "-", "-C", d], stdin=subprocess.Popen(["git", "archive", "--format=tar", "HEAD"], stdout=subprocess.PIPE, cwd=path).stdout, check=True)
    t = Path(__file__).resolve().parent.parent.parent / "templates"
    shutil.copy2(t / "cloudbuild.yaml", Path(d) / "cloudbuild.yaml")
    if ep != "__dockerfile__": shutil.copy2(t / "Dockerfile", Path(d) / "Dockerfile")
    return d, ep

def _build_tfvars(name, pid, img, cfg, secrets, repo_path, public, domain):
    from gapp.admin.sdk.manifest import resolve_env_vars, get_env_vars, load_manifest
    env = dict(cfg.get("env", {}))
    # Correct env var resolution by loading manifest from the specific repo_path
    manifest = load_manifest(repo_path)
    env_vars = get_env_vars(manifest)
    if env_vars:
        for e in resolve_env_vars(env_vars, {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": name}):
            if "value" in e: env[e["name"]] = e["value"]
    
    # Ensure domain is null if empty string or None to avoid Terraform validation errors
    custom_domain = domain if domain and domain.strip() else None
    
    return {"project_id": pid, "service_name": name, "image": img, "memory": cfg["memory"], "cpu": cfg["cpu"], "max_instances": cfg["max_instances"], "env": env, "secrets": {n.upper().replace("-", "_"): n for n in (secrets or {})}, "public": bool(public), "custom_domain": custom_domain}

def _get_staging_dir(name):
    return Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "gapp" / name / "terraform"
