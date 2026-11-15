"""Defining deployments and changing their settings, in the fleet.

Every function here reads the fleet, changes one definition and writes
the fleet back. None touches the cloud. Each returns what it changed, as
before and after, so a command can say it.

Which deployment a command means is one rule (`which`): the name when it
is given, else the one definition whose repo is the clone's remote.

A definition records the gapp that wrote it. A change is refused unless
that is this gapp's version; `upgrade` is the one thing that moves it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from gapp import __version__
from gapp.admin.sdk import entryfile, fleet, fleetstore, lookup, settings as settings_mod
from gapp.admin.sdk.entryfile import EntryFileRefusal
from gapp.admin.sdk.fleet import FleetRefusal
from gapp.admin.sdk.fleetstore import FleetUnavailable
from gapp.admin.sdk.lookup import LookupRefusal
from gapp.admin.sdk.settings import SettingsRefusal

OWN_SETTABLE = ("ref", "repo")


class Refused(Exception):
    """The command was refused and nothing was changed. The message says why and what to do."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _guard(fn):
    """Every refusal a lower layer raises, as one kind for the commands."""
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (EntryFileRefusal, FleetRefusal, SettingsRefusal) as e:
            raise Refused(e.message) from e
        except (LookupRefusal, FleetUnavailable) as e:
            raise Refused(str(e)) from e
    wrapped.__name__, wrapped.__doc__ = fn.__name__, fn.__doc__
    return wrapped


# --- the fleet, and which deployment ---


def _load() -> tuple[fleetstore.Location, dict]:
    where = fleetstore.location()
    return where, fleetstore.read(where)


def _save(where: fleetstore.Location, deployments: dict, message: str = "gapp: change the fleet") -> None:
    fleetstore.write(deployments, where, message)


def which(deployments: dict, deployment: str | None, cwd: Path | None, command: str) -> str:
    """The name of the deployment a command means."""
    if deployment:
        return lookup.find(deployments, name=deployment).name
    remotes = lookup.clone_remotes(cwd or Path.cwd())
    remote = lookup.pick_remote(remotes)
    if remote is None:
        held = f" Your fleet holds: {', '.join(sorted(deployments))}." if deployments else ""
        raise Refused(f"Say which deployment: {command} --deployment <name>.{held}")
    found = lookup.find(deployments, remotes=remotes, command=command)
    if found.is_new:
        raise Refused(
            f"{remote.repo} has no deployment in your fleet. Define one: gapp add --project <project>"
        )
    return found.name


def _result(name: str, changes: list[dict], where: fleetstore.Location, **more) -> dict:
    return {"deployment": name, "changed": bool(changes), "changes": changes, "fleet": where.describe(), **more}


def _change(key: str, before, after) -> dict:
    return {"key": key, "before": settings_mod.describe(key, before), "after": settings_mod.describe(key, after)}


def _settings(definition: dict) -> dict:
    return dict(definition.get("settings") or {})


# --- definitions ---


@_guard
def add(name: str | None = None, *, project: str | None = None, repo: str | None = None,
        ref: str | None = None, copy_of: str | None = None,
        cwd: Path | None = None) -> dict:
    """Define a deployment. Touches no cloud."""
    where, deployments = _load()
    source = None
    if copy_of:
        if not name:
            raise Refused("A copy needs its own name: gapp add <name> --copy-of " + copy_of)
        source = deployments[lookup.find(deployments, name=copy_of).name]
        from gapp.admin.sdk import versions
        versions.check_definition(source, copy_of, "copy it")
        if repo:
            raise Refused("A copy is another deployment of the same repo, so it takes no --repo.")
        repo, project, ref = source.get("repo"), project or source.get("project"), ref or source.get("ref")
    else:
        remote = lookup.pick_remote(lookup.clone_remotes(cwd or Path.cwd()))
        if remote is not None:
            repo = repo or remote.repo
            name = name or remote.name
        if not name:
            raise Refused("Give the deployment a name: gapp add <name> --project <project>")
    if not project:
        raise Refused(f"Say which project it runs in: gapp add {name} --project <project>")
    name, project = fleet.validate_name(name), fleet.validate_project(project)
    if ref:
        ref = fleet.validate_ref(ref)
    if repo:
        repo = _repo(repo)
    if name in deployments:
        held = deployments[name]
        raise Refused(
            f"'{name}' is already a deployment ({held.get('repo') or 'no repo'}, on {held.get('project')}). "
            f"Pick another name, or change that one with the settings commands."
        )
    id_ = fleet.allocate_id(deployments, name, project)
    definition = entryfile.new_definition(id=id_, project=project, repo=repo, ref=ref)
    if source is not None:
        copied = _settings(source)
        copied.pop("domain", None)
        definition["settings"] = copied
    deployments[name] = definition
    _save(where, deployments, f"gapp: add {name}")
    return _result(name, [{"key": "deployment", "before": "not defined", "after": "defined"}], where,
                   definition=definition, copied_from=copy_of)


