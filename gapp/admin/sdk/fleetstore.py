"""Where the fleet lives, and reading and writing it there.

The fleet is the set of deployment definitions, kept in the entry file
(`entryfile.py`). It lives in exactly one place for a profile:

- the profile's own file on this machine, the default;
- a file the operator named (`gapp fleet use /path/gapp.yaml`); or
- a git repo (`gapp fleet use owner/repo`), as `gapp.yaml` at its root
  (`fleetrepo.py`).

There is never a second copy, so nothing here syncs. Which it is comes
from one explicit setting, the profile's, or for one process from the
GAPP_FLEET variable, which takes exactly what `gapp fleet use` takes
(`target`). Nothing is detected.

A fleet in a file is changed by replacing the file; the copy it replaces
is kept as a backup in gapp's state folder, never beside the file, so a
config folder or a repo holds only what was put there. A fleet in a repo
is changed by one commit, pushed, and git is its history. Neither
involves CI.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from gapp.admin.sdk import entryfile, fleetrepo
from gapp.admin.sdk.config import FLEET_ENV, get_active_config, get_active_profile, get_config_dir
from gapp.admin.sdk.entryfile import EntryFileRefusal
from gapp.admin.sdk.fleet import FleetRefusal

LOCAL_FILE_NAME = entryfile.FILE_NAME
FILE_SUFFIXES = (".yaml", ".yml")
BACKUP_HEADER = "# gapp backup"


class FleetUnavailable(RuntimeError):
    """The fleet could not be read. The message says why and what to do."""


@dataclass(frozen=True)
class Location:
    """Where the fleet lives."""
    kind: str                    # "local" (a file), "github" (a git repo) or "unusable"
    repo: str | None = None      # github: owner/repo, or another git address
    path: Path | None = None     # local: the file
    named: bool = False          # local: a file the operator named, not the profile's own
    set_by: str | None = None    # FLEET_ENV when the variable chose it, not the profile
    problem: str | None = None   # unusable: why

    def describe(self) -> str:
        if self.kind == "unusable":
            return f"nowhere gapp can use: {self.problem}"
        if self.kind == "github":
            text = f"{self.repo} ({entryfile.FILE_NAME})"
        elif self.named:
            text = f"the file {self.path}" + ("" if self.path.exists() else " (no file yet)")
        else:
            text = f"a file on this machine ({self.path})"
        return text + (f", set by {self.set_by}" if self.set_by else "")


def local_path(profile: str | None = None) -> Path:
    """The local fleet file of a profile (the active one by default)."""
    return get_config_dir() / "fleets" / (profile or get_active_profile()) / LOCAL_FILE_NAME


def target(value: str) -> Location:
    """Where a fleet named as `gapp fleet use` and GAPP_FLEET take it is.

    `local` is the profile's own file. A full path (starting with / or ~)
    ending in .yaml or .yml is that fleet file, which may not exist yet.
    Anything else is a git repo, as `fleetrepo` reads it: owner/repo,
    host/path, a URL, an ssh address, or a full path to a repo. A relative
    path is refused: it would mean a different fleet in every folder.
    """
    value = (value or "").strip()
    if not value:
        raise FleetRefusal("invalid-input", "Say where: <owner>/<repo>, local, or the full path of a fleet file.")
    if value == "local":
        return Location("local", path=local_path())
    is_url = "://" in value or value.startswith("git@")
    if not is_url and value.lower().endswith(FILE_SUFFIXES):
        if not value.startswith(("/", "~")):
            raise FleetRefusal("invalid-input", _relative(value))
        return Location("local", path=Path(value).expanduser().resolve(), named=True)
    if value in (".", "..") or value.startswith(("./", "../")):
        raise FleetRefusal("invalid-input", _relative(value))
    if value.startswith("~"):
        value = os.path.expanduser(value)
    return Location("github", repo=fleetrepo.normalize(value))


def _relative(value: str) -> str:
    return (f"'{value}' is a relative path, which would mean a different fleet in every folder. "
            f"Give the full path, starting with / or ~ (for this folder: $PWD/{value.removeprefix('./')}).")


def location() -> Location:
    """Where the fleet lives: what GAPP_FLEET names when it is set, else
    the profile's setting, else the profile's own file. A GAPP_FLEET that
    names nothing gapp can use makes every read and write fail with why;
    it never falls back to the profile."""
    named = (os.environ.get(FLEET_ENV) or "").strip()
    if named:
        try:
            return replace(target(named), set_by=FLEET_ENV)
        except FleetRefusal as e:
            return Location("unusable", set_by=FLEET_ENV,
                            problem=f"{FLEET_ENV} is set to '{named}'. {e.message}")
    held = get_active_config()
    if held.get("fleet_file"):
        return Location("local", path=Path(held["fleet_file"]), named=True)
    if held.get("fleet_repo"):
        return Location("github", repo=held["fleet_repo"])
    return Location("local", path=local_path())


# --- backups of a fleet kept in a file ---


def backups_dir() -> Path:
    """gapp's state folder for backups, respecting XDG_STATE_HOME. State,
    not config: a dotfile manager leaves it alone."""
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "gapp" / "backups"


def _keep(where: Location, text: str, before: str) -> Path:
    """Keep `text`, the fleet file as it was, as a new backup; return its path.

    One file per change, named by the time; a comment header says which
    fleet it is a copy of, so the backup is itself a fleet file
    `gapp fleet import` takes as it is.
    """
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S-") + f"{now.microsecond // 1000:03d}Z"
    header = (f"{BACKUP_HEADER}\n# fleet: {where.path}\n"
              f"# taken: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}, before: {before}\n")
    body = text if text.endswith("\n") or not text else text + "\n"
    name = f"{stamp}.yaml"
    while True:
        path = folder / name
        try:
            with open(path, "x") as f:
                f.write(header + body)
            return path
        except FileExistsError:
            name = f"{stamp}-{secrets.token_hex(2)}.yaml"


def latest_backup(where: Location) -> Path | None:
    """The newest backup of the fleet kept in `where`'s file, or None."""
    if where.kind != "local" or where.path is None:
        return None
    folder = backups_dir()
    if not folder.is_dir():
        return None
    wanted = f"# fleet: {where.path}"
    for path in sorted(folder.glob("*.yaml"), reverse=True):
        try:
            with open(path) as f:
                head = [f.readline().rstrip("\n") for _ in range(2)]
        except OSError:
            continue
        if head == [BACKUP_HEADER, wanted]:
            return path
    return None


