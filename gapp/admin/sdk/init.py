"""gapp init — local project setup."""

import copy
import subprocess
from pathlib import Path

from gapp.admin.sdk.util import get_git_root
from gapp.admin.sdk.manifest import get_solution_name, load_manifest, save_manifest
from gapp.admin.sdk.start import missing, start_method
from gapp.admin.sdk.manifest_edit import name_source


def init_solution(repo_path: Path | None = None, *, fresh: bool = False) -> dict:
    """Initialize a gapp solution in the current repo.

    Idempotent — safe to call repeatedly. Creates gapp.yaml on first
    call, writes the deployment's name into it, and checks the file and
    the app's start method each time. Every other setting is changed with
    `gapp manifest` (`gapp.admin.sdk.manifest_edit`).

    How the app starts is never written here: it is the app's own
    Procfile (a web: line) or Dockerfile, which init only reports.

    Without `fresh`, a gapp.yaml gapp can't read or that fails the schema
    is never changed: the error says what's wrong and names `fresh`.
    With `fresh`, the current file (readable or not) is moved to
    gapp.yaml.bak (or .bak.1, .bak.2, ...) and a new one is written with
    only the name. Nothing is deleted. The name is the old file's when it
    can be read (its top-level `name:` line, for a file that isn't YAML),
    else derived as usual; `fresh_result` says which.

    Args:
        repo_path: Path to the repo. Defaults to cwd.
        fresh: Start over with a new gapp.yaml, keeping the old one as a backup.

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
        fresh_result: with fresh and an existing file, {"backup": file name,
            "old_name": the name read from the old file or None,
            "old_name_unknown": True when the old file's name couldn't be
            read, so the derived name may not be the deployment's}; else None
    """
    if repo_path:
        git_root = get_git_root(repo_path)
    else:
        git_root = get_git_root()
    if not git_root:
        raise RuntimeError("Not inside a git repository.")

    result = {"name": None, "manifest_status": None, "topic_status": None, "fresh_result": None}
    if fresh and (git_root / "gapp.yaml").exists():
        result["fresh_result"] = _start_over(git_root)

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

    if result["fresh_result"]:
        result["manifest_status"] = "replaced"

    # Add GitHub topic
    result["topic_status"] = _add_github_topic(git_root)

    result["start"] = start_method(git_root)
    result["next_step"] = None if result["start"]["method"] else {
        "action": "add a start method",
        "hint": missing(solution_name, result["start"]),
    }
    return result


def _start_over(git_root: Path) -> dict:
    """Move gapp.yaml aside and leave a new one holding only the old name, if readable."""
    path = git_root / "gapp.yaml"
    text = path.read_text()
    old_name, unknown = _old_name(text)
    backup = git_root / "gapp.yaml.bak"
    n = 1
    while backup.exists():
        backup = git_root / f"gapp.yaml.bak.{n}"
        n += 1
    path.rename(backup)
    save_manifest(git_root, {"name": old_name} if old_name else {})
    return {"backup": backup.name, "old_name": old_name, "old_name_unknown": unknown}


def _old_name(text: str) -> str | None:
    """The old file's deployment name and whether it is unknown. A file that
    parses gives its `name` (none means the derived name, which is known).
    One that doesn't gives its single top-level `name:` line; with none, or
    several, the old name is unknown."""
    import re
    from gapp.admin.sdk.fleet import NAME_RE
    from gapp.admin.sdk.manifest import parse_manifest_text
    from gapp.admin.sdk.schema import ManifestUnreadable
    try:
        name = parse_manifest_text(text).get("name")
        unknown = False
    except ManifestUnreadable:
        lines = re.findall(r"^name:[ \t]*[\"']?([^\s\"'#]+)[\"']?[ \t]*(?:#.*)?$", text, re.M)
        name = lines[0] if len(lines) == 1 else None
        unknown = name is None
    if not (isinstance(name, str) and NAME_RE.match(name)):
        return None, unknown or name is not None
    return name, False


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
