"""Core SDK implementation for gapp.

The label / identity / env model:

  Solution label (per project, per (owner, solution)):
    gapp_<owner>_<solution>=v-N    (owned)
    gapp__<solution>=v-N           (global, no owner — double underscore)

  Project env label (per project, optional):
    gapp-env=<env>                 (no owner segment)
    Missing = undefined env.

  No role labels. No defaults. No env in solution label keys.

Owner is optional in the profile ("global mode" vs "owned mode"). Identity
surfaces in solution label keys (single vs double underscore) and in tfstate
metadata when written. Env is a project property, set/changed only by
gapp_projects_set_env. Setup and deploy never write gapp-env.

See CONTRIBUTING.md for the full model and resolution truth table.
"""

import os
import shutil
import tempfile
import time
import json
import subprocess
from pathlib import Path
from typing import Optional, List, Dict

from gapp import __version__, MIN_SUPPORTED_MAJOR
from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.cloud.base import CloudProvider
from gapp.admin.sdk.config import load_config, save_config, get_active_config
from gapp.admin.sdk.manifest import (
    get_solution_name, load_manifest, save_manifest, get_required_apis,
    get_domain, get_entrypoint, get_name, get_paths,
    get_prerequisite_secrets, get_service_config, resolve_env_vars, get_public, get_env_vars
)
from gapp.admin.sdk.models import StatusResult, DeploymentInfo, NextStep, ServiceStatus, DomainStatus

CURRENT_MAJOR = int(__version__.split(".")[0])

# The single project-level label that binds a project to a named env.
# No owner segment — env is project-wide.
PROJECT_ENV_LABEL = "gapp-env"

# String forbidden as a value for --env. "default" was a v-2 magic value
# meaning "no env"; in v-3 the absence of a gapp-env label is its own state
# ("undefined"), and --env requires an actually-named value.
RESERVED_ENV_NAMES = frozenset({"default"})

# Display literal for projects that have no gapp-env label.
UNDEFINED_ENV_DISPLAY = "<undefined>"

