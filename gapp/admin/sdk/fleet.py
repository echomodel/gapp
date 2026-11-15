"""What a definition's own fields may hold, and the id a new one gets.

A definition's `id` is what every cloud resource of the deployment is
keyed by: the service and its account, the bucket, the secrets and the
Terraform state. It is set once, defaults to the deployment's name, and
two definitions on one project never share one.

Pure functions; no I/O.
"""

from __future__ import annotations

import re

# The service account is `<id>-sa`, and a service account id is at most
# 30 characters.
ID_MAX = 27

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# A branch, tag or commit of the app's repo. No spaces, and no leading dash
# so it can never be read as an option by git.
REF_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./@+-]{0,199}$")
PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


class FleetRefusal(Exception):
    """A value a definition refuses, with a code and a plain sentence."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


def validate_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not REF_RE.match(ref) or ".." in ref:
        raise FleetRefusal("invalid-input", (
            f"'{ref}' is not a ref gapp accepts: a branch, tag or commit of the app's repo, "
            f"with no spaces and not starting with a dash."
        ))
    return ref


def validate_project(project: str) -> str:
    project = (project or "").strip()
    if not PROJECT_RE.match(project):
        raise FleetRefusal("invalid-input", f"'{project}' is not a GCP project id.")
    return project


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
