"""Core SDK implementation for gapp.

The label / identity / env model:

  Project labels (written only by an Owner: `gapp setup` on a workstation,
  or `gapp ci project setup`):
    gapp=hosted                    the project hosts gapp deployments
    gapp-env=<env>                 optional env binding (gapp projects set-env)

  Deployment labels (written by every setup and deploy, in both modes, on
  the deployment's own resources: its bucket, by gapp, and its Cloud Run
  services, by Terraform):
    gapp-deployment=<id>
    gapp-version=<contract major>

No per-deployment project labels. A deployment is found by its project
(--project, or the fleet entry in CI) or by searching the projects marked
gapp=hosted for its bucket. The version guard reads gapp-version from the
deployment's services. Env is a project property, set/changed only by
gapp_projects_set_env. Setup and deploy never write gapp-env.

See CONTRIBUTING.md for the full model.
"""

import os
import shutil
import tempfile
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from pathlib import Path
from typing import Optional, List, Dict

from gapp import __version__
from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.cloud.base import CloudProvider, CloudAccessError, ci_identity_hint, on_ci_runner
from gapp.admin.sdk.config import load_config, save_config, get_active_config
from gapp.admin.sdk.manifest import (
    get_solution_name, load_manifest, save_manifest, get_required_apis,
    get_domain, get_entrypoint, get_name, get_paths,
    get_prerequisite_secrets, get_service_config, resolve_env_vars, get_public, get_env_vars
)
from gapp.admin.sdk.models import StatusResult, DeploymentInfo, NextStep, ServiceStatus, DomainStatus

CURRENT_MAJOR = int(__version__.split(".")[0])

# The project label that marks a project as hosting gapp deployments. Only an
# Owner writes it (`gapp setup`, `gapp ci project setup`); a CI run never does.
HOSTED_LABEL = "gapp"
HOSTED_VALUE = "hosted"

# Labels every setup and deploy writes on the deployment's own resources
# (its bucket and its Cloud Run services).
DEPLOYMENT_LABEL = "gapp-deployment"
VERSION_LABEL = "gapp-version"

# How many projects the search for a deployment asks at once.
SEARCH_WORKERS = 8

# The single project-level label that binds a project to a named env.
# Env is project-wide.
PROJECT_ENV_LABEL = "gapp-env"

# String forbidden as a value for --env. "default" was a v-2 magic value
# meaning "no env"; in v-3 the absence of a gapp-env label is its own state
# ("undefined"), and --env requires an actually-named value.
RESERVED_ENV_NAMES = frozenset({"default"})

# Display literal for projects that have no gapp-env label.
UNDEFINED_ENV_DISPLAY = "<undefined>"


# Region every gapp service, image, and domain mapping lives in.
REGION = "us-central1"