# Display literal for global-namespace owners.
GLOBAL_OWNER_DISPLAY = "<global>"


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
        """Bucket name is environment-blind AND owner-blind.

        Project IS the resource boundary. Owner is a label-layer / identity
        concern, not a resource-naming concern. Two owners using the same
        solution name on the same project would collide on bucket name —
        that collision is gated at setup time (Layer 1 cross-owner check)
        and at deploy time (tfstate identity check, when implemented).
        """
        return f"gapp-{solution_name}-{project_id}".lower()

    def get_label_key(self, solution_name: str) -> str:
        """Solution label key. Env-blind.

        Owned:  gapp_<owner>_<solution>
        Global: gapp__<solution>   (double underscore — no-owner sentinel)
        """
        owner = self.get_owner()
        owner_seg = owner if owner else ""
        return f"gapp_{owner_seg}_{solution_name}".lower()

    def get_label_value(self) -> str:
        """Solution label value: contract major only, derived from __version__."""
        return f"v-{CURRENT_MAJOR}"

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
          rebind would create (same (owner, solution, target-env) on
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
            solution_count = sum(
                1 for k in labels.keys()
                if k.startswith("gapp_") and not k.startswith("gapp-")
            )
            raise RuntimeError(
                f"Project '{project_id}' is already bound to env='{existing}'. "
                f"Refusing to change to env='{env_norm}' without force=True. "
                f"({solution_count} solution(s) on this project would be reclassified.)"
            )

        # On force-overwrite, check for cross-project duplicates the rebind would create.
        if existing and force:
            self._check_rebind_duplicates(project_id, labels, env_norm)

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

    def _check_rebind_duplicates(self, project_id: str, labels: dict, target_env: str) -> None:
        """When set_project_env is forcing an env rebind, refuse if any
        solution on this project would end up on >1 project under target_env.
        """
        for key in labels.keys():
            if not key.startswith("gapp_") or key.startswith("gapp-"):
                continue
            # Same solution label on another project?
            other_projects = self.provider.list_projects(
                filter_query=f"labels:{key}", limit=10
            )
            for p in other_projects:
                if p["projectId"] == project_id:
                    continue
                other_env = p.get("labels", {}).get(PROJECT_ENV_LABEL)
                if other_env == target_env:
                    raise RuntimeError(
                        f"Refusing rebind: solution label '{key}' also exists "
                        f"on project '{p['projectId']}' which is already "
                        f"bound to env='{target_env}'. Setting '{project_id}' "
                        f"to env='{target_env}' would create cross-project "
                        f"duplication. Resolve manually with `gcloud projects "
                        f"update --remove-labels=...`."
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
        """
        if name:
            return {"name": name, "project_id": None, "repo_path": None}
        git_root = self._get_git_root()
        if git_root and (git_root / "gapp.yaml").is_file():
            manifest = load_manifest(git_root, strict=strict)
            solution_name = get_solution_name(manifest, git_root)
            return {"name": solution_name, "project_id": None, "repo_path": str(git_root)}
        return None

    def discover_projects_for_solution(self, solution_name: str) -> List[Dict]:
        """Find all projects hosting the given solution under the active owner.

        Returns a list of dicts: {projectId, env (str|None), labels}.
        Env is the project's gapp-env value (or None if undefined).
        """
        label_key = self.get_label_key(solution_name)
        projects = self.provider.list_projects(filter_query=f"labels:{label_key}")
        result = []
        for p in projects:
            labels = p.get("labels", {})
            result.append({
                "projectId": p["projectId"],
                "env": labels.get(PROJECT_ENV_LABEL) or None,
                "labels": labels,
            })
        return result

    def resolve_project_for_solution(
        self,
        solution_name: str,
        env: Optional[str] = None,
        project: Optional[str] = None,
        allow_zero: bool = False,
    ) -> Dict:
        """Single-source resolver per CONTRIBUTING.md truth table.

        Inputs are caller-provided; owner comes from the active profile.
        Returns a dict with: project_id, env, labels, status ("resolved"
        or "first_setup" when allow_zero and zero matches with explicit project).

        Raises RuntimeError on any unresolvable case.
        """
        env_norm = _validate_env_name(env)

        # Explicit project: bypass discovery, verify state.
        if project:
            labels = self.provider.get_project_labels(project)
            project_env = labels.get(PROJECT_ENV_LABEL) or None
            label_key = self.get_label_key(solution_name)
            has_solution = label_key in labels

            if env_norm is not None and env_norm != project_env:
                raise RuntimeError(
                    f"Project '{project}' is bound to env="
                    f"'{project_env or UNDEFINED_ENV_DISPLAY}'. "
                    f"You passed --env='{env_norm}'. Pick one."
                )

            if not has_solution:
                if allow_zero:
                    return {
                        "project_id": project,
                        "env": project_env,
                        "labels": labels,
                        "status": "first_setup",
                    }
                raise RuntimeError(
                    f"Solution '{solution_name}' is not deployed on project "
                    f"'{project}'. Run setup first."
                )
            return {
                "project_id": project,
                "env": project_env,
                "labels": labels,
                "status": "resolved",
            }

        # Discovery path: query by solution label.
        if not self.is_discovery_on():
            raise RuntimeError(
                "Discovery is OFF for this profile. Pass --project explicitly "
                "or run `gapp config discovery on`."
            )

        candidates = self.discover_projects_for_solution(solution_name)
        if not candidates:
            if allow_zero:
                raise RuntimeError(
                    f"Solution '{solution_name}' is not deployed anywhere. "
                    f"Pass --project to install for the first time."
                )
            raise RuntimeError(
                f"Solution '{solution_name}' is not deployed. Run "
                f"`gapp setup --project <project-id>` first."
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
                f"Solution '{solution_name}' exists on multiple projects "
                f"under env='{env_norm}' — corruption requires manual "
                f"cleanup:\n  {rows}\n"
                f"Use `gcloud projects update <pid> --remove-labels="
                f"{self.get_label_key(solution_name)}` on whichever "
                f"project should not own this solution."
            )
        raise RuntimeError(
            f"Solution '{solution_name}' exists on multiple projects:\n"
            f"  {rows}\nPass --env or --project to disambiguate."
        )

    # -- Fleet listing --

    def list_target_projects(self) -> dict:
        """List GCP projects with a gapp-env label.

        gapp-env is a single project-wide label with no owner segment, so this
        returns the same set regardless of the active owner. No filtering knob.
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
        return {
            "projects": sorted(projects, key=lambda x: x["id"]),
            "owner": self.get_owner(),
        }

    def list_apps(self, all_owners: bool = False, project_limit: int = 50) -> dict:
        """List deployed apps via project labels.

        Args:
            all_owners: When False (default), scope listing to the active
                owner namespace (or global if no owner is set). When True,
                ignore the active owner and return apps across every owner
                namespace. Backs the CLI flag `--all`.
            project_limit: Max projects to scan in one call.

        Returns one entry per (project × solution-label). Solution labels are
        env-blind; env comes from the project's gapp-env label (or None when
        the project has no env binding). Contract major is parsed from the
        value.
        """
        owner = self.get_owner()
        is_global_mode = not all_owners and not owner

        if all_owners:
            filter_query = "labels:gapp*"
        elif owner:
            filter_query = f"labels:gapp_{owner}_*"
        else:
            filter_query = "labels:gapp__*"

        projects_data = self.provider.list_projects(
            filter_query=filter_query, limit=project_limit
        )

        apps = []
        for project in projects_data:
            pid = project["projectId"]
            labels = project.get("labels", {})
            project_env = labels.get(PROJECT_ENV_LABEL) or None
            for key, val in labels.items():
                app = self._parse_app_label(key, val, pid, project_env)
                if app is None:
                    continue
                if not all_owners and owner and app["owner"] != owner:
                    continue
                if is_global_mode and app["owner"] != "global":
                    continue
                apps.append(app)

        # Detect cross-project duplicates (same owner+name+named env on >1 project).
        dup_keys: Dict[tuple, list] = {}
        for app in apps:
            if app["env"] is None:
                continue  # undefined env on multiple projects is not corruption
            k = (app["owner"], app["name"], app["env"])
            dup_keys.setdefault(k, []).append(app["project"])
        for app in apps:
            if app["env"] is None:
                app["duplicate"] = False
                continue
            k = (app["owner"], app["name"], app["env"])
            app["duplicate"] = len(dup_keys[k]) > 1

        result = {
            "apps": sorted(apps, key=lambda x: (x["owner"], x["name"], x["env"] or "")),
            "metadata": {
                "projects": {"count": len(projects_data), "limit": project_limit},
                "apps": {"count": len(apps)},
                "owner": owner,
                "contract_major": CURRENT_MAJOR,
            },
            "messages": [],
            "warnings": [],
        }
        if all_owners:
            result["messages"].append("Showing apps across all owner namespaces.")
        elif owner:
            result["messages"].append(f"Showing apps for owner '{owner}'. Use --all to check for more.")
        else:
            result["messages"].append("Showing global apps. Use --all to check for more.")
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

    @staticmethod
    def _parse_app_label(key: str, val: str, project_id: str, project_env: Optional[str]) -> Optional[dict]:
        """Parse a project label into an app dict, or None if not a solution label.

        Recognized formats (env-blind):
          gapp_<owner>_<solution>=v-N
          gapp__<solution>=v-N         (global)
          gapp-<solution>=<env>        (legacy v-2 default-env label)

        Project labels gapp-env, gapp-env_*, and other non-solution gapp-*
        labels are skipped. Env on the result comes from the project's
        gapp-env (passed in as project_env), not from the solution label.
        """
        if key.startswith(PROJECT_ENV_LABEL):  # gapp-env or gapp-env_<owner>
            return None
        if key.startswith("gapp_"):
            parts = key.split("_")
            if len(parts) < 3:
                return None
            l_owner = parts[1] if parts[1] else "global"
            # Solutions with underscores break the parser; accept everything
            # after segment 2 as the name to maintain legacy parity.
            l_name = "_".join(parts[2:])
            major = None
            if val.startswith("v-"):
                try:
                    major = int(val.split("-")[1].split("_")[0])
                except (IndexError, ValueError):
                    pass
            return {
                "name": l_name,
                "project": project_id,
                "owner": l_owner,
                "env": project_env,   # may be None (undefined)
                "contract_major": major,
                "is_legacy": False,
            }
        if key.startswith("gapp-") and not key.startswith(PROJECT_ENV_LABEL):
            # legacy v-2 form: gapp-<solution>=<env>
            return {
                "name": key[5:],
                "project": project_id,
                "owner": "global",
                "env": val or None,
                "contract_major": None,
                "is_legacy": True,
            }
        return None

    # -- Setup / deploy --

    def setup(
        self,
        project_id: Optional[str] = None,
        solution: Optional[str] = None,
        env: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        """Provision GCP foundation for a solution on a project.

        Setup writes ONLY the solution label (gapp_<owner>_<solution>=v-N).
        It NEVER writes gapp-env. Use gapp projects set-env to bind an env.

        Layer-1 cross-owner check: refuses if a different owner already
        has the same solution name on the target project, unless force=True.
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

        # Layer-1 cross-owner check.
        own_owner = self.get_owner() or ""
        for k in labels.keys():
            if not k.startswith("gapp_") or k.startswith("gapp-"):
                continue
            parts = k.split("_")
            if len(parts) < 3:
                continue
            other_owner = parts[1]
            other_name = "_".join(parts[2:])
            if other_name == solution_name and other_owner != own_owner:
                if not force:
                    other_label = f"owner='{other_owner or GLOBAL_OWNER_DISPLAY}'"
                    raise RuntimeError(
                        f"Project '{target_project}' already has a solution "
                        f"named '{solution_name}' under {other_label}. "
                        f"Resources (bucket, Cloud Run service) are owner-blind "
                        f"and would clobber. Re-run with force=True to proceed "
                        f"only if intentional."
                    )

        # Contract gating on the target solution label.
        self._check_contract(labels, solution_name, target_project)

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

        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None
        manifest = load_manifest(repo_path) if repo_path else {}

        apis_enabled = []
        for api in [
            "run.googleapis.com", "secretmanager.googleapis.com",
            "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
        ] + get_required_apis(manifest):
            self.provider.enable_api(target_project, api)
            apis_enabled.append(api)

        bucket_name = self.get_bucket_name(solution_name, target_project)
        bucket_status = "exists" if self.provider.bucket_exists(target_project, bucket_name) else "created"
        if bucket_status == "created":
            self.provider.create_bucket(target_project, bucket_name)

        self.provider.ensure_build_permissions(target_project)

        label_key = self.get_label_key(solution_name)
        label_value = self.get_label_value()
        if labels.get(label_key) != label_value:
            labels[label_key] = label_value
            self.provider.set_project_labels(target_project, labels)
            label_status = "added"
        else:
            label_status = "exists"

        return {
            "name": solution_name,
            "project_id": target_project,
            "env": project_env,
            "bucket": bucket_name,
            "bucket_status": bucket_status,
            "label_status": label_status,
            "apis": apis_enabled,
        }

    def _check_contract(self, labels: dict, solution_name: str, project_id: str) -> None:
        """Refuse setup/deploy if the project's stamped contract for this solution
        is outside the supported window."""
        target_key = self.get_label_key(solution_name)
        existing = labels.get(target_key)
        if not existing or not existing.startswith("v-"):
            return
        try:
            n = int(existing.split("-")[1])
        except (IndexError, ValueError):
            return
        if n > CURRENT_MAJOR:
            raise RuntimeError(
                f"Project '{project_id}' has '{solution_name}' stamped at v{n}.x; "
                f"this gapp build is v{__version__} (contract major {CURRENT_MAJOR}). "
                f"Upgrade gapp to manage this project."
            )
        if n < MIN_SUPPORTED_MAJOR:
            raise RuntimeError(
                f"Project '{project_id}' has '{solution_name}' stamped at v{n}.x; "
                f"below MIN_SUPPORTED_MAJOR={MIN_SUPPORTED_MAJOR}. "
                f"Migrate the label manually (gcloud projects update --update-labels=...) "
                f"or use an older gapp build."
            )

    def deploy(
        self,
        ref: Optional[str] = None,
        solution: Optional[str] = None,
        env: Optional[str] = None,
        dry_run: bool = False,
        project_id: Optional[str] = None,
        rebuild: bool = False,
    ) -> dict:
        ctx = self.resolve_solution(solution)
        if not ctx:
            raise RuntimeError("Could not determine solution name.")
        solution_name = ctx["name"]
        repo_path = Path(ctx["repo_path"]) if ctx.get("repo_path") else None

        # Try to resolve a project. For dry-run we tolerate "no project yet."
        target_project = None
        project_env = None
        labels: Dict[str, str] = {}
        try:
            res = self.resolve_project_for_solution(
                solution_name, env=env, project=project_id, allow_zero=False
            )
            target_project = res["project_id"]
            project_env = res["env"]
            labels = res["labels"]
        except RuntimeError:
            if not dry_run:
                raise

        preview = {
            "name": solution_name,
            "owner": self.get_owner(),
            "env": project_env,
            "project_id": target_project,
            "label": self.get_label_key(solution_name),
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

        self._check_contract(labels, solution_name, target_project)
        label_key = self.get_label_key(solution_name)
        if not labels.get(label_key, "").startswith("v-"):
            raise RuntimeError(
                f"Project '{target_project}' does not host '{solution_name}'. "
                f"Run 'gapp setup' first."
            )

        bucket_name = self.get_bucket_name(solution_name, target_project)
        if not self.provider.bucket_exists(target_project, bucket_name):
            raise RuntimeError(f"Foundation missing. Run 'gapp setup'.")

        build_ref = ref or "HEAD"
        if paths := get_paths(load_manifest(repo_path)):
            return {"services": [
                self._deploy_single_service(
                    s["name"], target_project, repo_path, load_manifest(repo_path / s["path"]),
                    service_path=s["path"], env=project_env, parent_solution=solution_name,
                    ref=build_ref, rebuild=rebuild,
                )
                for s in preview["services"]
            ]}
        return self._deploy_single_service(
            solution_name, target_project, repo_path, load_manifest(repo_path), env=project_env,
            ref=build_ref, rebuild=rebuild,
        )

    def status(self, name: str | None = None, env: Optional[str] = None) -> StatusResult:
        # Lenient: status is read-only and must work even if the local
        # gapp.yaml predates the current schema or is missing fields the
        # build pipeline would require.
        ctx = self.resolve_solution(name, strict=False)
        if not ctx:
            return StatusResult(initialized=False, next_step=NextStep(action="init"))
        solution_name = ctx["name"]
        repo_path = ctx.get("repo_path")

        # Resolve project (best-effort — status tolerates pending state).
        project_id = None
        try:
            res = self.resolve_project_for_solution(solution_name, env=env)
            project_id = res["project_id"]
        except RuntimeError:
            project_id = None

        result = StatusResult(
            initialized=True,
            name=solution_name,
            repo_path=repo_path,
            deployment=DeploymentInfo(project=project_id, pending=True),
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
        return result

    # -- Internal helpers --

    def _deploy_single_service(self, name, project_id, repo_path, manifest,
                               service_path=".", env=None, parent_solution=None,
                               ref="HEAD", rebuild=False):
        service_root = repo_path / service_path
        entrypoint, _ = _resolve_entrypoint(manifest, service_root, repo_path)
        sha = self._resolve_ref(repo_path, ref)
        self.provider.ensure_artifact_registry(project_id, "us-central1")
        image = f"us-central1-docker.pkg.dev/{project_id}/gapp/{name}:{sha}"
        if rebuild or not self.provider.image_exists(project_id, "us-central1", name, sha):
            build_dir, build_ep = _prepare_build_dir(repo_path, image, entrypoint, ref=ref)
            try:
                self.provider.submit_build_sync(project_id, Path(build_dir), image, build_ep)
            finally:
                shutil.rmtree(build_dir, ignore_errors=True)
        bucket_name = self.get_bucket_name(parent_solution or name, project_id)
        state_prefix = f"terraform/state/{name}" if parent_solution else "terraform/state"
        tfvars = _build_tfvars(
            name, project_id, image, get_service_config(manifest),
            get_prerequisite_secrets(manifest), Path(repo_path) / service_path,
            get_public(manifest), get_domain(manifest),
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


def _build_tfvars(name, pid, img, cfg, secrets, repo_path, public, domain):
    from gapp.admin.sdk.manifest import resolve_env_vars, get_env_vars, load_manifest
    env = dict(cfg.get("env", {}))
    manifest = load_manifest(repo_path)
    env_vars = get_env_vars(manifest)
    if env_vars:
        for e in resolve_env_vars(env_vars, {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": name}):
            if "value" in e: env[e["name"]] = e["value"]
    custom_domain = domain if domain and domain.strip() else ""
    return {
        "project_id": pid, "service_name": name, "image": img,
        "memory": cfg["memory"], "cpu": cfg["cpu"], "max_instances": cfg["max_instances"],
        "env": env,
        "secrets": {n.upper().replace("-", "_"): n for n in (secrets or {})},
        "public": bool(public), "custom_domain": custom_domain,
    }


def _get_staging_dir(name):
    return Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "gapp" / name / "terraform"
