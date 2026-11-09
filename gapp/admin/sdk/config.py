"""Configuration and path resolution for gapp."""

import os
from pathlib import Path

import yaml


def get_config_dir() -> Path:
    """Return the gapp config directory, respecting XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(base) / "gapp"


def get_config_file() -> Path:
    """Return the path to config.yaml."""
    return get_config_dir() / "config.yaml"


def get_legacy_file() -> Path:
    """Return the path to solutions.yaml."""
    return get_config_dir() / "solutions.yaml"


def load_config() -> dict:
    """Load the global config (active profile + all profiles)."""
    path = get_config_file()
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
            # Ensure basic structure
            if "profiles" not in data:
                data = {"active": "default", "profiles": {"default": data}}
            if "active" not in data:
                data["active"] = "default"
            return data
    
    # Try legacy migration
    legacy_path = get_legacy_file()
    if legacy_path.exists():
        with open(legacy_path) as f:
            legacy_data = yaml.safe_load(f) or {}
        
        owner = legacy_data.get("owner") or legacy_data.get("defaults", {}).get("owner")
        account = legacy_data.get("account")
        
        p = {"discovery": "on"}
        if owner: p["owner"] = owner
        if account: p["account"] = account
        
        return {
            "active": "default",
            "profiles": {"default": p}
        }
        
    return {
        "active": "default",
        "profiles": {"default": {"discovery": "on"}}
    }


def save_config(config: dict) -> None:
    """Save the global config, pruning missing attributes."""
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Prune None values from all profiles
    clean_profiles = {}
    for name, settings in config.get("profiles", {}).items():
        clean_profiles[name] = {k: v for k, v in settings.items() if v is not None}
    
    out = {
        "active": config.get("active", "default"),
        "profiles": clean_profiles
    }
    
    with open(path, "w") as f:
        yaml.dump(out, f, default_flow_style=False)


def get_fleet_repo() -> tuple[str | None, str | None]:
    """The fleet repo set on this machine for the active profile, and how it was set.

    Only `gapp ci init` writes it (`set_fleet_repo`); every other `ci`
    command reads it here and never looks it up anywhere else.
    """
    cfg = get_active_config()
    return cfg.get("fleet_repo") or None, cfg.get("fleet_repo_set_by") or None


def set_fleet_repo(repo: str | None, set_by: str | None = None) -> None:
    """Record the fleet repo for the active profile (None clears it)."""
    config = load_config()
    profile = config["profiles"].setdefault(config["active"], {"discovery": "on"})
    profile["fleet_repo"] = repo
    profile["fleet_repo_set_by"] = set_by if repo else None
    save_config(config)


def get_active_profile() -> str:
    """Return the name of the active profile."""
    return load_config().get("active", "default")


def get_active_config() -> dict:
    """Return the settings for the currently active profile."""
    config = load_config()
    active_name = config["active"]
    return config["profiles"].get(active_name, {"discovery": "on"})
