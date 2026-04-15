"""gapp init — local project setup."""

import subprocess
from pathlib import Path

from gapp.admin.sdk.config import load_solutions, save_solutions
from gapp.admin.sdk.context import get_git_root
from gapp.admin.sdk.manifest import get_solution_name, load_manifest, save_manifest


def init_solution(
    repo_path: Path | None = None,
    *,
    entrypoint: str | None = None,
    mcp_path: str | None = None,
    secrets: dict | None = None,
    domain: str | None = None,
) -> dict:
    """Initialize a gapp solution in the current repo.

    Idempotent — safe to call repeatedly. Creates gapp.yaml on first
    call, merges provided settings on every call. Only provided
    (non-None) parameters are written; omitted parameters leave
    existing values unchanged.

    Args:
        repo_path: Path to the repo. Defaults to cwd.
        entrypoint: ASGI entrypoint (module:app).
        mcp_path: MCP endpoint path (e.g., /mcp).
        secrets: Dict of secret names to descriptions for prerequisites.
        domain: Custom domain to map to the service (e.g., mcp.example.com).

    Returns a dict describing what was done:
        name: solution name
        manifest_status: "created" | "updated" | "unchanged"
        topic_status: "added" | "already_set" | "skipped"
        registered: bool
    """
    if repo_path:
        git_root = get_git_root(repo_path)
    else:
        git_root = get_git_root()
    if not git_root:
        raise RuntimeError("Not inside a git repository.")

    result = {"name": None, "manifest_status": None, "topic_status": None, "registered": False}

    # Ensure gapp.yaml exists
    manifest_path = git_root / "gapp.yaml"
    if manifest_path.exists():
        manifest = load_manifest(git_root)
        result["manifest_status"] = "unchanged"
    else:
        manifest = {}
        if entrypoint:
            manifest["service"] = {"entrypoint": entrypoint}
        result["manifest_status"] = "created"

    # Merge provided settings
    service = manifest.setdefault("service", {})
    changed = False

    if entrypoint is not None and service.get("entrypoint") != entrypoint:
        service["entrypoint"] = entrypoint
        changed = True

    if mcp_path is not None and service.get("mcp_path") != mcp_path:
        service["mcp_path"] = mcp_path
        changed = True

    if not service:
        manifest.pop("service", None)

    if domain is not None and manifest.get("domain") != domain:
        if domain:
            manifest["domain"] = domain
        else:
            manifest.pop("domain", None)
        changed = True

    if secrets is not None:
        prereqs = manifest.setdefault("prerequisites", {})
        existing_secrets = prereqs.setdefault("secrets", {})
        for name, desc in secrets.items():
            if name not in existing_secrets:
                existing_secrets[name] = {"description": desc}
                changed = True

    if changed or result["manifest_status"] == "created":
        save_manifest(git_root, manifest)
        if result["manifest_status"] != "created":
            result["manifest_status"] = "updated"

    manifest = load_manifest(git_root)
    solution_name = get_solution_name(manifest, git_root)
    result["name"] = solution_name

    # Add GitHub topic
    result["topic_status"] = _add_github_topic(git_root)

    # Register in solutions.yaml
    solutions = load_solutions()
    if solution_name not in solutions:
        solutions[solution_name] = {}
    solutions[solution_name]["repo_path"] = str(git_root)
    save_solutions(solutions)
    result["registered"] = True

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
