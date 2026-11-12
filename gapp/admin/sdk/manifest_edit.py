"""Read and change one app's gapp.yaml from the CLI and MCP.

`describe` reports the repo's own settings, each with what it means,
including the ones left unset. The writers change one setting at a time,
validate the result against the schema, and write the file only when its
content changes (through `save_manifest`, gapp's one writer).

The name is guarded: it names the Cloud Run service, the data bucket and
the secrets, so a different name is a different deployment. A change that
alters the deployment's name is refused unless the caller says it wants a
new deployment.
"""

from __future__ import annotations

import copy
from pathlib import Path

from gapp.admin.sdk.fleet import NAME_RE
from gapp.admin.sdk.manifest import (
    _origin_repo_name, dump_manifest, get_env_vars, get_service_config, get_solution_name,
    load_manifest, parse_manifest_text, save_manifest,
)
from gapp.admin.sdk.schema import (
    EnvEntry, Manifest, ManifestUnreadable, ManifestValidationError, Prerequisites, SecretSpec, ServiceSpec,
    validate_manifest,
)
from gapp.admin.sdk.start import describe as describe_start, start_method
from gapp.admin.sdk.util import get_git_root


DATA_PATH = "{{SOLUTION_DATA_PATH}}"

# Settings `gapp manifest set/unset` takes: CLI key -> path in gapp.yaml.
SETTINGS = {
    "name": ("name",),
    "public": ("public",),
    "domain": ("domain",),
    "start": ("start",),
    "memory": ("service", "memory"),
    "cpu": ("service", "cpu"),
    "max-instances": ("service", "max_instances"),
}


class ManifestChangeRefused(ValueError):
    """A change gapp will not make, with the reason and what to do instead."""


# -- reading --

def _root(repo_path: Path | None) -> Path:
    root = get_git_root(repo_path) if repo_path else get_git_root()
    if not root:
        raise RuntimeError("Not inside a git repository. Run this in the app's repo.")
    return root


def name_source(manifest: dict, root: Path) -> str:
    """Where the deployment's name comes from: 'gapp.yaml', 'repo' or 'folder'."""
    if manifest.get("name"):
        return "gapp.yaml"
    return "repo" if _origin_repo_name(root) else "folder"


NAME_SOURCE_TEXT = {
    "gapp.yaml": "set in gapp.yaml",
    "repo": "the origin repo's name; not written to gapp.yaml",
    "folder": "the folder's name (no origin remote); not written to gapp.yaml",
}


def _env_row(entry: dict) -> dict:
    name = entry.get("name")
    secret = entry.get("secret")
    if isinstance(secret, dict):
        meaning = (f"secret '{secret.get('name')}', generated on the first deploy if not set"
                   if secret.get("generate") else
                   f"secret '{secret.get('name')}'; set its value with `gapp secrets set {secret.get('name')}`")
        return {"name": name, "kind": "secret", "value": secret.get("name"), "meaning": meaning}
    value = entry.get("value")
    if isinstance(value, str) and value.startswith(DATA_PATH):
        sub = value[len(DATA_PATH):].lstrip("/")
        return {"name": name, "kind": "data_path", "value": value,
                "meaning": f"the data volume{', ' + sub if sub else ''} (kept across deploys)"}
    return {"name": name, "kind": "value", "value": value, "meaning": "plain value"}


def _file_problem(file: Path, e: Exception) -> ManifestChangeRefused:
    """A --file target's parse or schema problem, worded for that file (not the repo's gapp.yaml)."""
    if isinstance(e, ManifestUnreadable):
        where = f"line {e.line}, column {e.column}: " if e.line else ""
        return ManifestChangeRefused(f"{file} can't be read as YAML: {where}{e.problem}. Fix it in the file.")
    issues = "; ".join(f"{i['path']}: {i['message']}" for i in e.issues)
    return ManifestChangeRefused(f"{file} has settings gapp doesn't accept: {issues}. Fix it in the file.")


def _read_file(file: Path) -> dict:
    if not file.exists():
        return {}
    try:
        return parse_manifest_text(file.read_text())
    except ManifestUnreadable as e:
        raise _file_problem(file, e) from None