def _validate_env_name(env: Optional[str]) -> Optional[str]:
    """Normalize and validate a user-supplied --env value.

    Returns None for None or empty (caller treats as "no env preference").
    Raises ValueError for "default" or other reserved names.
    """
    if env is None:
        return None
    e = env.strip()
    if not e:
        return None
    if e.lower() in RESERVED_ENV_NAMES:
        raise ValueError(
            f"--env value '{e}' is reserved. Omit --env to operate without "
            f"an env preference, or use a different name."
        )
    return e.lower()


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
        """Bucket name is environment-blind.

        The project is the resource boundary: a deployment id is unique per
        project, and teams that must not share deployments use separate
        projects (IAM) or separate fleets.
        """
        return f"gapp-{solution_name}-{project_id}".lower()

    def deployment_labels(self, deployment: str) -> Dict[str, str]:
        """The labels every setup and deploy writes on a deployment's bucket and services."""
        return {DEPLOYMENT_LABEL: deployment.lower(), VERSION_LABEL: str(CURRENT_MAJOR)}

    def _hosted_projects(self, limit: Optional[int] = None) -> List[Dict]:
        """Projects marked gapp=hosted, with their labels."""
        return self.provider.list_projects(filter_query=f"labels.{HOSTED_LABEL}={HOSTED_VALUE}", limit=limit)

    def _deployments_on(self, project_id: str) -> Dict[str, Dict[str, str]]:
        """The labels of every deployment bucket on a project, by bucket name."""
        return {name: labels for name, labels in self.provider.list_bucket_labels(project_id).items()
                if labels.get(DEPLOYMENT_LABEL)}

    def _search_hosted(self, projects: List[Dict], match, first_only: bool = False) -> List[Dict]:
        """Ask each hosted project for its deployment buckets, in parallel.

        `match(project, bucket_labels)` returns a result dict or None. With
        first_only, returns as soon as one project answers with a match.
        """
        if not projects:
            return []

        def ask(project):
            found = []
            for labels in self._deployments_on(project["projectId"]).values():
                if (hit := match(project, labels)) is not None:
                    found.append(hit)
            return found

        results: List[Dict] = []
        with ThreadPoolExecutor(max_workers=min(SEARCH_WORKERS, len(projects))) as pool:
            futures = [pool.submit(ask, p) for p in projects]
            for future in as_completed(futures):
                results.extend(future.result())
                if first_only and results:
                    for f in futures:
                        f.cancel()
                    break
        return results

    def _service_names(self, deployment: str, repo_path: Optional[Path], strict: bool = True) -> List[str]:
        """The Cloud Run service names a deployment's manifest produces."""
        if not repo_path:
            return [deployment]
        manifest = load_manifest(repo_path, strict=strict)
        if paths := get_paths(manifest):
            names = []
            for p in paths:
                sub_m = load_manifest(repo_path / p, strict=strict) if (repo_path / p).is_dir() else {}
                names.append(get_name(sub_m) or f"{deployment}-{p.replace('/', '-')}")
            return names
        return [deployment]

    def _check_version(self, project_id: str, deployment: str, service_names: List[str]) -> None:
        """Refuse when a newer gapp deployed this deployment (its services' gapp-version)."""
        for svc in service_names:
            labels = self.provider.get_service_labels(project_id, REGION, svc) or {}
            try:
                n = int(labels.get(VERSION_LABEL, ""))
            except ValueError:
                continue
            if n > CURRENT_MAJOR:
                raise RuntimeError(
                    f"Deployment '{deployment}' on project '{project_id}' was deployed by gapp {n}.x "
                    f"(service label {VERSION_LABEL}={n}); this gapp is v{__version__}. "
                    f"Upgrade gapp to deploy it."
                )

    # -- Project env label operations --

    def read_project_env(self, project_id: str) -> Optional[str]:
        """Return the project's bound env, or None if unset (undefined)."""
        labels = self.provider.get_project_labels(project_id)
        return labels.get(PROJECT_ENV_LABEL) or None

    def set_project_env(self, project_id: str, env: str, force: bool = False) -> dict:
        """Write/update gapp-env on a project.

        - No existing label: stamp it.
        - Existing matches target: no-op.
        - Existing differs: refuse without force=True.
        - With force=True: cross-check fleet for new corruption that the
          rebind would create (same (deployment, target-env) on
          another project). If detected, refuse anyway — manual cleanup.
        """
        env_norm = _validate_env_name(env)
        if env_norm is None:
            raise ValueError(
                "set_project_env requires a non-empty named env. "
                "To unset, use clear_project_env."
            )

        labels = self.provider.get_project_labels(project_id)
        existing = labels.get(PROJECT_ENV_LABEL)

        if existing == env_norm:
            return {"project_id": project_id, "env": env_norm, "status": "exists"}

        if existing and not force:
            solution_count = len(self._deployments_on(project_id))
            raise RuntimeError(
                f"Project '{project_id}' is already bound to env='{existing}'. "
                f"Refusing to change to env='{env_norm}' without force=True. "
                f"({solution_count} solution(s) on this project would be reclassified.)"
            )

        # On force-overwrite, check for cross-project duplicates the rebind would create.
        if existing and force:
            self._check_rebind_duplicates(project_id, env_norm)

        labels[PROJECT_ENV_LABEL] = env_norm
        self.provider.set_project_labels(project_id, labels)
        return {
            "project_id": project_id,
            "env": env_norm,
            "status": "updated" if existing else "added",
            "previous": existing,
        }

    def clear_project_env(self, project_id: str) -> dict:
        """Remove gapp-env from a project. Project becomes undefined-env."""
        labels = self.provider.get_project_labels(project_id)
        existing = labels.get(PROJECT_ENV_LABEL)
        if not existing:
            return {"project_id": project_id, "status": "absent"}
        del labels[PROJECT_ENV_LABEL]
        self.provider.set_project_labels(project_id, labels)
        return {"project_id": project_id, "status": "removed", "previous": existing}

    def _check_rebind_duplicates(self, project_id: str, target_env: str) -> None:
        """When set_project_env is forcing an env rebind, refuse if any
        deployment on this project would end up on >1 project under target_env.
        """
        here = {l[DEPLOYMENT_LABEL] for l in self._deployments_on(project_id).values()}
        if not here:
            return
        others = [p for p in self._hosted_projects()
                  if p["projectId"] != project_id and (p.get("labels") or {}).get(PROJECT_ENV_LABEL) == target_env]

        def match(project, labels):
            dep = labels[DEPLOYMENT_LABEL]
            return {"project": project["projectId"], "deployment": dep} if dep in here else None

        for hit in self._search_hosted(others, match, first_only=True):
            other, dep = hit["project"], hit["deployment"]
            raise RuntimeError(
                f"Refusing rebind: deployment '{dep}' also exists on project '{other}' which is "
                f"already bound to env='{target_env}'. Setting '{project_id}' to env='{target_env}' "
                f"would create cross-project duplication. Resolve manually: remove the "
                f"{DEPLOYMENT_LABEL} label from the stray copy's bucket (gcloud storage buckets update "
                f"gs://{self.get_bucket_name(dep, other)} --remove-labels={DEPLOYMENT_LABEL})."
            )

    # -- Solution & project resolution --

    def resolve_solution(self, name: str | None = None, strict: bool = True) -> dict | None:
        """Resolve a solution context.

        With an explicit name, returns a no-repo context immediately —
        no filesystem access, no manifest validation. This is the path
        used by status/list when called with --solution.

        Without a name, falls back to git-root + gapp.yaml discovery.
        strict controls whether the manifest is schema-validated; pass
        strict=False from read-only commands so a stale gapp.yaml does
        not block cloud reads.

        NOTE: project_id in the returned dict is ALWAYS None. This method
        intentionally returns shape only — it does not perform GCP-side
        project discovery. Callers that need the resolved GCP project must
        chain `resolve_project_for_solution(...)` (see `status()`) or use
        the `resolve_solution_with_project(...)` helper which does both
        in one call. See issue #39 for the trap this contract created
        when callers assumed `project_id` would be populated.
        """
        if name:
            return {"name": name, "project_id": None, "repo_path": None}
        git_root = self._get_git_root()
        if git_root and (git_root / "gapp.yaml").is_file():
            manifest = load_manifest(git_root, strict=strict)
            solution_name = get_solution_name(manifest, git_root)
            return {"name": solution_name, "project_id": None, "repo_path": str(git_root)}
        return None

    def resolve_solution_with_project(
        self,
        name: str | None = None,
        env: Optional[str] = None,
        project: Optional[str] = None,
        strict: bool = True,
        first_only: bool = False,
    ) -> dict | None:
        """Resolve a solution AND best-effort attach its GCP project_id.

        Returns the same dict as `resolve_solution(name, strict=strict)`
        with `project_id` populated when discoverable, or left as None
        when no project currently hosts the solution. Callers that
        require a project must check `project_id` and raise their own
        error; this method never raises for "no project found" so that
        list-style callers can degrade gracefully.

        This is the canonical pattern for SDK consumers that need both
        the local manifest context and the deployed GCP project. See
        issue #39 — `resolve_solution` alone always returns
        project_id=None, which trapped every caller in `secrets.py`.
        """
        ctx = self.resolve_solution(name, strict=strict)
        if not ctx:
            return None
        try:
            res = self.resolve_project_for_solution(
                ctx["name"], env=env, project=project, first_only=first_only,
            )
            ctx["project_id"] = res["project_id"]
        except CloudAccessError:
            # An expired login or a wrong account is not "no project";
            # let the caller report the real error.
            raise
        except RuntimeError:
            ctx["project_id"] = None
        return ctx

    def discover_projects_for_solution(self, solution_name: str, env: Optional[str] = None,
                                       first_only: bool = False) -> List[Dict]:
        """Find the projects hosting a deployment.

        Searches the projects marked gapp=hosted for the deployment's bucket
        (labeled gapp-deployment=<id>), asking the projects in parallel.
        With env, only projects bound to that env are asked. With
        first_only, stops at the first match.

        Returns a list of dicts: {projectId, env (str|None), labels}, where
        labels are the project's and env is its gapp-env (None if undefined).
        """
        deployment = solution_name.lower()
        projects = self._hosted_projects()
        if env is not None:
            projects = [p for p in projects if (p.get("labels") or {}).get(PROJECT_ENV_LABEL) == env]

        def match(project, labels):
            if labels.get(DEPLOYMENT_LABEL) != deployment:
                return None
            plabels = project.get("labels") or {}
            return {"projectId": project["projectId"], "env": plabels.get(PROJECT_ENV_LABEL) or None,
                    "labels": plabels}

        found = self._search_hosted(projects, match, first_only=first_only)
        return sorted(found, key=lambda c: c["projectId"])

    def resolve_project_for_solution(
        self,
        solution_name: str,
        env: Optional[str] = None,
        project: Optional[str] = None,
        allow_zero: bool = False,
        first_only: bool = False,
    ) -> Dict:
        """Single-source resolver.

        Inputs are caller-provided.
        Returns a dict with: project_id, env, labels (the project's), status.

        An explicit project is taken as given (no search): only its env is
        checked. Whether the deployment is set up there is the caller's
        check (its bucket). Without a project, the hosted projects are
        searched; first_only stops at the first match (status).

        Raises RuntimeError on any unresolvable case.
        """
        env_norm = _validate_env_name(env)

        # Explicit project: no search; verify the env.
        if project:
            labels = self.provider.get_project_labels(project)
            project_env = labels.get(PROJECT_ENV_LABEL) or None
            if env_norm is not None and env_norm != project_env:
                raise RuntimeError(
                    f"Project '{project}' is bound to env="
                    f"'{project_env or UNDEFINED_ENV_DISPLAY}'. "
                    f"You passed --env='{env_norm}'. Pick one."
                )
            return {
                "project_id": project,
                "env": project_env,
                "labels": labels,
                "status": "resolved",
            }

        # Discovery path: search the hosted projects.
        if not self.is_discovery_on():
            raise RuntimeError(
                "Discovery is OFF for this profile. Pass --project explicitly "
                "or run `gapp config discovery on`."
            )

        # Status stops at the first match, so it asks only the projects of
        # the env it was given; a full search keeps every match so that an
        # ambiguous name is refused rather than guessed.
        candidates = self.discover_projects_for_solution(
            solution_name, env=env_norm if first_only else None, first_only=first_only,
        )
        if not candidates:
            if allow_zero:
                raise RuntimeError(
                    f"Solution '{solution_name}' is not deployed anywhere. "
                    f"Pass --project to install for the first time."
                )
            raise RuntimeError(
                f"No deployment '{solution_name}' found in any project marked {HOSTED_LABEL}={HOSTED_VALUE}. "
                f"Pass --project, or run `gapp setup --project <project-id>` first."
            )

        # Filter by --env if given.
        if env_norm is not None:
            filtered = [c for c in candidates if c["env"] == env_norm]
            if not filtered:
                envs = sorted({(c["env"] or UNDEFINED_ENV_DISPLAY) for c in candidates})
                raise RuntimeError(
                    f"Solution '{solution_name}' not found in env='{env_norm}'. "
                    f"Found in env(s): {', '.join(envs)}."
                )
            candidates = filtered

        if len(candidates) == 1:
            c = candidates[0]
            return {
                "project_id": c["projectId"],
                "env": c["env"],
                "labels": c["labels"],
                "status": "resolved",
            }

        # 2+ matches still — corruption (--env-narrowed) or ambiguity.
        rows = "\n  ".join(
            f"{c['projectId']} (env={c['env'] or UNDEFINED_ENV_DISPLAY})"
            for c in candidates
        )
        if env_norm is not None:
            raise RuntimeError(
                f"Deployment '{solution_name}' exists on multiple projects "
                f"under env='{env_norm}' — corruption requires manual "
                f"cleanup:\n  {rows}\n"
                f"Use `gcloud storage buckets update gs://gapp-{solution_name}-<pid> "
                f"--remove-labels={DEPLOYMENT_LABEL}` on whichever "
                f"project should not own this deployment."
            )
        raise RuntimeError(
            f"Deployment '{solution_name}' exists on multiple projects:\n"
            f"  {rows}\nPass --env or --project to disambiguate."
        )

    # -- Fleet listing --

    def list_target_projects(self) -> dict:
        """List GCP projects with a gapp-env label.

        No filtering knob: gapp-env is a single project-wide label.
        """
        projects_data = self.provider.list_projects(
            filter_query=f"labels:{PROJECT_ENV_LABEL}"
        )
        projects = []
        for p in projects_data:
            labels = p.get("labels", {})
            projects.append({
                "id": p["projectId"],
                "env": labels.get(PROJECT_ENV_LABEL),
            })
        return {"projects": sorted(projects, key=lambda x: x["id"])}

    def list_apps(self, project_limit: int = 50) -> dict:
        """List deployments on the projects marked gapp=hosted.

        Args:
            project_limit: Max hosted projects to scan in one call.

        Returns one entry per (project x deployment), from the deployment
        buckets' labels (gapp-deployment, gapp-version), asked of the hosted
        projects in parallel. Env comes from the project's gapp-env label (or
        None when the project has no env binding).
        """
        projects_data = self._hosted_projects(limit=project_limit)

        def match(project, labels):
            try:
                major = int(labels.get(VERSION_LABEL, ""))
            except ValueError:
                major = None
            return {
                "name": labels[DEPLOYMENT_LABEL],
                "project": project["projectId"],
                "env": (project.get("labels") or {}).get(PROJECT_ENV_LABEL) or None,
                "contract_major": major,
                "is_legacy": False,
            }

        apps = self._search_hosted(projects_data, match)

        # Detect cross-project duplicates (same name+named env on >1 project).
        dup_keys: Dict[tuple, list] = {}
        for app in apps:
            if app["env"] is None:
                continue  # undefined env on multiple projects is not corruption
            k = (app["name"], app["env"])
            dup_keys.setdefault(k, []).append(app["project"])
        for app in apps:
            if app["env"] is None:
                app["duplicate"] = False
                continue
            app["duplicate"] = len(dup_keys[(app["name"], app["env"])]) > 1

        result = {
            "apps": sorted(apps, key=lambda x: (x["name"], x["env"] or "", x["project"])),
            "metadata": {
                "projects": {"count": len(projects_data), "limit": project_limit},
                "apps": {"count": len(apps)},
                "contract_major": CURRENT_MAJOR,
            },
            "messages": [],
            "warnings": [],
        }
        if len(projects_data) >= project_limit:
            result["warnings"].append(
                f"Project list limit reached ({project_limit}). "
                f"Use --project-limit to increase."
            )
        if any(a.get("duplicate") for a in apps):
            result["warnings"].append(
                "Same-env duplicates detected — see 'duplicate' flag on rows."
            )
        return result

    # -- Setup / deploy --

    def setup(
        self,
        project_id: Optional[str] = None,
        solution: Optional[str] = None,
        env: Optional[str] = None,
    ) -> dict:
        """Provision GCP foundation for a solution on a project.

        Labels the deployment's bucket (gapp-deployment, gapp-version) and marks the project gapp=hosted when it
        is not yet. A CI run never writes project labels: there the mark is
        left to `gapp ci project setup`, which runs as an Owner. Setup NEVER
        writes gapp-env. Use gapp projects set-env to bind an env.
        """
        ctx = self.resolve_solution(solution)
        if not ctx:
            raise RuntimeError("Not inside a gapp solution.")
        solution_name = ctx["name"]

        # Resolve target project. allow_zero=True for first-time setup with --project.
        try:
            res = self.resolve_project_for_solution(
                solution_name, env=env, project=project_id, allow_zero=True
            )
        except RuntimeError:
            # Fall back: discovery returned 0 with no --project — must specify.
            if not project_id:
                raise
            raise

        target_project = res["project_id"]
        labels = res["labels"]
        project_env = res["env"]

        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
        bucket_name = self.get_bucket_name(solution_name, target_project)

        # Version gating on the deployment's services.
        self._check_version(target_project, solution_name, self._service_names(solution_name, repo_path))

        # Verify --env vs project env (already done in resolver, but
        # recheck after resolver path for explicit-project zero-match).
        if env is not None:
            env_norm = _validate_env_name(env)
            if env_norm != project_env:
                raise RuntimeError(
                    f"Project '{target_project}' is bound to env="
                    f"'{project_env or UNDEFINED_ENV_DISPLAY}'. "
                    f"You passed --env='{env_norm}'. To bind the project "
                    f"to a different env: gapp projects set-env."
                )

        manifest = load_manifest(repo_path) if repo_path else {}

        apis_enabled = []
        for api in [
            "run.googleapis.com", "secretmanager.googleapis.com",
            "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
            # cloudresourcemanager backs `gcloud projects describe` /
            # `cloudresourcemanager.googleapis.com/v3/projects/...` — used
            # by ensure_build_permissions, set_project_labels,
            # get_project_labels, and ci.setup's _get_project_number.
            # Without it enabled, the very next step in this function
            # (ensure_build_permissions) fails on any project that
            # doesn't happen to already have the API turned on.
            "cloudresourcemanager.googleapis.com",
        ] + get_required_apis(manifest):
            self.provider.enable_api(target_project, api)
            apis_enabled.append(api)

        bucket_status = "exists" if self.provider.bucket_exists(target_project, bucket_name) else "created"
        if bucket_status == "created":
            self.provider.create_bucket(target_project, bucket_name)

        self.provider.ensure_build_permissions(target_project)

        self.provider.set_bucket_labels(target_project, bucket_name, self.deployment_labels(solution_name))

        # The project mark, once per project, by an Owner only.
        if labels.get(HOSTED_LABEL) == HOSTED_VALUE:
            label_status = "exists"
        elif on_ci_runner():
            label_status = "skipped"
        else:
            self.provider.set_project_labels(target_project, {**labels, HOSTED_LABEL: HOSTED_VALUE})
            label_status = "added"

        return {
            "name": solution_name,
            "project_id": target_project,
            "env": project_env,
            "bucket": bucket_name,
            "bucket_status": bucket_status,
            "label_status": label_status,
            "apis": apis_enabled,
        }

    def deploy(
        self,
        ref: Optional[str] = None,
        solution: Optional[str] = None,
        env: Optional[str] = None,
        dry_run: bool = False,
        project_id: Optional[str] = None,
        rebuild: bool = False,
        replace_domain: bool = False,
    ) -> dict:
        """Build the image and apply Terraform for one solution.

        replace_domain: a deploy that would replace or remove a live custom
        domain mapping is refused unless this is True, because Terraform
        destroys the old mapping before creating the new one.
        """
        ctx = self.resolve_solution(solution)
        if not ctx:
            raise RuntimeError("Could not determine solution name.")
        solution_name = ctx["name"]
        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None

        # Try to resolve a project. For dry-run we tolerate "no project yet."
        target_project = None
        project_env = None
        try:
            res = self.resolve_project_for_solution(
                solution_name, env=env, project=project_id, allow_zero=False
            )
            target_project = res["project_id"]
            project_env = res["env"]
        except RuntimeError:
            if not dry_run:
                raise

        preview = {
            "name": solution_name,
            "env": project_env,
            "project_id": target_project,
            "labels": ", ".join(f"{k}={v}" for k, v in self.deployment_labels(solution_name).items()),
            "bucket": self.get_bucket_name(solution_name, target_project) if target_project else None,
            "repo_path": str(repo_path) if repo_path else None,
            "status": "ready" if target_project and repo_path else "pending_setup",
            "services": [],
        }
        if repo_path:
            manifest = load_manifest(repo_path)
            if paths := get_paths(manifest):
                for p in paths:
                    sub_m = load_manifest(repo_path / p) if (repo_path / p).is_dir() else {}
                    preview["services"].append({
                        "name": get_name(sub_m) or f"{solution_name}-{p.replace('/', '-')}",
                        "path": p,
                    })
            else:
                preview["services"].append({"name": solution_name, "path": "."})

        if dry_run:
            return {**preview, "dry_run": True}
        if not target_project:
            raise RuntimeError(f"No GCP project resolved for '{solution_name}'.")

        bucket_name = self.get_bucket_name(solution_name, target_project)
        if not self.provider.bucket_exists(target_project, bucket_name):
            raise RuntimeError(f"Foundation missing. Run 'gapp setup'.")
        self._check_version(target_project, solution_name, [s["name"] for s in preview["services"]])
        self.provider.set_bucket_labels(target_project, bucket_name, self.deployment_labels(solution_name))

        build_ref = ref or "HEAD"
        if paths := get_paths(load_manifest(repo_path)):
            return {"services": [
                self._deploy_single_service(
                    s["name"], target_project, repo_path, load_manifest(repo_path / s["path"]),
                    service_path=s["path"], env=project_env, parent_solution=solution_name,
                    ref=build_ref, rebuild=rebuild, replace_domain=replace_domain,
                )
                for s in preview["services"]
            ]}
        return self._deploy_single_service(
            solution_name, target_project, repo_path, load_manifest(repo_path), env=project_env,
            ref=build_ref, rebuild=rebuild, replace_domain=replace_domain,
        )

    def status(self, name: str | None = None, env: Optional[str] = None,
               project: Optional[str] = None) -> StatusResult:
        # Lenient: status is read-only and must work even if the local
        # gapp.yaml predates the current schema or is missing fields the
        # build pipeline would require. It reports the direct system only
        # (GCP labels and Terraform state) and never raises for a missing
        # or unusable Google Cloud identity: that is an outcome with the
        # login command, not a crash, and not "not deployed".
        local = self.resolve_solution(name, strict=False)
        if not local:
            return StatusResult(
                initialized=False,
                deployment=DeploymentInfo(project=None, pending=True),
                next_step=NextStep(action="init", hint=(
                    "No gapp.yaml here (or not inside a git repository). Run `gapp init` in the "
                    "solution's repo, or pass --solution <name> to check a deployed solution."
                )),
            )
        warnings = _manifest_warnings(local.get("repo_path"))
        try:
            ctx = self.resolve_solution_with_project(name, env=env, project=project, strict=False,
                                                     first_only=True)
        except CloudAccessError:
            return StatusResult(
                initialized=True,
                name=local["name"],
                repo_path=local.get("repo_path"),
                deployment=DeploymentInfo(project=None, pending=True),
                next_step=NextStep(action="sign in", hint=self._identity_hint(project)),
                warnings=warnings,
            )
        solution_name = ctx["name"]
        repo_path = ctx.get("repo_path")
        project_id = ctx.get("project_id")

        result = StatusResult(
            initialized=True,
            name=solution_name,
            repo_path=repo_path,
            deployment=DeploymentInfo(project=project_id, pending=True),
            warnings=warnings,
        )
        if not project_id:
            result.next_step = NextStep(
                action="setup",
                hint=f"No GCP project attached for '{solution_name}'.",
            )
            return result

        services_to_check = []
        if repo_path:
            manifest = load_manifest(Path(repo_path), strict=False)
            if paths := get_paths(manifest):
                for p in paths:
                    sub_m = load_manifest(Path(repo_path) / p, strict=False) if (Path(repo_path) / p).is_dir() else {}
                    services_to_check.append({
                        "name": get_name(sub_m) or f"{solution_name}-{p.replace('/', '-')}",
                        "is_workspace": True,
                    })
            else:
                services_to_check.append({"name": solution_name, "is_workspace": False})
        else:
            services_to_check.append({"name": solution_name, "is_workspace": False})

        for svc in services_to_check:
            bucket_name = self.get_bucket_name(solution_name, project_id)
            state_prefix = f"terraform/state/{svc['name']}" if svc["is_workspace"] else "terraform/state"
            outputs = self.provider.get_infrastructure_outputs(
                _get_staging_dir(svc["name"]), bucket_name, state_prefix,
            )
            if outputs and (url := outputs.get("service_url")):
                result.deployment.services.append(
                    ServiceStatus(name=svc["name"], url=url, healthy=self.provider.check_http_health(url))
                )
                result.deployment.pending = False
            # The mapping Terraform last applied is the live one; report its state.
            if outputs and result.domain is None and (live := (outputs.get("custom_domain") or "").strip()):
                result.domain = self._domain_status(project_id, live)
        return result

    def _identity_hint(self, project_id: Optional[str] = None) -> str:
        """What to do when no Google Cloud identity this machine holds works."""
        account = self.get_account()
        if on_ci_runner():
            return ci_identity_hint(account, project_id)
        who = f" for the configured account {account}" if account else ""
        login = f"gcloud auth login {account}" if account else "gcloud auth login"
        return (
            f"No Google Cloud identity this machine can use{who}: the login is missing, expired, "
            f"or not allowed to read the project. Sign in with `{login}` (or point gapp at another "
            f"account with `gapp config account <email>`), then run `gapp status` again."
        )

    def _domain_status(self, project_id: str, domain: str) -> DomainStatus:
        """Fill DomainStatus from the Cloud Run domain mapping's conditions."""
        mapping = self.provider.get_domain_mapping(project_id, REGION, domain)
        if not mapping:
            return DomainStatus(
                name=domain, status="error",
                detail="Terraform state records this domain, but Cloud Run has no "
                       "mapping for it. Redeploy to recreate the mapping.",
            )
        status_block = mapping.get("status") or {}
        state, detail = _domain_status_from_conditions(status_block.get("conditions") or [])
        cname = next(
            (r.get("rrdata") for r in status_block.get("resourceRecords") or []
             if (r.get("type") or "").upper() == "CNAME" and r.get("rrdata")),
            None,
        )
        kwargs = {"cname_target": cname.rstrip(".")} if cname else {}
        return DomainStatus(name=domain, status=state, detail=detail, **kwargs)

    # -- Internal helpers --

    def _check_domain_safety(self, service_name, project_id, requested_domain,
                             bucket_name, state_prefix, replace_domain):
        """Refuse a deploy that would take a live custom domain down.

        Terraform replaces a changed `google_cloud_run_domain_mapping` by
        destroying the old one first, so a typo, a placeholder, or an omitted
        `domain:` silently removes the live mapping. Two guards, both before
        any build or apply:

        1. A live mapping exists (recorded in Terraform state) and this deploy
           maps a different domain or none: refuse unless replace_domain.
        2. This deploy creates or replaces a mapping: the deploying identity
           must have verified the domain or a parent, or Cloud Run rejects
           the mapping after the old one is already gone.

        A redeploy that keeps the same domain changes nothing about the
        mapping, so neither guard applies to it.
        """
        requested = (requested_domain or "").strip().lower()
        # Fail closed: a state that cannot be read is not "no live mapping",
        # or this deploy could remove or replace a domain that is serving.
        try:
            outputs = self.provider.get_infrastructure_outputs(
                _get_staging_dir(service_name), bucket_name, state_prefix, strict=True,
            ) or {}
        except CloudAccessError as e:
            raise CloudAccessError(
                f"Refusing to deploy '{service_name}': its Terraform state could not be read, so gapp "
                f"cannot tell whether a custom domain is mapped to it now, and deploying could take "
                f"that domain down. Nothing was built or changed. Fix the access to the state bucket "
                f"{bucket_name} for this identity, then deploy again. ({e})"
            ) from e
        live = (outputs.get("custom_domain") or "").strip().lower()

        if live and live != requested and not replace_domain:
            if requested:
                raise RuntimeError(
                    f"Refusing to replace the live domain mapping of '{service_name}': "
                    f"{live} is mapped now and this deploy would map {requested} instead. "
                    f"Terraform destroys the old mapping before creating the new one, so "
                    f"{live} would stop serving. If the change is intended, re-run with "
                    f"--replace-domain (replace_domain=True); otherwise fix `domain:` in "
                    f"gapp.yaml or the fleet entry's domain (`gapp ci setup <name> ... --domain`)."
                )
            raise RuntimeError(
                f"Refusing to remove the live domain mapping {live} of '{service_name}': "
                f"this deploy sets no domain (none in gapp.yaml and none passed by CI). "
                f"If removing it is intended, re-run with --replace-domain "
                f"(replace_domain=True); otherwise set `domain: {live}` in gapp.yaml or "
                f"pass it through the fleet entry's domain (`gapp ci setup <name> ... --domain`)."
            )

        if requested and requested != live:
            verified = self.provider.list_verified_domains()
            if not _domain_is_verified(requested, verified):
                parent = requested.split(".", 1)[1] if requested.count(".") >= 2 else requested
                raise RuntimeError(
                    f"The deploying identity has not verified ownership of {requested} or a "
                    f"parent domain, so Cloud Run would refuse the domain mapping. Verify "
                    f"once, then redeploy:\n"
                    f"  gcloud domains verify {parent}      # opens Search Console; verify "
                    f"{parent} or the narrowest parent you control\n"
                    f"  gcloud domains list-user-verified   # must list it afterwards\n"
                    f"A domain is verified per identity. For CI deploys, also add the deploy "
                    f"service account (gapp-deploy@{project_id}.iam.gserviceaccount.com) as a "
                    f"verified owner of that Search Console property."
                )

    def _deploy_single_service(self, name, project_id, repo_path, manifest,
                               service_path=".", env=None, parent_solution=None,
                               ref="HEAD", rebuild=False, replace_domain=False):
        bucket_name = self.get_bucket_name(parent_solution or name, project_id)
        state_prefix = f"terraform/state/{name}" if parent_solution else "terraform/state"
        self._check_domain_safety(
            name, project_id, get_domain(manifest), bucket_name, state_prefix, replace_domain,
        )
        service_root = repo_path / service_path
        entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)
        sha = self._resolve_ref(repo_path, ref)
        self.provider.ensure_artifact_registry(project_id, REGION)
        image = f"{REGION}-docker.pkg.dev/{project_id}/gapp/{name}:{sha}"
        if rebuild or not self.provider.image_exists(project_id, REGION, name, sha):
            build_dir, build_ep = _prepare_build_dir(repo_path, image, entrypoint, ref=ref)
            try:
                self.provider.submit_build_sync(project_id, Path(build_dir), image, build_ep)
            finally:
                shutil.rmtree(build_dir, ignore_errors=True)
        # Secret materialization runs unconditionally before tfvars are built.
        # Skipping it on "already deployed" runs would leave the secrets tfvar
        # empty for legacy state that still owns per-secret module resources,
        # and terraform would destroy those resources as drift.
        from gapp.admin.sdk.secrets import (
            validate_declared_secrets,
            materialize_generated_secrets,
        )
        validate_declared_secrets(project_id, parent_solution or name, manifest)
        materialize_generated_secrets(project_id, parent_solution or name, manifest)

        tfvars = _build_tfvars(
            name, project_id, image, get_service_config(manifest),
            get_prerequisite_secrets(manifest), Path(repo_path) / service_path,
            get_public(manifest), get_domain(manifest),
            bucket_name,
            solution_name=parent_solution or name,
            labels=self.deployment_labels(parent_solution or name),
        )
        outputs = self.provider.apply_infrastructure(
            staging_dir=_get_staging_dir(name), bucket_name=bucket_name,
            state_prefix=state_prefix, auto_approve=True, tfvars=tfvars,
        )
        return {
            "name": name, "project_id": project_id, "image": image,
            "terraform_status": "applied", "service_url": outputs.get("service_url"),
            "env": env,
        }

    def _get_git_root(self) -> Optional[Path]:
        try:
            res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if res.returncode == 0:
                return Path(res.stdout.strip())
        except Exception:
            pass
        return None

    def _resolve_ref(self, path, ref):
        return subprocess.run(
            ["git", "rev-parse", "--short=12", ref],
            capture_output=True, text=True, cwd=path, check=True,
        ).stdout.strip()