def read(where: Location | None = None) -> dict:
    """The fleet's deployments, name -> definition. A local fleet with no
    file yet is an empty fleet. Raises FleetUnavailable when it cannot be
    read; an unreadable fleet is never treated as an empty one."""
    where = where or location()
    if where.kind == "unusable":
        raise FleetUnavailable(where.problem)
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
    backup = latest_backup(where)
    if backup:
        text += f" The copy from before its last change is {backup}."
    return text


def write(doc: dict, where: Location | None = None, message: str = "gapp: change the fleet",
          other_keys: dict | None = None) -> Path | None:
    """Replace the fleet's deployments with `doc`.

    A fleet in a file: the file is replaced, and the one it replaces is
    kept as a new backup (`_keep`), whose path is returned (None for the
    first write). A fleet in a repo: one commit with `message`, pushed.
    Top-level keys this gapp does not know are kept either way (or, for
    an import, replaced by `other_keys`), and a file this gapp cannot read
    is never written over.
    """
    where = where or location()
    if not isinstance(doc, dict) or not all(isinstance(entry, dict) for entry in doc.values()):
        raise FleetRefusal("bad-file", "A fleet is a mapping of deployment names to their definitions.")
    if where.kind == "unusable":
        raise FleetUnavailable(where.problem)
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
    whole, before = entryfile.empty(), None
    if where.path.exists():
        before = where.path.read_text()
        try:
            whole = entryfile.parse(before, f"Your fleet file {where.path}")
        except EntryFileRefusal as e:                    # never write over a file this gapp cannot read
            raise FleetUnavailable(_local_unreadable(where, e.message)) from e
    text = entryfile.dump(_with(whole, doc, other_keys))
    where.path.parent.mkdir(parents=True, exist_ok=True)
    backup = _keep(where, before, message.removeprefix("gapp: ")) if before is not None else None
    pending = where.path.with_name(where.path.name + ".new")
    try:
        pending.write_text(text)
        os.replace(pending, where.path)              # the fleet is either as it was or as it is now
    finally:
        pending.unlink(missing_ok=True)
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
    fleet in a file as a backup (`_keep`), a fleet in a repo as the commit
    before this one.
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


def use(to: str, *, create: bool = False) -> dict:
    """Move the fleet: to a repo, to a file, or back to the profile's own
    file with "local". `to` takes the forms `target` reads.

    The fleet is carried across when the place it is going holds none.
    When both hold deployments the move is refused: there is never a
    second copy, and gapp does not merge two fleets. Refused while
    GAPP_FLEET is set, since the profile's setting would not be what
    this process uses.
    """
    from gapp.admin.sdk.config import set_fleet
    named = (os.environ.get(FLEET_ENV) or "").strip()
    if named:
        raise FleetRefusal("set-by-variable", (
            f"{FLEET_ENV} is set to {named}, so that is the fleet gapp uses, whatever this profile says. "
            f"Changing the profile would not change that. Unset {FLEET_ENV} to move the profile's fleet."))
    now = location()
    then = target(to)
    if (now.kind, now.repo, now.path) == (then.kind, then.repo, then.path):
        return {"moved": False, "where": now.describe(), "deployments": len(read(now)), "created": False}
    created = False
    if create:
        if then.kind != "github":
            raise FleetRefusal("invalid-input", "--create makes a repo on GitHub, so it takes <owner>/<repo>.")
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
    set_fleet(repo=then.repo if then.kind == "github" else None,
              file=str(then.path) if then.named else None)
    backup = None
    if here and now.kind == "local" and now.path.exists():
        backup = _keep(now, now.path.read_text(), f"the fleet moves to {then.describe()}")
        now.path.unlink()                                # the fleet is in one place; the backup keeps the old file
    return {"moved": True, "where": then.describe(), "deployments": len(here or there), "created": created,
            "carried": bool(here), "backup": str(backup) if backup else None}


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
              "set_by": where.set_by, "where": where.describe(), "deployments": None, "backup": None,
              "problem": None}
    backup = latest_backup(where)
    if backup:
        result["backup"] = str(backup)
    try:
        result["deployments"] = len(read(where))
    except FleetUnavailable as e:
        result["problem"] = str(e)
    return result