def describe(repo_path: Path | None = None, *, file: Path | str | None = None) -> dict:
    """This repo's gapp.yaml settings, each with what it means, unset ones included.

    Reads the file leniently, so a file that fails the schema is still
    shown, with its problems under `issues`. With `file`, describes that
    gapp.yaml-shaped file instead (a deployment's fleet-side manifest): no
    name and no start method, which belong to a repo.
    """
    if file is not None:
        file = Path(file)
        result = {"file": str(file), "repo": None, "exists": file.exists(), "issues": [], "settings": [],
                  "env": [], "start": None, "name": None, "name_source": None}
        manifest = _read_file(file)
    else:
        root = _root(repo_path)
        path = root / "gapp.yaml"
        result = {"file": None, "repo": str(root), "exists": path.exists(), "issues": [], "settings": [],
                  "env": [], "start": start_method(root)}
        manifest = load_manifest(root, strict=False) if path.exists() else {}
    try:
        validate_manifest(manifest)
    except ManifestValidationError as e:
        result["issues"] = e.issues

    rows = result["settings"]
    if file is None:
        source = name_source(manifest, root)
        name = get_solution_name(manifest, root)
        result["name"] = name
        result["name_source"] = source
        rows.append({"key": "name", "value": name, "set": source == "gapp.yaml",
                     "meaning": NAME_SOURCE_TEXT[source]})
    public = manifest.get("public")
    start_value = manifest.get("start")
    if start_value:
        st = result["start"]
        if st is None:
            meaning = "starts the app when its repo has no Dockerfile or Procfile"
        elif st["method"] == "start":
            meaning = "starts the app"
        else:
            meaning = "ignored: the " + ("Dockerfile" if st["method"] == "dockerfile" else "Procfile") + " starts the app"
        rows.append({"key": "start", "value": start_value, "set": True, "meaning": meaning})
    rows.append({"key": "public", "value": public, "set": public is not None,
                 "meaning": "anyone can call the URL; the app does its own auth" if public else
                 "private: only callers with Cloud Run invoker access"})
    domain = manifest.get("domain")
    rows.append({"key": "domain", "value": domain, "set": bool(domain),
                 "meaning": "mapped at deploy; CNAME it to ghs.googlehosted.com" if domain else
                 "the .run.app URL only"})
    service = manifest.get("service") or {}
    defaults = get_service_config({})
    for key, field in (("memory", "memory"), ("cpu", "cpu"), ("max-instances", "max_instances")):
        here = service.get(field)
        rows.append({"key": key, "value": here if here is not None else defaults[field],
                     "set": here is not None, "meaning": "set in gapp.yaml" if here is not None else "default"})
    if manifest.get("paths"):
        rows.append({"key": "paths", "value": manifest["paths"], "set": True,
                     "meaning": "each subfolder is its own service"})
    apis = (manifest.get("prerequisites") or {}).get("apis")
    if apis:
        rows.append({"key": "apis", "value": apis, "set": True, "meaning": "enabled by gapp setup"})

    if file is not None:
        # In a fleet-side manifest, a key it doesn't set is decided by the app repo's gapp.yaml.
        for row in rows:
            if not row["set"]:
                row["value"] = None
                row["meaning"] = "not set here: the app repo's gapp.yaml decides"
    result["env"] = [_env_row(e) for e in get_env_vars(manifest)]
    return result


# -- writing --

def _target(repo_path: Path | None, file: Path | str | None):
    """What a write changes: ("file", path) for --file, else ("repo", git root)."""
    return ("file", Path(file)) if file is not None else ("repo", _root(repo_path))


def _load_for_write(target) -> dict:
    kind, where = target
    if kind == "file":
        return _read_file(where)
    if not (where / "gapp.yaml").exists():
        raise ManifestChangeRefused("No gapp.yaml in this repo. Run `gapp init` first.")
    return load_manifest(where, strict=False)


