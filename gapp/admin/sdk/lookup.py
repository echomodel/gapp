"""Which deployment a command means.

A command names its deployment outright, or runs in a clone and means the
deployment of that clone's repo. Two rules hold throughout:

- A folder's name may suggest, never select. It is only ever the proposed
  name of a deployment that does not exist yet.
- A remote names a repo, not a deployment. The remote finds candidates and
  the fleet decides: one match is used, several are refused.

Everything here is pure: the fleet document and the clone's remotes come
in as values. `clone_remotes` is the one function that runs git.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

GITHUB_HOST = "github.com"


@dataclass(frozen=True)
class Remote:
    """A git remote's repo, apart from how the URL was spelled."""
    host: str
    path: str                    # owner/repo, or a longer path on hosts that nest

    @property
    def repo(self) -> str:
        """As a fleet definition records it: `owner/repo` on GitHub, `host/path` elsewhere."""
        return self.path if self.host == GITHUB_HOST else f"{self.host}/{self.path}"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$")
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", re.IGNORECASE)


def parse_remote(url: str) -> Remote | None:
    """The host and repo path of a remote URL (https, ssh, git, or scp-like), or None."""
    url = (url or "").strip()
    match = _URL.match(url) or _SCP_LIKE.match(url)
    if not match:
        return None
    host, path = match.group(1).lower(), match.group(2).strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    path = path.strip("/")
    if not path or "/" not in path:
        return None
    return Remote(host, path)


def clone_remotes(repo_path: Path) -> dict[str, Remote]:
    """The remotes of the clone at `repo_path`, by remote name. Empty when
    there are none, the folder is not a clone, or git is missing."""
    try:
        res = subprocess.run(["git", "-C", str(repo_path), "remote", "-v"],
                             capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if res.returncode != 0:
        return {}
    remotes = {}
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)":
            parsed = parse_remote(parts[1])
            if parsed:
                remotes[parts[0]] = parsed
    return remotes


class LookupRefusal(Exception):
    """The command cannot tell which deployment is meant. The message says what to type."""


def pick_remote(remotes: dict[str, Remote]) -> Remote | None:
    """The remote that identifies the clone: `origin`, else the only one.
    None when the clone has no remote. Several remotes and no `origin` is
    refused: gapp never picks among them."""
    if not remotes:
        return None
    if "origin" in remotes:
        return remotes["origin"]
    if len(remotes) == 1:
        return next(iter(remotes.values()))
    listed = ", ".join(f"{name} ({remote.repo})" for name, remote in sorted(remotes.items()))
    raise LookupRefusal(
        f"This clone has several remotes and none is called origin: {listed}. gapp does not choose "
        f"between them. Name the deployment: add its name to the command."
    )


def _same_repo(recorded: str | None, remote: Remote) -> bool:
    return bool(recorded) and recorded.lower() == remote.repo.lower()


@dataclass(frozen=True)
class Found:
    """What a lookup found. `name` is set when exactly one deployment is
    meant; otherwise `proposed` is the name a new deployment would get."""
    name: str | None = None
    entry: dict | None = None
    how: str | None = None              # "named", "remote", "gapp.yaml"
    proposed: str | None = None
    repo: str | None = None             # the clone's repo, as a definition would record it
    candidates: list[str] = field(default_factory=list)

    @property
    def is_new(self) -> bool:
        return self.name is None


def _command_lines(doc: dict, names: list[str], command: str) -> str:
    return "\n".join(f"  {command} {name}    ({doc[name].get('repo') or 'no repo'} on {doc[name].get('project')})"
                     for name in names)


def find(doc: dict, *, name: str | None = None, remotes: dict[str, Remote] | None = None,
         manifest_name: str | None = None, folder_name: str | None = None, project: str | None = None,
         command: str = "gapp deploy") -> Found:
    """The deployment a command means.

    name: given outright. It must be a deployment of the fleet.
    remotes: the clone's remotes when the command runs in one.
    manifest_name: `name:` from the clone's gapp.yaml. Used only when the
        clone has no remote, and then it matches only a definition that has
        no repo.
    folder_name: the clone's folder. Only ever the proposed name of a new
        deployment in a clone with no remote and no `name:`.
    project: narrows a repo's deployments to those on this project.
    command: the command being run, for the lines printed when several match.
    """
    if name:
        if name not in doc:
            raise LookupRefusal(_not_a_deployment(doc, name))
        return Found(name=name, entry=doc[name], how="named")

    remote = pick_remote(remotes or {})
    if remote is None:
        return _find_without_remote(doc, manifest_name, folder_name)

    names = sorted(n for n, entry in doc.items() if _same_repo(entry.get("repo"), remote))
    if project:
        on_project = [n for n in names if doc[n].get("project") == project]
        if names and not on_project:
            raise LookupRefusal(
                f"{remote.repo} has no deployment on {project}. Its deployments:\n"
                f"{_command_lines(doc, names, command)}"
            )
        names = on_project
    if len(names) == 1:
        return Found(name=names[0], entry=doc[names[0]], how="remote", repo=remote.repo)
    if names:
        raise LookupRefusal(
            f"{remote.repo} has {len(names)} deployments. Say which:\n{_command_lines(doc, names, command)}"
        )
    proposed = remote.name
    taken = doc.get(proposed)
    if taken is not None:
        owner = taken.get("repo") or "a deployment with no repo"
        raise LookupRefusal(
            f"{remote.repo} has no deployment in your fleet, and the name '{proposed}' belongs to {owner} "
            f"(on {taken.get('project')}). Give this repo's deployment another name."
        )
    return Found(proposed=proposed, repo=remote.repo)


def _find_without_remote(doc: dict, manifest_name: str | None, folder_name: str | None) -> Found:
    if manifest_name:
        entry = doc.get(manifest_name)
        if entry is None:
            return Found(proposed=manifest_name)
        if entry.get("repo"):
            raise LookupRefusal(
                f"This clone has no remote, and gapp.yaml names '{manifest_name}', which is {entry['repo']}'s "
                f"deployment. A deployment tied to a repo is only reached from a clone of that repo, or by "
                f"naming it on the command."
            )
        return Found(name=manifest_name, entry=entry, how="gapp.yaml")
    without_repo = sorted(n for n, entry in doc.items() if not entry.get("repo"))
    if without_repo:
        raise LookupRefusal(
            "This clone has no remote and its gapp.yaml has no `name:`, so gapp cannot tell which deployment "
            "it is. Name one on the command. Deployments with no repo: " + ", ".join(without_repo) + "."
        )
    return Found(proposed=folder_name)


def _not_a_deployment(doc: dict, name: str) -> str:
    if not doc:
        return f"'{name}' is not a deployment: your fleet holds none yet."
    return f"'{name}' is not a deployment in your fleet. It holds: {', '.join(sorted(doc))}."
