"""The fleet file: `gapp/deployments.yaml` in the fleet repo.

One entry per deployment, keyed by the operator-chosen name. An entry's
own fields are `id`, `repo` and `project`; every other key is a gapp.yaml
key, deep-merged over the deployed repo's gapp.yaml by the deploy run
(or written as the whole file when the repo has none):

    my-app:
      id: my-app
      repo: me/my-app
      project: my-project
      domain: mcp.example.com
      env:
        - name: LOG_LEVEL
          value: INFO

`id` is what every GCP resource is keyed by (the service and its account,
the bucket, secrets, Terraform state, the label); it is passed to the
runner as the solution name, so the entry governs whatever the repo's
gapp.yaml says. It is set once, defaults to the deployment name, and is
never copied: two entries on one project never share an id.

Pure functions over the parsed document; no I/O. The runner and the
operator's machine run the same code, so what `gapp ci setup` refuses
before dispatching is what the register run would have refused.
"""

from __future__ import annotations

import copy
import re

import yaml

DEPLOYMENTS_FILE = "gapp/deployments.yaml"
ENTRY_FIELDS = ("id", "repo", "project")
# The service account is `<id>-sa`, and a service account id is at most
# 30 characters.
ID_MAX = 27

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")

DEPLOYMENTS_FILE_INITIAL = """\
# Deployments of this fleet. gapp owns this file: `gapp ci setup` writes
# an entry through the register workflow and `gapp ci deploy` reads it.
# One entry per deployment, keyed by its name: id (what the GCP resources
# are keyed by), repo (owner/name), project (the GCP project id; the GitHub
# Environment of the same name holds the deploy identity), and any
# gapp.yaml keys to supply or override for this deployment.
{}
"""