def _write(target, before: dict, after: dict, *, new_deployment: bool, change: str) -> dict:
    kind, where = target
    if kind == "file":
        try:
            validate_manifest(after)
        except ManifestValidationError as e:
            raise _file_problem(where, e) from None
        changed = after != before or not where.exists()
        if changed:
            where.write_text(dump_manifest(after))
        result = describe(file=where)
        result.update({"changed": changed, "change": change if changed else "no change"})
        return result
    root = where
    old_name, new_name = get_solution_name(before, root), get_solution_name(after, root)
    if old_name != new_name and not new_deployment:
        raise ManifestChangeRefused(
            f"This changes the deployment's name from '{old_name}' to '{new_name}'. The name is the "
            f"deployment's identity (its service, data bucket and secrets), so '{new_name}' would be a "
            f"new, empty deployment and '{old_name}' would keep its URL and data. Nothing was changed. "
            f"To do it anyway, add --new-deployment."
        )
    validate_manifest(after)
    changed = after != before
    if changed:
        save_manifest(root, after)
    result = describe(root)
    result.update({"changed": changed, "change": change if changed else "no change"})
    if changed and old_name != new_name:
        result["new_deployment"] = {"old_name": old_name, "new_name": new_name}
    return result


def _parse(key: str, value: str):
    if key == "public":
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
        raise ManifestChangeRefused("public takes true or false.")
    if key == "max-instances":
        try:
            n = int(value)
        except ValueError:
            n = 0
        if n < 1:
            raise ManifestChangeRefused("max-instances takes a whole number, 1 or more.")
        return n
    value = value.strip()
    if not value:
        raise ManifestChangeRefused(f"{key} needs a value; to remove it, use unset.")
    if key == "name" and not NAME_RE.match(value):
        raise ManifestChangeRefused(
            "A name is lowercase letters, digits and hyphens, starting with a letter or digit, at most 63 characters."
        )
    if key == "domain" and ("." not in value or "/" in value or ":" in value):
        raise ManifestChangeRefused("domain takes a host name, e.g. app.example.com (no scheme or path).")
    return value


def set_value(key: str, value: str, *, new_deployment: bool = False, repo_path: Path | None = None,
              file: Path | str | None = None) -> dict:
    """Set one setting (a key of SETTINGS) in gapp.yaml, or in `file`."""
    if key not in SETTINGS:
        raise ManifestChangeRefused(f"Unknown setting '{key}'. Settings: {', '.join(SETTINGS)}. "
                                    "Environment variables: gapp manifest env set.")
    if key == "name" and file is not None:
        raise ManifestChangeRefused("A fleet-side manifest has no name: the deployment's id names it. "
                                    "Set the name in the app repo's gapp.yaml, without --file.")
    root = _target(repo_path, file)
    before = _load_for_write(root)
    after = copy.deepcopy(before)
    parsed = _parse(key, value)
    path = SETTINGS[key]
    if len(path) == 1:
        after[path[0]] = parsed
    else:
        after.setdefault(path[0], {})
        after[path[0]][path[1]] = parsed
    return _write(root, before, after, new_deployment=new_deployment, change=f"{key} = {str(parsed).lower() if isinstance(parsed, bool) else parsed}")


def unset_value(key: str, *, new_deployment: bool = False, repo_path: Path | None = None,
                file: Path | str | None = None) -> dict:
    """Remove one setting (a key of SETTINGS) from gapp.yaml, or from `file`."""
    root = _target(repo_path, file)
    before = _load_for_write(root)
    after = copy.deepcopy(before)
    if key not in SETTINGS:
        raise ManifestChangeRefused(f"Unknown setting '{key}'. Settings: {', '.join(SETTINGS)}.")
    path = SETTINGS[key]
    if len(path) == 1:
        after.pop(path[0], None)
    else:
        section = after.get(path[0])
        if isinstance(section, dict):
            section.pop(path[1], None)
            if not section:
                after.pop(path[0])
    return _write(root, before, after, new_deployment=new_deployment, change=f"{key} removed")