def _repo(repo: str) -> str:
    """A repo as a definition records it: `owner/repo` on GitHub, `host/path` elsewhere."""
    repo = (repo or "").strip()
    remote = lookup.parse_remote(repo) or lookup.parse_remote(f"https://{repo}")
    if remote is not None and "." in remote.host:
        return remote.repo
    return fleet.validate_repo(repo)


@_guard
def remove(name: str) -> dict:
    """Take a deployment out of the fleet. Touches no cloud.

    What it had deployed stays as it is. The result names it, and the
    commands that define a deployment on those resources again; they do
    not bring its settings back. `see_removed` is the command that shows
    what was removed, settings included.
    """
    from gapp.admin.sdk import record
    where, deployments = _load()
    name = lookup.find(deployments, name=name).name
    definition = deployments.pop(name)
    backup = fleetstore.write(deployments, where, f"gapp: remove {name}")
    if where.kind == "local":
        see = f"cat {backup}" if backup else None
    else:
        from gapp.admin.sdk import fleetrepo
        see = fleetrepo.show_last_change(where.repo)
    id_, project = definition.get("id"), definition.get("project")
    env = (definition.get("settings") or {}).get("env") or {}
    secrets = sorted({f"{id_}-{spec['secret']}" for spec in env.values()
                      if isinstance(spec, dict) and spec.get("secret")})
    return _result(
        name, [{"key": "deployment", "before": "defined", "after": "removed"}], where,
        removed=definition,
        left={"project": project, "service": id_, "bucket": f"gapp-{id_}-{project}".lower(), "secrets": secrets,
              "state": f"gs://{record.bucket(project)}/{record.folder(id_)}/"},
        add_again=add_again(definition, name),
        see_removed=see,
    )


def add_again(definition: dict, name: str) -> list[str]:
    """The commands that define a deployment on `definition`'s resources
    again: `gapp add` under its id (gapp picks the id from the name, and a
    free name is its own id), then `gapp rename` back when its name was
    different. Settings are not part of it."""
    id_ = definition.get("id")
    again = f"gapp add {id_} --project {definition.get('project')}"
    if definition.get("repo"):
        again += f" --repo {definition['repo']}"
    if definition.get("ref"):
        again += f" --ref {definition['ref']}"
    return [again] + ([f"gapp rename {id_} {name}"] if name and name != id_ else [])


@_guard
def rename(old: str, new: str) -> dict:
    """Change a deployment's name. Its id, and so its cloud resources, stay."""
    where, deployments = _load()
    old = lookup.find(deployments, name=old).name
    new = fleet.validate_name(new)
    if new == old:
        raise Refused(f"'{old}' is already its name.")
    if new in deployments:
        raise Refused(f"'{new}' is already a deployment. Pick another name.")
    renamed = {(new if n == old else n): d for n, d in deployments.items()}
    _save(where, renamed, f"gapp: rename {old} to {new}")
    return _result(new, [{"key": "name", "before": old, "after": new}], where)


@_guard
def show(deployment: str | None = None, *, cwd: Path | None = None) -> dict:
    """One definition, and the settings a deploy would use with where each comes from.

    `settings` is the definition's settings object, exactly what `apply`
    takes, so a file can be started from it. `effective` lists every
    setting, each as set on the definition or as its default. The app's
    own `app.json` is not read here: a deploy reads it, from the commit it
    builds, and `gapp deploy --dry-run` shows what it adds.
    """
    from gapp.admin.sdk import loading, versions
    loaded = loading.load_definition(deployment, cwd, "gapp show", "read it", read_only=True)
    where, name, definition = loaded.where, loaded.name, loaded.definition
    stands = versions.standing(definition["gapp"])
    own = _settings(definition)
    rows = []
    for key in settings_mod.SCALARS:
        if key in own:
            rows.append({"key": key, "value": settings_mod.describe(key, own[key]), "from": "definition"})
        elif key in settings_mod.DEFAULTS:
            rows.append({"key": key, "value": settings_mod.describe(key, settings_mod.DEFAULTS[key]), "from": "default"})
    if own.get("apis"):
        rows.append({"key": "apis", "value": settings_mod.describe("apis", own["apis"]), "from": "definition"})
    for variable, entry in (own.get("env") or {}).items():
        rows.append({"key": f"env {variable}", "value": settings_mod.describe(variable, entry), "from": "definition"})
    return {
        "deployment": name, "id": definition.get("id"), "repo": definition.get("repo"),
        "project": definition.get("project"), "ref": definition.get("ref"), "gapp": definition.get("gapp"),
        "this_gapp": __version__, "upgrade": versions.UPGRADE_WORDS.get(stands),
        "settings": settings_mod.validate(own), "effective": rows, "fleet": where.describe(),
    }


