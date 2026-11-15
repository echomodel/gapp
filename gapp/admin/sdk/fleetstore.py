"""Where the fleet lives, and reading and writing it there.

The fleet is the set of deployment definitions, kept in the entry file
(`entryfile.py`). It lives in exactly one place for a profile:

- a file on this machine, the default; or
- a git repo, once one is set (`gapp fleet use`), as `gapp.yaml` at its
  root (`fleetrepo.py`).

There is never a second copy, so nothing here syncs. Which of the two it
is comes from one explicit setting, the profile's fleet repo; nothing is
detected.

A local fleet is changed by replacing the file, keeping the copy it
replaces beside it. A fleet in a repo is changed by one commit, pushed.
Neither involves CI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from gapp.admin.sdk import entryfile, fleetrepo
from gapp.admin.sdk.config import get_active_profile, get_config_dir, get_fleet_repo
from gapp.admin.sdk.entryfile import EntryFileRefusal
from gapp.admin.sdk.fleet import FleetRefusal

LOCAL_FILE_NAME = entryfile.FILE_NAME
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
            return f"{self.repo} ({entryfile.FILE_NAME})"
        return f"a file on this machine ({self.path})"


def local_path(profile: str | None = None) -> Path:
    """The local fleet file of a profile (the active one by default)."""
    return get_config_dir() / "fleets" / (profile or get_active_profile()) / LOCAL_FILE_NAME


FLEET_FILE_ENV = "GAPP_FLEET_FILE"


def location() -> Location:
    """Where the fleet lives: the profile's fleet repo when one is set, else
    the local file. A workflow on a runner has already checked the fleet
    repo out, and names the file there with GAPP_FLEET_FILE; nothing is
    fetched and nothing is written back."""
    named = os.environ.get(FLEET_FILE_ENV)
    if named:
        return Location("local", path=Path(named))
    repo = get_fleet_repo()
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
            return entryfile.parse(where.path.read_text(), f"Your fleet file {where.path}")["deployments"]
        except EntryFileRefusal as e:
            raise FleetUnavailable(_local_unreadable(where, e.message)) from e
        except OSError as e:
            raise FleetUnavailable(f"Your fleet file {where.path} could not be read: {e}.") from e
    try:
        return entryfile.parse(fleetrepo.read_text(where.repo), f"{entryfile.FILE_NAME} in {where.repo}")["deployments"]
    except EntryFileRefusal as e:
        raise FleetUnavailable(e.message) from e
    except fleetrepo.FleetRepoError as e:
        raise FleetUnavailable(f"{e} Nothing was assumed about what it holds.") from e


def _local_unreadable(where: Location, why: str) -> str:
    text = why
    if where.backup_path.exists():
        text += f" The copy from before its last change is {where.backup_path}."
    return text


def write(doc: dict, where: Location | None = None, message: str = "gapp: change the fleet",
          other_keys: dict | None = None) -> Path | None:
    """Replace the fleet's deployments with `doc`.

    A local fleet: the file is replaced, and the one it replaces is kept
    beside it as the backup, whose path is returned (None for the first
    write). A fleet in a repo: one commit with `message`, pushed.
    Top-level keys this gapp does not know are kept either way (or, for
    an import, replaced by `other_keys`), and a file this gapp cannot read
    is never written over.
    """
    where = where or location()
    if not isinstance(doc, dict) or not all(isinstance(entry, dict) for entry in doc.values()):
        raise FleetRefusal("bad-file", "A fleet is a mapping of deployment names to their definitions.")
    if where.kind != "local":
        try:
            whole = entryfile.parse(fleetrepo.read_text(where.repo), f"{entryfile.FILE_NAME} in {where.repo}")
            whole = _with(whole, doc, other_keys)
            fleetrepo.write_text(where.repo, entryfile.dump(whole), message)
        except EntryFileRefusal as e:
            raise FleetUnavailable(e.message) from e
        except fleetrepo.FleetRepoError as e:
            raise FleetUnavailable(str(e)) from e
        return None
    whole = entryfile.empty()
    if where.path.exists():
        try:
            whole = entryfile.parse(where.path.read_text(), f"Your fleet file {where.path}")
        except EntryFileRefusal as e:                    # never write over a file this gapp cannot read
            raise FleetUnavailable(_local_unreadable(where, e.message)) from e
    text = entryfile.dump(_with(whole, doc, other_keys))
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


def _with(whole: dict, deployments: dict, other_keys: dict | None) -> dict:
    if other_keys is not None:
        whole = {"format": whole.get("format", entryfile.FORMAT), **other_keys}
    whole["deployments"] = deployments
    return whole


# --- copying the fleet out and in ---


def export_text(where: Location | None = None) -> str:
    """The whole fleet as the text of an entry file."""
    where = where or location()
    try:
        if where.kind == "local":
            text = where.path.read_text() if where.path.exists() else None
            return entryfile.dump(entryfile.parse(text, f"Your fleet file {where.path}"))
        return entryfile.dump(entryfile.parse(fleetrepo.read_text(where.repo), f"{entryfile.FILE_NAME} in {where.repo}"))
    except EntryFileRefusal as e:
        raise FleetUnavailable(e.message) from e
    except (OSError, fleetrepo.FleetRepoError) as e:
        raise FleetUnavailable(f"Your fleet could not be read: {e}") from e


def import_text(text: str, source: str = "The file") -> dict:
    """Replace the fleet with the one in `text`, all of it or none of it.

    Every definition is checked first. The fleet it replaces is kept: a
    local fleet's file as the backup beside it, a fleet in a repo as the
    commit before this one.
    """
    from gapp.admin.sdk import fleet, settings as settings_mod
    if not (text or "").strip():
        raise FleetRefusal("bad-file", f"{source} is empty, so nothing was imported.")
    try:
        whole = entryfile.parse(text, source)
    except EntryFileRefusal as e:
        raise FleetRefusal(e.code, e.message) from e
    deployments = whole["deployments"]
    ids = {}
    for name, definition in deployments.items():
        try:
            fleet.validate_name(str(name))
            for key in ("id", "project", "gapp"):
                if not definition.get(key):
                    raise FleetRefusal("bad-file", f"it has no '{key}'.")
            fleet.validate_project(str(definition["project"]))
            entryfile.version_key(definition["gapp"])
            settings_mod.validate(definition.get("settings"))
        except (FleetRefusal, EntryFileRefusal, settings_mod.SettingsRefusal) as e:
            raise FleetRefusal("bad-file", f"{source}: '{name}' cannot be imported: {e.message} Nothing was imported.") from e
        other = ids.setdefault((definition["project"], definition["id"]), name)
        if other != name:
            raise FleetRefusal("bad-file", (
                f"{source}: '{name}' and '{other}' have the same id, '{definition['id']}', on {definition['project']}, "
                f"so they would be one deployment. Nothing was imported."))
    where = location()
    before = read(where)
    other_keys = {k: v for k, v in whole.items() if k not in ("format", "deployments")}
    backup = write(deployments, where, "gapp: import the fleet", other_keys=other_keys)
    return {"where": where.describe(), "deployments": len(deployments), "replaced": len(before),
            "backup": str(backup) if backup else None, "kind": where.kind}


def use(target: str, *, create: bool = False) -> dict:
    """Move the fleet: to a repo, or back to this machine with "local".

    The fleet is carried across when the place it is going holds none.
    When both hold deployments the move is refused: there is never a
    second copy, and gapp does not merge two fleets.
    """
    from gapp.admin.sdk.config import set_fleet_repo
    now = location()
    target = (target or "").strip()
    if not target:
        raise FleetRefusal("invalid-input", "Say where: gapp fleet use <owner>/<repo>, or gapp fleet use local.")
    then = Location("local", path=local_path()) if target == "local" else Location("github", repo=fleetrepo.normalize(target))
    if (now.kind, now.repo) == (then.kind, then.repo):
        return {"moved": False, "where": now.describe(), "deployments": len(read(now)), "created": False}
    created = False
    if create and then.kind == "github":
        created = _create_repo(then.repo)
    here = read(now)
    there = read(then)
    if here and there:
        raise FleetRefusal("two-fleets", (
            f"Both hold deployments: {now.describe()} has {len(here)} and {then.describe()} has {len(there)}. "
            f"gapp does not merge two fleets. Empty one of them first, or keep using the one you have."
        ))
    if here:
        write(here, then, "gapp: the fleet moves here")
    set_fleet_repo(then.repo if then.kind == "github" else None)
    if here and now.kind == "local" and now.path.exists():
        os.replace(now.path, now.backup_path)            # the fleet is in one place; keep the old file as a backup
    return {"moved": True, "where": then.describe(), "deployments": len(here or there), "created": created,
            "carried": bool(here), "backup": str(now.backup_path) if here and now.kind == "local" else None}


def _create_repo(repo: str) -> bool:
    """Make the repo on GitHub when it is not there, private. True when it was made."""
    import subprocess
    if "/" not in repo or "." in repo.split("/", 1)[0]:
        raise FleetRefusal("invalid-input", "--create makes a repo on GitHub, so it takes <owner>/<repo>.")
    try:
        seen = subprocess.run(["gh", "repo", "view", repo], capture_output=True, text=True)
        if seen.returncode == 0:
            return False
        made = subprocess.run(["gh", "repo", "create", repo, "--private"], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise FleetUnavailable("--create needs the GitHub CLI (`gh`), which is not installed.") from e
    if made.returncode != 0:
        raise FleetUnavailable(f"{repo} could not be created: {(made.stderr or made.stdout).strip()}")
    return True


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