def env_set(name: str, value: str | None = None, *, secret: str | None = None, generate: bool = False,
            data_path: bool = False, repo_path: Path | None = None, file: Path | str | None = None) -> dict:
    """Add or replace one environment variable.

    Exactly one kind: a plain `value`; `secret` (a short secret name, with
    `generate` to have gapp create its value); or `data_path`, where `value`
    is an optional subfolder of the data volume.
    """
    name = (name or "").strip()
    if not name:
        raise ManifestChangeRefused("The variable needs a name.")
    if secret is not None and data_path:
        raise ManifestChangeRefused("Pick one: a value, --secret, or --data-path.")
    if secret is not None and value is not None:
        raise ManifestChangeRefused("A secret variable takes no value; gapp reads it from the secret.")
    if generate and secret is None:
        raise ManifestChangeRefused("--generate goes with --secret.")
    if secret is not None:
        secret = secret.strip()
        if not NAME_RE.match(secret):
            raise ManifestChangeRefused("A secret name is lowercase letters, digits and hyphens.")
        entry = {"name": name, "secret": {"name": secret, **({"generate": True} if generate else {})}}
        what = f"secret '{secret}'" + (", generated" if generate else "")
    elif data_path:
        sub = (value or "").strip().strip("/")
        entry = {"name": name, "value": f"{DATA_PATH}/{sub}" if sub else DATA_PATH}
        what = "the data volume" + (f", {sub}" if sub else "")
    else:
        if value is None:
            raise ManifestChangeRefused("Give a value, --secret NAME, or --data-path [SUBFOLDER].")
        entry = {"name": name, "value": value}
        what = value
    root = _target(repo_path, file)
    before = _load_for_write(root)
    after = copy.deepcopy(before)
    env = after.setdefault("env", [])
    for i, existing in enumerate(env):
        if existing.get("name") == name:
            env[i] = entry
            break
    else:
        env.append(entry)
    return _write(root, before, after, new_deployment=False, change=f"{name} = {what}")


def env_unset(name: str, *, repo_path: Path | None = None, file: Path | str | None = None) -> dict:
    """Remove one environment variable. The secret it named, if any, is left
    in Secret Manager."""
    root = _target(repo_path, file)
    before = _load_for_write(root)
    after = copy.deepcopy(before)
    env = after.get("env") or []
    kept = [e for e in env if e.get("name") != name]
    if len(kept) == len(env):
        raise ManifestChangeRefused(f"{root[1] if root[0] == 'file' else 'gapp.yaml'} has no variable '{name}'.")
    if kept:
        after["env"] = kept
    else:
        after.pop("env")
    return _write(root, before, after, new_deployment=False, change=f"{name} removed")


# -- the annotated example --

def _desc(model, field: str) -> str:
    return model.model_fields[field].description or ""


def example() -> str:
    """An annotated gapp.yaml with every key, each commented with its schema
    description, so the example cannot drift from what gapp accepts."""
    lines = [
        "# gapp.yaml: every key is optional.",
        "# An app starts from its Dockerfile, else its Procfile's web: line, else start below.",
        "",
        f"# {_desc(Manifest, 'name')}",
        "name: my-app",
        "",
        f"# {_desc(Manifest, 'start')}",
        "# start: my-app serve",
        "",
        f"# {_desc(Manifest, 'public')}",
        "public: true",
        "",
        f"# {_desc(Manifest, 'domain')}",
        "domain: app.example.com",
        "",
        f"# {_desc(Manifest, 'env')}",
        "env:",
        f"  # {_desc(EnvEntry, 'name')}",
        "  - name: LOG_LEVEL",
        f"    # {_desc(EnvEntry, 'value')}",
        "    value: INFO",
        "  - name: APP_DATA",
        f'    value: "{DATA_PATH}/data"',
        "  - name: API_KEY",
        f"    # {_desc(EnvEntry, 'secret')}",
        "    secret:",
        f"      # {_desc(SecretSpec, 'name')}",
        "      name: api-key",
        "  - name: SIGNING_KEY",
        "    secret:",
        "      name: signing-key",
        f"      # {_desc(SecretSpec, 'generate')}",
        "      generate: true",
        "",
        f"# {_desc(Manifest, 'service')}",
        "service:",
        f"  # {_desc(ServiceSpec, 'memory')}",
        "  memory: 1Gi",
        f"  # {_desc(ServiceSpec, 'cpu')}",
        '  cpu: "1"',
        f"  # {_desc(ServiceSpec, 'max_instances')}",
        "  max_instances: 2",
        "",
        f"# {_desc(Manifest, 'prerequisites')}",
        "prerequisites:",
        f"  # {_desc(Prerequisites, 'apis')}",
        "  apis:",
        "    - firestore.googleapis.com",
        "",
        f"# {_desc(Manifest, 'paths')} Each subfolder has its own gapp.yaml.",
        "# paths:",
        "#   - services/api",
        "#   - services/worker",
    ]
    return "\n".join(lines) + "\n"
