"""gapp ci — CI/CD automation for gapp solutions."""

import json
import subprocess

from gapp.admin.sdk.config import get_config_dir

import yaml


_CI_TOPIC = "gapp-ci"


def _load_ci_config() -> dict:
    """Load CI config from XDG config dir."""
    path = get_config_dir() / "ci.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_ci_config(config: dict) -> None:
    """Save CI config to XDG config dir."""
    path = get_config_dir() / "ci.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def _resolve_repo(repo: str) -> str:
    """Resolve repo arg to owner/name. If no owner, default to gh user."""
    if "/" in repo:
        return repo
    # Get authenticated gh user
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not determine GitHub user. Run 'gh auth login' first."
        )
    owner = result.stdout.strip()
    return f"{owner}/{repo}"


def _find_ci_repo_by_topic() -> str | None:
    """Find a repo tagged with gapp-ci topic for the authenticated user."""
    result = subprocess.run(
        ["gh", "search", "repos", "--topic", _CI_TOPIC, "--owner", "@me",
         "--json", "fullName"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    repos = json.loads(result.stdout)
    if repos:
        return repos[0]["fullName"]
    return None


def init_ci(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo.

    1. Resolve repo to owner/name
    2. Write to XDG config (ci.yaml)
    3. Tag the repo with gapp-ci topic (unless local_only)
    4. Ensure only one repo has the topic

    Returns dict describing what was done.
    """
    full_name = _resolve_repo(repo)

    result = {
        "repo": full_name,
        "config_status": None,
        "topic_status": None,
    }

    # Write to local config
    config = _load_ci_config()
    config["repo"] = full_name
    _save_ci_config(config)
    result["config_status"] = "saved"

    if local_only:
        result["topic_status"] = "skipped"
        return result

    # Check if another repo already has the topic
    existing = _find_ci_repo_by_topic()
    if existing and existing != full_name:
        raise RuntimeError(
            f"Another repo already has the {_CI_TOPIC} topic: {existing}\n"
            f"  Remove the topic from that repo first, or use --local-only."
        )

    if existing == full_name:
        result["topic_status"] = "already_set"
        return result

    # Ensure repo exists
    check = subprocess.run(
        ["gh", "repo", "view", full_name, "--json", "name"],
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        # Create it as private
        create = subprocess.run(
            ["gh", "repo", "create", full_name, "--private",
             "--description", "gapp CI/CD deployment workflows"],
            capture_output=True,
            text=True,
        )
        if create.returncode != 0:
            raise RuntimeError(
                f"Failed to create repo {full_name}: {create.stderr.strip()}"
            )
        result["repo_created"] = True

    # Add topic
    subprocess.run(
        ["gh", "repo", "edit", full_name, "--add-topic", _CI_TOPIC],
        capture_output=True,
        text=True,
    )
    result["topic_status"] = "added"

    return result


def get_ci_status() -> dict:
    """Check CI configuration state.

    Returns dict with:
        repo: owner/name of CI repo (or None)
        source: "local" | "remote" | None
        local_config: bool (ci.yaml exists and has repo)
        remote_config: str | None (repo found via topic)
    """
    result = {
        "repo": None,
        "source": None,
        "local_config": False,
        "remote_config": None,
    }

    # Check local config
    config = _load_ci_config()
    local_repo = config.get("repo")
    if local_repo:
        result["local_config"] = True
        result["repo"] = local_repo
        result["source"] = "local"

    # Check remote (don't fail if gh is not available or not authenticated)
    try:
        remote_repo = _find_ci_repo_by_topic()
        result["remote_config"] = remote_repo
        if not result["repo"] and remote_repo:
            result["repo"] = remote_repo
            result["source"] = "remote"
    except Exception:
        pass

    return result


def get_ci_repo() -> str | None:
    """Get the CI repo name. Local config takes priority over remote."""
    status = get_ci_status()
    return status.get("repo")
