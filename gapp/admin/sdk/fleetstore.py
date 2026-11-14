"""Where the fleet lives, and reading and writing it there.

The fleet is the set of deployment definitions (`fleet.py` holds their
shape and rules). It lives in exactly one place for a profile:

- a file on this machine, the default; or
- a GitHub repo, once one is set (`gapp ci init`), at `gapp/deployments.yaml`.

There is never a second copy, so nothing here syncs. Which of the two it
is comes from one explicit setting, the profile's fleet repo; nothing is
detected.

A local fleet is changed here: the file is replaced, and the copy it
replaces is kept beside it. A GitHub fleet is read here and changed by a
run in that repo (`gapp ci setup`), which records each change as a commit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gapp.admin.sdk import fleet
from gapp.admin.sdk.config import get_active_profile, get_config_dir, get_fleet_repo
from gapp.admin.sdk.fleet import FleetRefusal

LOCAL_FILE_NAME = "deployments.yaml"
BACKUP_SUFFIX = ".bak"


class FleetUnavailable(RuntimeError):
    """The fleet could not be read. The message says why and what to do."""


@dataclass(frozen=True)
class Location:
    """Where the active profile's fleet lives."""
    kind: str                    # "local" or "github"
    repo: str | None = None      # github: owner/repo
    path: Path | None = None     # local: the file

    @property
    def backup_path(self) -> Path | None:
        return self.path.with_name(self.path.name + BACKUP_SUFFIX) if self.path else None

    def describe(self) -> str:
        if self.kind == "github":
            return f"{self.repo} on GitHub ({fleet.DEPLOYMENTS_FILE})"
        return f"a file on this machine ({self.path})"


def local_path(profile: str | None = None) -> Path:
    """The local fleet file of a profile (the active one by default)."""
    return get_config_dir() / "fleets" / (profile or get_active_profile()) / LOCAL_FILE_NAME


def location() -> Location:
    """Where the active profile's fleet lives: its fleet repo when one is set, else the local file."""
    repo, _ = get_fleet_repo()
    if repo:
        return Location("github", repo=repo)
    return Location("local", path=local_path())


def read(where: Location | None = None) -> dict:
    """The fleet's deployments, name -> definition. A local fleet with no
    file yet is an empty fleet. Raises FleetUnavailable when it cannot be
    read; an unreadable fleet is never treated as an empty one."""
    where = where or location()
    if where.kind == "local":
        if not where.path.exists():
            return {}
        try:
            return fleet.parse(where.path.read_text())
        except FleetRefusal as e:
            raise FleetUnavailable(_local_unreadable(where, e.message)) from e
        except OSError as e:
            raise FleetUnavailable(f"Your fleet file {where.path} could not be read: {e}.") from e
    from gapp.admin.sdk import ci
    doc, rd = ci._load_fleet(where.repo)
    if doc is not None:
        return doc
    if rd.absent:
        raise FleetUnavailable(
            f"{where.repo} has no {fleet.DEPLOYMENTS_FILE}, so your fleet there holds nothing yet. "
            f"Install gapp's files in it: gapp ci init {where.repo}"
        )
    raise FleetUnavailable(
        f"Your fleet is kept in {where.repo} on GitHub, and it could not be read: {rd.note} "
        f"Nothing was assumed about what it holds."
    )


def _local_unreadable(where: Location, why: str) -> str:
    text = f"Your fleet file {where.path} is not a fleet gapp can read: {why}"
    if where.backup_path.exists():
        text += f" The copy from before its last change is {where.backup_path}."
    return text


def write_local(doc: dict, where: Location | None = None) -> Path | None:
    """Replace the local fleet with `doc`. The file it replaces is kept
    beside it as the backup, whose path is returned (None for the first
    write). Refuses a GitHub fleet: that is changed by a run in its repo."""
    where = where or location()
    if where.kind != "local":
        raise FleetUnavailable(
            f"Your fleet is kept in {where.repo} on GitHub, not on this machine. "
            f"Change it there with `gapp ci setup`."
        )
    if not isinstance(doc, dict) or not all(isinstance(entry, dict) for entry in doc.values()):
        raise FleetRefusal("bad-file", "A fleet is a mapping of deployment names to their definitions.")
    text = fleet.dump(doc)
    where.path.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if where.path.exists():
        backup = where.backup_path
        os.replace(where.path, backup)
    pending = where.path.with_name(where.path.name + ".new")
    try:
        pending.write_text(text)
        os.replace(pending, where.path)
    except OSError:
        if backup is not None and not where.path.exists():
            os.replace(backup, where.path)              # leave the fleet as it was
        raise
    return backup


def describe_fleet() -> dict:
    """Where the fleet lives and how many deployments it holds, for `gapp fleet`."""
    where = location()
    result = {"kind": where.kind, "repo": where.repo, "path": str(where.path) if where.path else None,
              "where": where.describe(), "deployments": None, "backup": None, "problem": None}
    if where.kind == "local" and where.backup_path.exists():
        result["backup"] = str(where.backup_path)
    try:
        result["deployments"] = len(read(where))
    except FleetUnavailable as e:
        result["problem"] = str(e)
    return result
