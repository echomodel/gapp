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
    _origin_repo_name, get_env_vars, get_service_config, get_solution_name,
    load_manifest, save_manifest,
)
from gapp.admin.sdk.schema import (
    EnvEntry, Manifest, ManifestValidationError, Prerequisites, SecretSpec, ServiceSpec,
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


def describe(repo_path: Path | None = None) -> dict:
    """This repo's gapp.yaml settings, each with what it means, unset ones included.

    Reads the file leniently, so a file that fails the schema is still
    shown, with its problems under `issues`.
    """
    root = _root(repo_path)
    path = root / "gapp.yaml"
    result = {"repo": str(root), "exists": path.exists(), "issues": [], "settings": [], "env": [],
              "start": start_method(root)}
    manifest = load_manifest(root, strict=False) if path.exists() else {}
    try:
        validate_manifest(manifest)
    except ManifestValidationError as e:
        result["issues"] = e.issues

    source = name_source(manifest, root)
    name = get_solution_name(manifest, root)
    result["name"] = name
    result["name_source"] = source

    rows = result["settings"]
    rows.append({"key": "name", "value": name, "set": source == "gapp.yaml",
                 "meaning": NAME_SOURCE_TEXT[source]})
    public = manifest.get("public")
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

    result["env"] = [_env_row(e) for e in get_env_vars(manifest)]
    return result


# -- writing --

def _load_for_write(root: Path) -> dict:
    if not (root / "gapp.yaml").exists():
        raise ManifestChangeRefused("No gapp.yaml in this repo. Run `gapp init` first.")
    return load_manifest(root, strict=False)


def _write(root: Path, before: dict, after: dict, *, new_deployment: bool, change: str) -> dict:
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


def set_value(key: str, value: str, *, new_deployment: bool = False, repo_path: Path | None = None) -> dict:
    """Set one setting (a key of SETTINGS) in gapp.yaml."""
    if key not in SETTINGS:
        raise ManifestChangeRefused(f"Unknown setting '{key}'. Settings: {', '.join(SETTINGS)}. "
                                    "Environment variables: gapp manifest env set.")
    root = _root(repo_path)
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


def unset_value(key: str, *, new_deployment: bool = False, repo_path: Path | None = None) -> dict:
    """Remove one setting (a key of SETTINGS) from gapp.yaml."""
    root = _root(repo_path)
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
            data_path: bool = False, repo_path: Path | None = None) -> dict:
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
    root = _root(repo_path)
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


def env_unset(name: str, *, repo_path: Path | None = None) -> dict:
    """Remove one environment variable. The secret it named, if any, is left
    in Secret Manager."""
    root = _root(repo_path)
    before = _load_for_write(root)
    after = copy.deepcopy(before)
    env = after.get("env") or []
    kept = [e for e in env if e.get("name") != name]
    if len(kept) == len(env):
        raise ManifestChangeRefused(f"gapp.yaml has no variable '{name}'.")
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
        "# How the app starts is not here: it is a Procfile web: line or a Dockerfile.",
        "",
        f"# {_desc(Manifest, 'name')}",
        "name: my-app",
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