def _domain_is_verified(domain: str, verified: List[str]) -> bool:
    """True when `domain` or one of its parents is in the verified list."""
    d = domain.strip().rstrip(".").lower()
    for v in verified:
        v = (v or "").strip().rstrip(".").lower()
        if v and (d == v or d.endswith("." + v)):
            return True
    return False


def _domain_status_from_conditions(conditions: list) -> tuple[str, Optional[str]]:
    """Map a Cloud Run domain mapping's conditions to gapp's DomainStatus.

    Cloud Run reports three conditions: `DomainRoutable`, `CertificateProvisioned`
    and the summary `Ready`. Returns (status, detail) where status is one of
    pending_dns, pending_cert, active, error.
    """
    by_type = {c.get("type"): c for c in conditions if isinstance(c, dict)}

    def _status(kind):
        return (by_type.get(kind) or {}).get("status")

    def _detail(kind):
        c = by_type.get(kind) or {}
        return c.get("message") or c.get("reason") or None

    if not by_type:
        return "pending_dns", "The mapping reports no conditions yet; check again shortly."
    if _status("Ready") == "True":
        return "active", None
    if "DomainRoutable" in by_type and _status("DomainRoutable") != "True":
        return "pending_dns", _detail("DomainRoutable")
    cert = by_type.get("CertificateProvisioned")
    if cert and cert.get("status") != "True":
        detail = _detail("CertificateProvisioned") or ""
        # Cloud Run words a missing CNAME as a certificate condition:
        # "You must configure your DNS records for certificate issuance to begin."
        if "dns" in detail.lower() or "record" in detail.lower():
            return "pending_dns", detail or None
        return "pending_cert", detail or None
    return "error", _detail("Ready") or "The mapping is not ready."


