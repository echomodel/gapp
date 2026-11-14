"""This machine's gapp config: profiles, and what each one holds.

A profile is a separate fleet and a separate Google Cloud login on one
machine. One is active at a time, and every command works in it. The file
holds the active profile's name and, per profile, the login gapp runs
gcloud as and the repo its fleet is kept in when it is not kept on this
machine.

gapp is the only writer of the file. A key it does not know is left out
of what it reads and of what it next writes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

DEFAULT_PROFILE = "default"
PROFILE_KEYS = ("account", "fleet_repo")
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class ConfigError(RuntimeError):
    """The config file could not be read."""


class ConfigRefusal(Exception):
    """A config change that was refused. Nothing was changed."""


def get_config_dir() -> Path:
    """The gapp config directory, respecting XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(base) / "gapp"


def get_config_file() -> Path:
    return get_config_dir() / "config.yaml"


def load_config() -> dict:
    """The config: {"active": name, "profiles": {name: {key: value}}}. With
    no file there is one profile, `default`, and it is active."""
    path = get_config_file()
    data = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ConfigError(f"Your gapp config {path} could not be read: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"Your gapp config {path} is not a mapping.")
    profiles = {}
    for name, held in (data.get("profiles") or {}).items():
        held = held if isinstance(held, dict) else {}
        profiles[str(name)] = {k: held[k] for k in PROFILE_KEYS if held.get(k)}
    active = str(data.get("active") or DEFAULT_PROFILE)
    profiles.setdefault(active, {})
    return {"active": active, "profiles": profiles}


def save_config(config: dict) -> None:
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    profiles = {name: {k: held[k] for k in PROFILE_KEYS if held.get(k)}
                for name, held in (config.get("profiles") or {}).items()}
    out = {"active": config.get("active") or DEFAULT_PROFILE, "profiles": profiles}
    path.write_text(yaml.safe_dump(out, default_flow_style=False))


def get_active_profile() -> str:
    return load_config()["active"]


def get_active_config() -> dict:
    """What the active profile holds."""
    config = load_config()
    return config["profiles"][config["active"]]


# --- the fleet repo of the active profile ---


def get_fleet_repo() -> str | None:
    """The repo the active profile's fleet is kept in, or None when it is
    kept on this machine. `gapp fleet use` is the only writer."""
    return get_active_config().get("fleet_repo") or None


def set_fleet_repo(repo: str | None) -> None:
    config = load_config()
    config["profiles"][config["active"]]["fleet_repo"] = repo
    save_config(config)


# --- gapp config account ---


def account(email: str | None = None) -> dict:
    """Show the Google Cloud login the active profile uses, or set it."""
    config = load_config()
    name = config["active"]
    held = config["profiles"][name]
    changed = False
    if email is not None:
        email = email.strip().lower()
        if not email or "@" not in email or " " in email:
            raise ConfigRefusal(f"'{email}' is not an email address. Give the Google Cloud login: gapp config account <email>")
        changed = held.get("account") != email
        held["account"] = email
        save_config(config)
    return {"profile": name, "account": held.get("account") or None, "changed": changed}


# --- gapp config profile ---


def _name(name: str) -> str:
    name = (name or "").strip()
    if not PROFILE_NAME_RE.match(name):
        raise ConfigRefusal(f"'{name}' is not a profile name: lowercase letters, digits and hyphens, "
                            f"starting with a letter or digit.")
    return name


def _existing(config: dict, name: str) -> str:
    name = (name or "").strip()
    if name not in config["profiles"]:
        have = ", ".join(sorted(config["profiles"]))
        raise ConfigRefusal(f"There is no profile '{name}'. You have: {have}. Make one: gapp config profile add {name}")
    return name


def _fleet_dir(name: str) -> Path:
    return get_config_dir() / "fleets" / name


def profiles() -> dict:
    """Every profile, with what it holds and which is active."""
    config = load_config()
    return {"active": config["active"], "profiles": [
        {"name": name, "active": name == config["active"], "account": held.get("account") or None,
         "fleet_repo": held.get("fleet_repo") or None}
        for name, held in sorted(config["profiles"].items())]}


def use_profile(name: str) -> dict:
    config = load_config()
    name = _existing(config, name)
    changed = config["active"] != name
    config["active"] = name
    save_config(config)
    return {"profile": name, "changed": changed}


def add_profile(name: str) -> dict:
    config = load_config()
    name = _name(name)
    if name in config["profiles"]:
        raise ConfigRefusal(f"There is already a profile '{name}'. Switch to it: gapp config profile use {name}")
    config["profiles"][name] = {}
    save_config(config)
    return {"profile": name, "changed": True}


def remove_profile(name: str) -> dict:
    """Forget a profile. Its fleet is never removed with it: a fleet in a
    repo stays in the repo, and one on this machine that holds deployments
    refuses the removal."""
    from gapp.admin.sdk import fleetstore
    config = load_config()
    name = _existing(config, name)
    if name == config["active"]:
        raise ConfigRefusal(f"'{name}' is the active profile. Switch to another first: gapp config profile use <name>")
    if not config["profiles"][name].get("fleet_repo"):
        local = fleetstore.Location("local", path=fleetstore.local_path(name))
        try:
            count = len(fleetstore.read(local))
        except fleetstore.FleetUnavailable as e:
            raise ConfigRefusal(f"Profile '{name}' keeps its fleet on this machine, and it could not be read, so the "
                                f"profile was not removed. {e}") from e
        if count:
            raise ConfigRefusal(
                f"Profile '{name}' keeps its fleet on this machine, and it holds {count} "
                f"deployment{'' if count == 1 else 's'}. Removing the profile would leave them defined nowhere. "
                f"Move that fleet to a repo first: gapp config profile use {name}, then gapp fleet use <owner>/<repo>")
    del config["profiles"][name]
    save_config(config)
    return {"profile": name, "changed": True}


def rename_profile(old: str, new: str) -> dict:
    """Give a profile another name. Its login and its fleet go with it."""
    config = load_config()
    old = _existing(config, old)
    new = _name(new)
    if new in config["profiles"]:
        raise ConfigRefusal(f"There is already a profile '{new}'.")
    was, now = _fleet_dir(old), _fleet_dir(new)
    if was.exists() and now.exists():
        raise ConfigRefusal(f"A fleet folder for '{new}' is already on this machine, so '{old}' was not renamed. "
                            f"Pick another name.")
    if was.exists():
        os.replace(was, now)
    config["profiles"][new] = config["profiles"].pop(old)
    if config["active"] == old:
        config["active"] = new
    save_config(config)
    return {"profile": new, "was": old, "changed": True}