@_guard
def last_deployed_by(project: str, deployment_id: str) -> str | None:
    """The gapp version of the deployment's last successful deploy, from the
    deploy record in its project, or None when there is no record. Raises
    when the record cannot be read."""
    from gapp.admin.sdk import record
    from gapp.admin.sdk.cloud import get_provider
    return (record.read_latest(get_provider(), project, deployment_id) or {}).get("gapp")


def _next_deploy_needs_upgrade(definition: dict) -> bool | None:
    """Whether the next deploy of an upgraded definition must be given
    --upgrade: True when an earlier gapp deployed it, False when nothing
    is on record, None when the record could not be read here (no gcloud,
    no login, no access). Never raises: the definition is already
    upgraded, and this only words what to do next."""
    try:
        return last_deployed_by(definition["project"], definition["id"]) is not None
    except Exception:
        return None


def upgrade(deployment: str | None = None, *, cwd: Path | None = None) -> dict:
    """Bring one definition to this gapp's version. Changes the definition
    only; then reads the deploy record, if it can, to say whether the next
    deploy needs --upgrade (`deploy_needs_upgrade`: true, false, or null
    when it could not be read)."""
    from gapp import UPGRADES_FROM
    from gapp.admin.sdk import versions
    where, deployments = _load()
    name = which(deployments, deployment, cwd, "gapp upgrade")
    definition = deployments[name]
    recorded = definition.get("gapp")
    if recorded is None:
        versions.check_readable(definition, name, "upgrade it")
    stands = versions.standing(recorded)
    if stands == "current":
        raise Refused(f"'{name}' is already on gapp {__version__}. There is nothing to upgrade.")
    if stands == "newer":
        raise Refused(
            f"'{name}' was written by gapp {recorded}; this is gapp {__version__}. "
            f"A definition never goes back. Install gapp {recorded} or later."
        )
    if stands == "too-old":
        raise Refused(
            f"'{name}' was written by gapp {recorded}, which gapp {__version__} cannot upgrade from "
            f"(the oldest it can is {UPGRADES_FROM}). Install gapp {UPGRADES_FROM}, run `gapp upgrade "
            f"--deployment {name}`, then come back to this one."
        )
    definition["gapp"] = __version__
    _save(where, deployments, f"gapp: upgrade {name} to gapp {__version__}")
    return _result(name, [{"key": "gapp", "before": recorded, "after": __version__}], where,
                   plan={"definition": [], "next_deploy": [], "by_hand": []},
                   deploy_needs_upgrade=_next_deploy_needs_upgrade(definition))


# --- settings ---


def _open(deployment: str | None, cwd: Path | None, command: str) -> tuple:
    from gapp.admin.sdk import loading
    loaded = loading.load_definition(deployment, cwd, command, "change it")
    return loaded.where, loaded.deployments, loaded.name, loaded.definition


def _close(where, deployments, name, definition, new_settings: dict, changes: list[dict]) -> dict:
    from gapp.admin.sdk import versions
    for change in changes:
        versions.check_setting(definition, change["key"].split()[0], name)
    if changes:
        definition["settings"] = settings_mod.validate(new_settings)
        _save(where, deployments, "gapp: change " + ", ".join(c["key"] for c in changes) + f" on {name}")
    return _result(name, changes, where)


@_guard
def set_value(key: str, value: str, *, deployment: str | None = None, cwd: Path | None = None) -> dict:
    """Set one value of a definition: its ref or repo, or one setting."""
    key = (key or "").strip()
    where, deployments, name, definition = _open(deployment, cwd, f"gapp set {key} <value>")
    if key in OWN_SETTABLE:
        new = fleet.validate_ref(value) if key == "ref" else _repo(value)
        before = definition.get(key)
        if before == new:
            return _result(name, [], where)
        definition[key] = new
        _save(where, deployments, f"gapp: set {key} on {name}")
        return _result(name, [{"key": key, "before": before or "not set", "after": new}], where)
    if key in ("id", "project", "gapp"):
        raise Refused(_fixed(key, name))
    new = settings_mod.parse_scalar(key, value)
    current = _settings(definition)
    if current.get(key) == new:
        return _result(name, [], where)
    change = _change(key, current.get(key, settings_mod.DEFAULTS.get(key)), new)
    current[key] = new
    return _close(where, deployments, name, definition, current, [change])


def _fixed(key: str, name: str) -> str:
    if key == "gapp":
        return f"The gapp version is not typed. It moves with: gapp upgrade --deployment {name}"
    return f"A deployment's {key} is fixed when it is defined; changing it would make a different deployment."


