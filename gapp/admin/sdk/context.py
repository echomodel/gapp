"""Solution context resolution — determine which solution to operate on."""

import subprocess
import os
from pathlib import Path

from gapp.admin.sdk.config import load_config, save_config, get_active_config
from gapp.admin.sdk.manifest import get_solution_name, load_manifest

# -- Global Settings --

def get_active_profile() -> str:
    """Return the name of the active configuration profile."""
    return load_config().get("active", "default")

def set_active_profile(name: str) -> None:
    """Switch the active profile, creating it if it doesn't exist."""
    config = load_config()
    name = name.strip().lower()
    config["active"] = name
    if name not in config["profiles"]:
        config["profiles"][name] = {"discovery": "on"}
    save_config(config)

def get_owner() -> str | None:
    """Return the owner name from the active profile."""
    return get_active_config().get("owner")

def set_owner(name: str | None) -> None:
    """Set the owner name in the active profile."""
    config = load_config()
    active = config["active"]
    profile = config["profiles"][active]
    profile["owner"] = name.strip().lower() if name else None
    save_config(config)

def get_account() -> str | None:
    """Return the gcloud account from the active profile."""
    return get_active_config().get("account")

def set_account(account: str | None) -> None:
    """Set the gcloud account in the active profile after validation."""
    if account:
        # Verify account is in gcloud auth list
        res = subprocess.run(["gcloud", "auth", "list", "--format", "value(account)"], capture_output=True, text=True)
        authed_accounts = [a.strip().lower() for a in res.stdout.splitlines() if a.strip()]
        target = account.strip().lower()
        if target not in authed_accounts:
            raise RuntimeError(
                f"Account '{account}' is not authenticated in gcloud.\n"
                f"Please run: gcloud auth login {account}\n"
                f"Then retry: gapp config account {account}"
            )

    config = load_config()
    active = config["active"]
    profile = config["profiles"][active]
    profile["account"] = account.strip().lower() if account else None
    save_config(config)

def is_discovery_on() -> bool:
    """Return True if GCP label discovery is enabled for the active profile."""
    return get_active_config().get("discovery", "on") == "on"

def set_discovery(state: str) -> None:
    """Toggle discovery 'on' or 'off' for the active profile."""
    state = state.strip().lower()
    if state not in ("on", "off"):
        raise ValueError("Discovery must be 'on' or 'off'.")
    config = load_config()
    active = config["active"]
    config["profiles"][active]["discovery"] = state
    save_config(config)

# -- Command Execution --

def run_gcloud(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a gcloud command, optionally forcing the configured account."""
    account = get_account()
    if account:
        env = kwargs.get("env") or os.environ.copy()
        env["CLOUDSDK_CORE_ACCOUNT"] = account
        kwargs["env"] = env
    
    return subprocess.run(["gcloud"] + args, **kwargs)

# -- Naming and Label Logic --

def get_bucket_name(solution_name: str, project_id: str, env: str = "default") -> str:
    """Generate the bucket name: gapp-[<owner>-]<solution>-<project>[-<env>]
    
    Defaults (None for owner, 'default' for env) are omitted for backward compatibility.
    """
    owner = get_owner()
    parts = ["gapp"]
    if owner:
        parts.append(owner)
    parts.append(solution_name)
    parts.append(project_id)
    if env != "default":
        parts.append(env)
    
    return "-".join(parts).replace("_", "-").lower()

def get_label_key(solution_name: str, env: str = "default") -> str:
    """Generate the project label key: gapp-[<owner>-]<solution>[-<env>]
    
    Defaults are omitted for backward compatibility.
    """
    owner = get_owner()
    parts = ["gapp"]
    if owner:
        parts.append(owner)
    parts.append(solution_name)
    if env != "default":
        parts.append(env)
    
    return "-".join(parts).replace("_", "-").lower()

# -- Context Resolution --

def get_git_root(path: Path | None = None) -> Path | None:
    """Find the git root directory from the given path or cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=path or Path.cwd(),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def resolve_solution(name: str | None = None) -> dict | None:
    """Resolve solution context without local registry."""
    if name:
        return {
            "name": name,
            "project_id": None,
            "repo_path": None,
        }

    git_root = get_git_root()
    if git_root and (git_root / "gapp.yaml").is_file():
        manifest = load_manifest(git_root)
        solution_name = get_solution_name(manifest, git_root)
        return {
            "name": solution_name,
            "project_id": None,
            "repo_path": str(git_root),
        }

    return None


def resolve_full_context(solution: str | None = None, env: str = "default") -> dict:
    """Resolve solution context with remote fallbacks and environment support."""
    ctx = resolve_solution(solution)
    if not ctx and solution:
        ctx = {"name": solution, "project_id": None, "repo_path": None}
    if not ctx:
        return {"name": None, "project_id": None, "repo_path": None, "github_repo": None}

    result = {**ctx, "github_repo": None}

    # Fill project_id from GCP labels if discovery is ON
    if not result.get("project_id") and is_discovery_on():
        from gapp.admin.sdk.deployments import discover_project_from_label
        result["project_id"] = discover_project_from_label(result["name"], env=env)

    # Fill github_repo from local git remote
    repo_path = result.get("repo_path")
    if repo_path:
        expanded = Path(repo_path).expanduser()
        if expanded.exists():
            try:
                gh = subprocess.run(
                    ["gh", "repo", "view", "--json", "nameWithOwner",
                     "--jq", ".nameWithOwner"],
                    capture_output=True, text=True, cwd=expanded,
                )
                if gh.returncode == 0 and gh.stdout.strip():
                    result["github_repo"] = gh.stdout.strip()
            except FileNotFoundError:
                pass

    return result
