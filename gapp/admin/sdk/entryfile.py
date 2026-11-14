"""The entry file: `gapp.yaml`, where everything gapp keeps in a fleet starts.

    format: 1
    deployments:
      my-app:
        id: my-app
        repo: github.com/owner/my-app
        project: my-project
        ref: main
        gapp: 4.0.0
        settings: {...}

`format` says how the fleet is laid out, and it is the first thing read.
A gapp that does not know the format stops there: it reads nothing else,
so it can neither misread a newer layout nor rewrite one it does not
understand. Both the key and its place at the top of this file are fixed
for good, which is what lets any later gapp refuse an older file cleanly.

Each definition records, in `gapp`, the version of the gapp that wrote it.
That is the version a runner deploys it with, and it only changes through
`gapp upgrade`.

Keys this gapp does not know are kept as they are, at the top level and in
other deployments' definitions. A fleet may hold definitions written by a
newer gapp, and changing one definition must not disturb the rest.
"""

from __future__ import annotations

import re

import yaml

from gapp import __version__

FILE_NAME = "gapp.yaml"
FORMAT = 1

OWN_FIELDS = ("id", "repo", "project", "ref", "gapp")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")


class EntryFileRefusal(Exception):
    """The entry file cannot be used as it is. The message says what was found and what to do."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def empty() -> dict:
    return {"format": FORMAT, "deployments": {}}


def parse(text: str | None, where: str = "The fleet file") -> dict:
    """The file's contents as a document. No text is a fleet with nothing in it.

    Raises EntryFileRefusal before looking at anything but `format` when
    the format is missing or is not one this gapp reads.
    """
    if text is None or not text.strip():
        return empty()
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise EntryFileRefusal("not-yaml", f"{where} is not valid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise EntryFileRefusal("bad-file", f"{where} is not a gapp file: it has no keys at its top level.")
    _check_format(doc.get("format"), "format" in doc, where)
    deployments = doc.get("deployments")
    if deployments is None:
        doc["deployments"] = {}
    elif not isinstance(deployments, dict) or not all(isinstance(d, dict) for d in deployments.values()):
        raise EntryFileRefusal("bad-file", f"{where}: 'deployments' is not a mapping of names to definitions.")
    return doc


def _check_format(found, present: bool, where: str) -> None:
    if not present:
        raise EntryFileRefusal(
            "no-format",
            f"{where} has no 'format' at its top, so it was not written by gapp {__version__} or later. "
            f"This gapp does not read it and has changed nothing.",
        )
    if isinstance(found, bool) or not isinstance(found, int):
        raise EntryFileRefusal(
            "bad-format",
            f"{where} has format {found!r}, which is not a whole number. This gapp has changed nothing.",
        )
    if found > FORMAT:
        raise EntryFileRefusal(
            "newer-format",
            f"{where} is in format {found}; gapp {__version__} reads format {FORMAT}. "
            f"Install a newer gapp. Nothing was changed.",
        )
    if found < FORMAT:
        raise EntryFileRefusal(
            "older-format",
            f"{where} is in format {found}, which gapp {__version__} no longer reads (it reads format {FORMAT}). "
            f"Nothing was changed.",
        )


def dump(doc: dict) -> str:
    """The document as text, `format` first."""
    ordered = {"format": doc.get("format", FORMAT), "deployments": doc.get("deployments") or {}}
    ordered.update({k: v for k, v in doc.items() if k not in ordered})
    return yaml.safe_dump(ordered, sort_keys=False, default_flow_style=False)


def new_definition(*, id: str, project: str, repo: str | None = None, ref: str | None = None) -> dict:
    """A definition as this gapp writes one: its own fields, then empty settings."""
    definition = {"id": id}
    if repo:
        definition["repo"] = repo
    definition["project"] = project
    if ref:
        definition["ref"] = ref
    definition["gapp"] = __version__
    definition["settings"] = {}
    return definition


def version_key(version) -> tuple:
    """A gapp version in an order that can be compared. A release candidate comes before its release."""
    match = _VERSION_RE.match(version) if isinstance(version, str) else None
    if not match:
        raise EntryFileRefusal("bad-version", f"{version!r} is not a gapp version (such as 4.0.0).")
    major, minor, patch, rc = match.groups()
    return int(major), int(minor), int(patch), int(rc) if rc is not None else float("inf")


def standing(definition: dict, name: str) -> str:
    """How a definition's version stands against this gapp.

    "current": written by this version.
    "older":   written by an earlier one; `gapp upgrade` brings it here.
    "newer":   written by a later one; this gapp must not change or deploy it.
    """
    recorded = definition.get("gapp")
    if recorded is None:
        raise EntryFileRefusal(
            "no-version",
            f"Deployment '{name}' does not record the gapp version that wrote it, so this gapp "
            f"cannot tell how to treat it. Nothing was changed.",
        )
    theirs, mine = version_key(recorded), version_key(__version__)
    return "current" if theirs == mine else ("older" if theirs < mine else "newer")


def require_current(definition: dict, name: str, to_do: str) -> None:
    """Refuse `to_do` unless the definition was written by this gapp's version."""
    where = standing(definition, name)
    if where == "newer":
        raise EntryFileRefusal(
            "newer-definition",
            f"Deployment '{name}' was written by gapp {definition['gapp']}; this is gapp {__version__}. "
            f"Install gapp {definition['gapp']} or later to {to_do}. Nothing was changed.",
        )
    if where == "older":
        raise EntryFileRefusal(
            "older-definition",
            f"Deployment '{name}' was written by gapp {definition['gapp']}; this is gapp {__version__}. "
            f"Bring it up to this version first, then {to_do}: gapp upgrade --deployment {name}",
        )
