"""A fleet kept in a git repo: `gapp.yaml` at the repo's root.

Reading is a fetch; a change is one ordinary commit, pushed. Nothing here
knows about CI, runners or any one git host: it is plain git, using
whatever access to the repo this machine already has. gapp keeps its own
clone of the repo under the cache directory and never touches a clone of
yours.

A push that is rejected because the repo moved on in the meantime leaves
the repo as it was, and the command that asked is refused: run it again.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from gapp.admin.sdk import entryfile, lookup

FILE_NAME = entryfile.FILE_NAME


class FleetRepoError(RuntimeError):
    """The fleet's repo could not be read or changed. The message says why."""


def url_of(repo: str) -> str:
    """The URL git uses for a fleet repo as it is recorded: `owner/repo` is
    on GitHub, `host/path` is on that host, and a URL or a path is itself."""
    repo = repo.strip()
    if "://" in repo or repo.startswith(("/", "./", "../", "git@")) or re.match(r"^[^/@]+@[^:/]+:", repo):
        return repo
    first = repo.split("/", 1)[0]
    if "." in first:
        return f"https://{repo}.git"
    return f"https://{lookup.GITHUB_HOST}/{repo}.git"


def normalize(target: str) -> str:
    """A fleet repo as the profile records it. An https URL becomes
    `owner/repo` (GitHub) or `host/path`; anything else is kept as typed,
    so an ssh address or a path keeps working the way it was given."""
    target = (target or "").strip()
    if target.startswith(("https://", "http://")):
        remote = lookup.parse_remote(target)
        if remote is not None:
            return remote.repo
    return target.rstrip("/")


def cache_dir(repo: str) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "gapp" / "fleets" / re.sub(r"[^A-Za-z0-9._-]+", "_", repo).strip("_")


KEPT_LINES = 60      # of a failed git command's output, from the end


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _fail(repo: str, doing: str, result: subprocess.CompletedProcess) -> FleetRepoError:
    # All of what git and its hooks said, not the last line: a hook's reason
    # is in the lines above its closing summary.
    said = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part and part.strip())
    lines = said.splitlines()[-KEPT_LINES:]
    if not lines:
        return FleetRepoError(f"Your fleet is kept in {repo}, and {doing} failed: git gave no reason")
    if len(lines) == 1:
        return FleetRepoError(f"Your fleet is kept in {repo}, and {doing} failed: {lines[0]}")
    return FleetRepoError(f"Your fleet is kept in {repo}, and {doing} failed. Nothing was changed. "
                          f"git said:\n" + "\n".join(f"  {line}" for line in lines))


def _branch(clone: Path) -> str | None:
    """The branch the fleet is on: the repo's default branch, or None for a repo with no commit yet."""
    head = _git(["symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD"], clone)
    if head.returncode == 0 and "/" in head.stdout:
        return head.stdout.strip().split("/", 1)[1]
    listed = _git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], clone)
    names = [n.split("/", 1)[1] for n in listed.stdout.split() if "/" in n and not n.endswith("/HEAD")]
    for preferred in ("main", "master"):
        if preferred in names:
            return preferred
    return names[0] if names else None


def sync(repo: str) -> tuple[Path, str | None]:
    """gapp's clone of the fleet repo, brought to what the repo holds now. Returns it and its branch."""
    clone = cache_dir(repo)
    if not (clone / ".git").is_dir():
        clone.parent.mkdir(parents=True, exist_ok=True)
        made = _git(["clone", "--quiet", url_of(repo), str(clone)])
        if made.returncode != 0:
            raise _fail(repo, "reading it", made)
    else:
        fetched = _git(["fetch", "--quiet", "--prune", "origin"], clone)
        if fetched.returncode != 0:
            raise _fail(repo, "reading it", fetched)
        _git(["remote", "set-head", "origin", "--auto"], clone)
    branch = _branch(clone)
    if branch:
        for step in (["checkout", "--quiet", "-B", branch, f"origin/{branch}"], ["reset", "--quiet", "--hard", f"origin/{branch}"]):
            done = _git(step, clone)
            if done.returncode != 0:
                raise _fail(repo, "reading it", done)
    return clone, branch


def read_text(repo: str) -> str | None:
    """The fleet file's text as the repo holds it now, or None when the repo has none."""
    clone, _ = sync(repo)
    file = clone / FILE_NAME
    return file.read_text() if file.is_file() else None


def write_text(repo: str, text: str, message: str) -> bool:
    """Replace the fleet file with `text` as one commit, pushed. Returns
    False when the file already held exactly that."""
    clone, branch = sync(repo)
    file = clone / FILE_NAME
    if file.is_file() and file.read_text() == text:
        return False
    if branch is None:
        branch = "main"
        _git(["checkout", "--quiet", "-B", branch], clone)
    file.write_text(text)
    for step, doing in ((["add", FILE_NAME], "recording the change"),
                        (["commit", "--quiet", "-m", message, "--", FILE_NAME], "recording the change")):
        done = _git(step, clone)
        if done.returncode != 0:
            _undo(clone, branch)
            raise _fail(repo, doing, done)
    pushed = _git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], clone)
    if pushed.returncode != 0:
        _undo(clone, branch)
        if "fetch first" in pushed.stderr or "non-fast-forward" in pushed.stderr or "rejected" in pushed.stderr:
            raise FleetRepoError(
                f"Your fleet in {repo} changed while this ran, so nothing was changed. Run it again."
            )
        raise _fail(repo, "sending the change", pushed)
    return True


def show_last_change(repo: str) -> str | None:
    """The git command that shows the last change gapp's clone of the fleet repo holds."""
    clone = cache_dir(repo)
    head = _git(["rev-parse", "--short", "HEAD"], clone) if clone.is_dir() else None
    if head is None or head.returncode != 0 or not head.stdout.strip():
        return None
    return f"git -C {clone} show {head.stdout.strip()}"


def _undo(clone: Path, branch: str) -> None:
    """Put gapp's clone back to what the repo holds."""
    if _git(["rev-parse", "--verify", "-q", f"origin/{branch}"], clone).returncode == 0:
        _git(["reset", "--quiet", "--hard", f"origin/{branch}"], clone)
    else:
        _git(["update-ref", "-d", f"refs/heads/{branch}"], clone)
        _git(["rm", "--quiet", "--cached", "-f", FILE_NAME], clone)
        (clone / FILE_NAME).unlink(missing_ok=True)