class FleetRefusal(Exception):
    """A change the fleet file refuses, with a code and a plain sentence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --- the document ---


def parse(text: str | None) -> dict:
    """The deployments document: name -> entry. An empty file is an empty fleet."""
    doc = yaml.safe_load(text or "") or {}
    if not isinstance(doc, dict):
        raise FleetRefusal("bad-file", f"{DEPLOYMENTS_FILE} is not a mapping of deployment names to entries.")
    out = {}
    for name, entry in doc.items():
        if not isinstance(entry, dict):
            raise FleetRefusal("bad-file", f"The entry '{name}' in {DEPLOYMENTS_FILE} is not a mapping.")
        out[str(name)] = dict(entry)
    return out


def dump(doc: dict) -> str:
    """The file text: the header comment, then the entries sorted by name, own fields first."""
    header = DEPLOYMENTS_FILE_INITIAL.rsplit("{}", 1)[0]
    if not doc:
        return header + "{}\n"
    ordered = {}
    for name in sorted(doc):
        entry = doc[name]
        ordered[name] = {**{k: entry[k] for k in ENTRY_FIELDS if k in entry},
                         **{k: v for k, v in entry.items() if k not in ENTRY_FIELDS}}
    return header + yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False)


def manifest_keys(entry: dict) -> dict:
    """The gapp.yaml keys an entry carries: everything but its own fields."""
    return {k: copy.deepcopy(v) for k, v in entry.items() if k not in ENTRY_FIELDS}


def overlay_for(entry: dict) -> dict:
    """What the deploy run merges over the repo's gapp.yaml: the entry's
    gapp.yaml keys, plus `name` set to the id so gapp keys every resource by it."""
    out = manifest_keys(entry)
    out["name"] = entry["id"]
    return out


def deployments_of_repo(doc: dict, repo: str) -> list[str]:
    return sorted(name for name, entry in doc.items() if (entry.get("repo") or "").lower() == repo.lower())


def secrets_declared(manifest: dict) -> list[str]:
    """Secret names a manifest (or an entry's gapp.yaml keys) declares."""
    names = []
    for item in manifest.get("env") or []:
        if isinstance(item, dict) and isinstance(item.get("secret"), dict) and item["secret"].get("name"):
            names.append(item["secret"]["name"])
    for name in (manifest.get("prerequisites") or {}).get("secrets") or {}:
        names.append(name)
    return names


# --- validation ---


def validate_name(name: str) -> str:
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise FleetRefusal("invalid-input", (
            f"'{name}' is not a valid deployment name: lowercase letters, digits and hyphens, "
            f"starting with a letter or digit, at most 63 characters."
        ))
    return name


def validate_repo(repo: str) -> str:
    repo = (repo or "").strip()
    if not REPO_RE.match(repo):
        raise FleetRefusal("invalid-input", f"'{repo}' is not a repo of the form owner/name.")
    return repo


def validate_project(project: str) -> str:
    project = (project or "").strip()
    if not PROJECT_RE.match(project):
        raise FleetRefusal("invalid-input", f"'{project}' is not a GCP project id.")
    return project


def unknown_keys(keys: dict | None) -> list[str]:
    """The gapp.yaml fields among `keys` that gapp.yaml does not have, as the
    gapp.yaml validator reports them (its unknown-field issues, by path)."""
    from gapp.admin.sdk.schema import ManifestValidationError, validate_manifest
    present = {k: v for k, v in (keys or {}).items() if v is not None}
    try:
        validate_manifest(present)
    except ManifestValidationError as e:
        return [i["path"] for i in e.issues if i["type"] == "extra_forbidden"]
    return []


def validate_changes(changes: dict | None) -> dict:
    """gapp.yaml keys to set on an entry (None removes a key). The entry's own fields are not changeable here."""
    changes = dict(changes or {})
    for key in ENTRY_FIELDS:
        if key in changes:
            raise FleetRefusal("invalid-input", (
                f"'{key}' is the entry's own field, not a gapp.yaml key; it is set by its own option."
            ))
    if "name" in changes:
        raise FleetRefusal("invalid-input", (
            "'name' is not a key a deployment can set: gapp keys the deployment's resources by its id."
        ))
    for key in unknown_keys(changes):
        raise FleetRefusal("invalid-input", (
            f"'{key}' is not a gapp.yaml key (check for typos); `gapp manifest schema` lists them."
        ))
    return changes


# --- the id ---


def allocate_id(doc: dict, name: str, project: str, exclude: str | None = None) -> str:
    """The id for a new entry: the name, or the next free `<name>-N` on that project."""
    taken = {e.get("id") for n, e in doc.items() if e.get("project") == project and n != exclude}
    candidate = name
    n = 2
    while candidate in taken:
        candidate = f"{name}-{n}"
        n += 1
    if len(candidate) > ID_MAX:
        why = (f"its id would be '{candidate}', {len(candidate)} characters" if candidate != name
               else f"{len(name)} characters")
        raise FleetRefusal("name-too-long", (
            f"'{name}' is too long for a deployment: {why}, and an id is at most {ID_MAX} characters "
            f"(its service account is '<id>-sa'). Pick a shorter name."
        ))
    return candidate


# --- changes ---


def without_unknown_keys(entry: dict) -> dict:
    """The entry with every gapp.yaml key gapp does not know removed (paths as the validator reports them)."""
    out = copy.deepcopy(entry)
    for path in unknown_keys(manifest_keys(entry)):
        *parents, last = path.split(".")
        node = out
        for part in parents:
            node = node[int(part)] if isinstance(node, list) else node.get(part, {})
        if isinstance(node, dict):
            node.pop(last, None)
    return out


def _apply_changes(entry: dict, changes: dict) -> dict:
    out = dict(entry)
    for key, value in changes.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = copy.deepcopy(value)
    return out


def set_entry(doc: dict, name: str, *, repo: str, project: str, changes: dict | None = None) -> tuple[dict, str, dict]:
    """Add or update the deployment `name`. Returns (doc, "added"|"updated"|"unchanged", entry).

    Same name and repo: an update of its gapp.yaml keys, which also drops
    any key gapp.yaml does not have (so re-running setup repairs an entry
    a deploy would refuse). Same name, another
    repo or project: refused (a deployment binds to one repo and one
    project; pick another name, or rename). A repo already deployed under
    other names: refused for a new name, since another deployment of the
    same repo is made with `--copy-of`.
    """
    name = validate_name(name)
    repo = validate_repo(repo)
    project = validate_project(project)
    changes = validate_changes(changes)
    doc = copy.deepcopy(doc)
    existing = doc.get(name)
    if existing is not None:
        if (existing.get("repo") or "").lower() != repo.lower():
            raise FleetRefusal("name-taken", (
                f"'{name}' already deploys {existing.get('repo')}, not {repo}. Pick another name for {repo}, "
                f"or rename the existing deployment first: gapp ci rename {name} <new-name>"
            ))
        if existing.get("project") != project:
            raise FleetRefusal("project-bound", (
                f"'{name}' deploys to project '{existing.get('project')}', and this command names '{project}'. "
                f"A deployment stays on one project, because its id keys that project's resources. For another "
                f"project, add a deployment: gapp ci setup <new-name> --copy-of {name} --project {project}"
            ))
        updated = _apply_changes(without_unknown_keys(existing), changes)
        if updated == existing:
            return doc, "unchanged", existing
        doc[name] = updated
        return doc, "updated", updated
    others = deployments_of_repo(doc, repo)
    if others:
        raise FleetRefusal("repo-deployed", (
            f"{repo} is already deployed as {', '.join(others)}. "
            f"Update it: gapp ci setup {others[0]} --repo {repo} --project {doc[others[0]].get('project')} ... "
            f"Add another: gapp ci setup {name} --copy-of {others[0]} ..."
        ))
    entry = {"id": allocate_id(doc, name, project), "repo": repo, "project": project}
    doc[name] = _apply_changes(entry, changes)
    return doc, "added", doc[name]


def copy_entry(doc: dict, new: str, source: str, *, project: str | None = None,
               changes: dict | None = None) -> tuple[dict, dict, dict]:
    """A new deployment from an existing one: the same repo and gapp.yaml
    keys, never its id, domain, deploy history or secret values. Returns
    (doc, entry, info) where info names the env vars copied and the
    secrets the new deployment needs set on its project.
    """
    new = validate_name(new)
    changes = validate_changes(changes)
    doc = copy.deepcopy(doc)
    src = doc.get(source)
    if src is None:
        raise FleetRefusal("source-missing", (
            f"'{source}' is not a deployment in this fleet, so there is nothing to copy. "
            f"See them: gapp ci list"
        ))
    if new in doc:
        raise FleetRefusal("name-taken", (
            f"'{new}' is already a deployment ({doc[new].get('repo')} on {doc[new].get('project')}). "
            f"Pick another name."
        ))
    project = validate_project(project) if project else src.get("project")
    keys = manifest_keys(without_unknown_keys(src))
    keys.pop("domain", None)
    entry = {"id": allocate_id(doc, new, project), "repo": src.get("repo"), "project": project, **keys}
    entry = _apply_changes(entry, changes)
    doc[new] = entry
    info = {
        "copied_env": [e.get("name") for e in (keys.get("env") or []) if isinstance(e, dict) and e.get("name")],
        "secrets": secrets_declared(entry),
        "project_from_source": not bool(project) or project == src.get("project"),
    }
    return doc, entry, info


def rename_entry(doc: dict, old: str, new: str) -> dict:
    """Change the name only; the id and every GCP resource stay as they are."""
    new = validate_name(new)
    doc = copy.deepcopy(doc)
    if old not in doc:
        raise FleetRefusal("deployment-missing", f"'{old}' is not a deployment in this fleet. See them: gapp ci list")
    if new in doc:
        raise FleetRefusal("name-taken", f"'{new}' is already a deployment. Pick another name.")
    doc[new] = doc.pop(old)
    return doc


# --- the overlay the deploy run applies ---


def merge_manifest(base: dict, overlay: dict) -> dict:
    """Deep-merge `overlay` into `base`: mappings merge key by key, lists of
    named mappings (such as `env`) merge by `name` with the overlay's item
    winning, any other value is replaced. Keys the overlay does not set
    are left as they are.
    """
    out = copy.deepcopy(base or {})
    for key, value in (overlay or {}).items():
        current = out.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            out[key] = merge_manifest(current, value)
        elif isinstance(value, list) and isinstance(current, list) and _named(value) and _named(current):
            merged = {item["name"]: item for item in current}
            for item in value:
                merged[item["name"]] = item
            out[key] = list(merged.values())
        else:
            out[key] = copy.deepcopy(value)
    return out


def _named(items: list) -> bool:
    return bool(items) and all(isinstance(i, dict) and "name" in i for i in items)