@_guard
def unset_value(key: str, *, deployment: str | None = None, cwd: Path | None = None) -> dict:
    """Clear one value: the ref, or one setting. The default applies again."""
    key = (key or "").strip()
    where, deployments, name, definition = _open(deployment, cwd, f"gapp unset {key}")
    if key == "ref":
        if "ref" not in definition:
            return _result(name, [], where)
        before = definition.pop("ref")
        _save(where, deployments, f"gapp: unset ref on {name}")
        return _result(name, [{"key": "ref", "before": before, "after": "the repo's default branch"}], where)
    if key in ("id", "project", "gapp", "repo"):
        raise Refused(f"A deployment's {key} cannot be cleared.")
    if key not in settings_mod.SCALARS:
        raise Refused(settings_mod._unknown(key) + " A variable is removed with `gapp env unset`, an API with `gapp apis remove`.")
    current = _settings(definition)
    if key not in current:
        return _result(name, [], where)
    change = _change(key, current.pop(key), settings_mod.DEFAULTS.get(key))
    return _close(where, deployments, name, definition, current, [change])


@_guard
def apis_add(api: str, *, deployment: str | None = None, cwd: Path | None = None) -> dict:
    api = settings_mod.api(api)
    where, deployments, name, definition = _open(deployment, cwd, f"gapp apis add {api}")
    current = _settings(definition)
    apis = list(current.get("apis") or [])
    if api in apis:
        return _result(name, [], where)
    current["apis"] = apis + [api]
    return _close(where, deployments, name, definition, current, [_change("apis", apis, sorted(current["apis"]))])


@_guard
def apis_remove(api: str, *, deployment: str | None = None, cwd: Path | None = None) -> dict:
    api = settings_mod.api(api)
    where, deployments, name, definition = _open(deployment, cwd, f"gapp apis remove {api}")
    current = _settings(definition)
    apis = list(current.get("apis") or [])
    if api not in apis:
        return _result(name, [], where)
    current["apis"] = [a for a in apis if a != api]
    return _close(where, deployments, name, definition, current, [_change("apis", apis, current["apis"])])


@_guard
def env_set(variable: str, value: str | None = None, *, secret: str | None = None, generate: bool = False,
            data_path: str | None = None, deployment: str | None = None, cwd: Path | None = None) -> dict:
    """Add or replace one variable: a plain value, a secret, or the data folder."""
    variable = settings_mod.env_name(variable)
    entry = settings_mod.env_entry(variable, value=value, secret=secret, generate=generate, data_path=data_path)
    where, deployments, name, definition = _open(deployment, cwd, f"gapp env set {variable} <value>")
    current = _settings(definition)
    env = dict(current.get("env") or {})
    if env.get(variable) == entry:
        return _result(name, [], where)
    change = _change(f"env {variable}", env.get(variable), entry)
    env[variable] = entry
    current["env"] = env
    result = _close(where, deployments, name, definition, current, [change])
    if "secret" in entry and not entry.get("generate"):
        result["next"] = f"gapp secrets set {entry['secret']} --deployment {name}"
    return result


@_guard
def env_unset(variable: str, *, deployment: str | None = None, cwd: Path | None = None) -> dict:
    """Remove one variable from the definition. A secret it read is left in place."""
    variable = settings_mod.env_name(variable)
    where, deployments, name, definition = _open(deployment, cwd, f"gapp env unset {variable}")
    current = _settings(definition)
    env = dict(current.get("env") or {})
    if variable not in env:
        return _result(name, [], where)
    change = _change(f"env {variable}", env.pop(variable), None)
    current["env"] = env
    return _close(where, deployments, name, definition, current, [change])


@_guard
def apply(text: str, *, deployment: str | None = None, cwd: Path | None = None, source: str = "The file",
          dry_run: bool = False) -> dict:
    """Replace a definition's settings with the ones in `text`, all at once or not at all.

    With `dry_run`, the same checks run and the same changes are
    reported, and nothing is written.
    """
    try:
        wanted = yaml.safe_load(text) if text and text.strip() else {}
    except yaml.YAMLError as e:
        raise Refused(f"{source} is not valid YAML: {e}") from e
    wanted = settings_mod.validate(wanted)
    where, deployments, name, definition = _open(deployment, cwd, "gapp apply <file>")
    current = settings_mod.validate(_settings(definition))
    changes = []
    for key in settings_mod.SCALARS + ("apis",):
        if current.get(key) != wanted.get(key):
            default = settings_mod.DEFAULTS.get(key)
            changes.append(_change(key, current.get(key, default), wanted.get(key, default)))
    before_env, after_env = current.get("env") or {}, wanted.get("env") or {}
    for variable in sorted(set(before_env) | set(after_env)):
        if before_env.get(variable) != after_env.get(variable):
            changes.append(_change(f"env {variable}", before_env.get(variable), after_env.get(variable)))
    if dry_run:
        return _result(name, changes, where, dry_run=True)
    return _close(where, deployments, name, definition, wanted, changes)