def _resolve_entrypoint(manifest, root, repo):
    ep, cmd = manifest.get("service", {}).get("entrypoint"), manifest.get("service", {}).get("cmd")
    if ep: return ep, "explicit"
    if cmd: return f"__cmd__:{cmd}", "cmd"
    if (root / "Dockerfile").exists(): return "__dockerfile__", "dockerfile"
    return "__mcp_app__", "mcp-app"


def _prepare_build_dir(path, image, ep, ref="HEAD"):
    d = tempfile.mkdtemp(prefix="gapp-build-")
    subprocess.run(
        ["tar", "xf", "-", "-C", d],
        stdin=subprocess.Popen(["git", "archive", "--format=tar", ref], stdout=subprocess.PIPE, cwd=path).stdout,
        check=True,
    )
    t = Path(__file__).resolve().parent.parent.parent / "templates"
    shutil.copy2(t / "cloudbuild.yaml", Path(d) / "cloudbuild.yaml")
    if ep != "__dockerfile__":
        shutil.copy2(t / "Dockerfile", Path(d) / "Dockerfile")
    return d, ep


def _build_tfvars(name, pid, img, cfg, secrets, repo_path, public, domain,
                  bucket_name, solution_name=None, labels=None):
    # Every key returned here corresponds to a structurally-required tfvar in
    # modules/cloud-run-service/variables.tf. None of them have defaults, so
    # dropping a key fails terraform plan instead of silently mis-deploying.
    # See issue #35: an earlier refactor lost `data_bucket` here, and the
    # module's old `default = ""` + conditional opt-out turned that into a
    # silent volume strip. Don't reintroduce optional-empty tfvars for things
    # the module structurally needs.
    from gapp.admin.sdk.manifest import resolve_env_vars, get_env_vars, load_manifest
    env = dict(cfg.get("env", {}))
    manifest = load_manifest(repo_path)
    env_vars = get_env_vars(manifest)
    sol = solution_name or name
    secrets_map = {n.upper().replace("-", "_"): n for n in (secrets or {})}
    if env_vars:
        for e in resolve_env_vars(env_vars, {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": name}):
            if "value" in e:
                env[e["name"]] = e["value"]
            elif isinstance(e.get("secret"), dict):
                secrets_map[e["name"]] = f"{sol}-{e['secret']['name']}"
    custom_domain = domain if domain and domain.strip() else ""
    return {
        "project_id": pid, "service_name": name, "image": img,
        "memory": cfg["memory"], "cpu": cfg["cpu"], "max_instances": cfg["max_instances"],
        "env": env,
        "secrets": secrets_map,
        "public": bool(public), "custom_domain": custom_domain,
        "data_bucket": bucket_name,
        "labels": dict(labels or {}),
    }


def _manifest_warnings(repo_path) -> List[str]:
    """Status reads gapp.yaml leniently, but never silently: the gapp.yaml
    validator's unknown fields come back as one warning line."""
    if not repo_path:
        return []
    from gapp.admin.sdk.schema import ManifestValidationError
    try:
        load_manifest(Path(repo_path), strict=True)
    except ManifestValidationError as e:
        unknown = [i["path"] for i in e.issues if i["type"] == "extra_forbidden"]
        if unknown:
            return [f"gapp.yaml has unknown field(s) {', '.join(unknown)} (check for typos); "
                    f"`gapp setup` and `gapp deploy` refuse it until they are fixed."]
    return []


def _get_staging_dir(name):
    return Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "gapp" / name / "terraform"
