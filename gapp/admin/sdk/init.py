"""gapp init — local project setup."""

import copy
import subprocess
from pathlib import Path

from gapp.admin.sdk.util import get_git_root
from gapp.admin.sdk.manifest import get_solution_name, load_manifest, save_manifest
from gapp.admin.sdk.start import missing, start_method
from gapp.admin.sdk.manifest_edit import name_source


def init_solution(repo_path: Path | None = None) -> dict:
    """Initialize a gapp solution in the current repo.

    Idempotent — safe to call repeatedly. Creates gapp.yaml on first
    call, writes the deployment's name into it, and checks the file and
    the app's start method each time. Every other setting is changed with
    `gapp manifest` (`gapp.admin.sdk.manifest_edit`).

    How the app starts is never written here: it is the app's own
    Procfile (a web: line) or Dockerfile, which init only reports.

    Args:
        repo_path: Path to the repo. Defaults to cwd.

    Returns a dict describing what was done:
        name: solution name
        name_source: where the name came from before this run: "gapp.yaml",
            "repo" (the origin remote) or "folder"; init writes it to gapp.yaml
        manifest_status: "created" | "updated" | "unchanged"
        topic_status: "added" | "already_set" | "skipped"
        start: how the app starts ({"method": "dockerfile" | "procfile" |
            None, "web", "ignored", "reason"})
        next_step: {"action", "hint"} when the app has no start method yet,
            else None
    """
    if repo_path:
        git_root = get_git_root(repo_path)
    else:
        git_root = get_git_root()
    if not git_root:
        raise RuntimeError("Not inside a git repository.")

    result = {"name": None, "manifest_status": None, "topic_status": None}

    # gapp.yaml is written once, at the end, and only when its content
    # changes: a run that changes nothing leaves the file as it is.
    exists = (git_root / "gapp.yaml").exists()
    before = load_manifest(git_root) if exists else {}
    manifest = copy.deepcopy(before)

    solution_name = get_solution_name(manifest, git_root)
    result["name"] = solution_name
    result["name_source"] = name_source(manifest, git_root)
    if manifest.get("name") != solution_name:
        manifest = {"name": solution_name, **{k: v for k, v in manifest.items() if k != "name"}}

    if not exists:
        save_manifest(git_root, manifest)
        result["manifest_status"] = "created"
    elif manifest != before:
        save_manifest(git_root, manifest)
        result["manifest_status"] = "updated"
    else:
        result["manifest_status"] = "unchanged"

    # Add GitHub topic
    result["topic_status"] = _add_github_topic(git_root)

    result["start"] = start_method(git_root)
    result["next_step"] = None if result["start"]["method"] else {
        "action": "add a start method",
        "hint": missing(solution_name, result["start"]),
    }
    return result


def _add_github_topic(repo_path: Path) -> str:
    """Add gapp-solution topic to the GitHub repo."""
    try:
        # Check current topics
        check = subprocess.run(
            ["gh", "repo", "view", "--json", "repositoryTopics"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        if check.returncode != 0:
            return "skipped"

        import json
        data = json.loads(check.stdout)
        topics = [t["name"] for t in (data.get("repositoryTopics") or [])]

        if "gapp-solution" in topics:
            return "already_set"

        # Add topic
        subprocess.run(
            ["gh", "repo", "edit", "--add-topic", "gapp-solution"],
            capture_output=True,
            text=True,
            cwd=repo_path,
        )
        return "added"
    except FileNotFoundError:
        return "skipped"
