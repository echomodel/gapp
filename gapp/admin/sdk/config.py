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
    if not path.exists():
        return {
            "active": "default",
            "profiles": {"default": {"discovery": "on"}}
        }
    
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    
    # Ensure structure and migration
    if "profiles" not in data:
        # Migrate flat config to profiles
        old_owner = data.get("owner")
        old_account = data.get("account")
        p = {"discovery": "on"}
        if old_owner: p["owner"] = old_owner
        if old_account: p["account"] = old_account
        
        data = {
            "active": "default",
            "profiles": {"default": p}
        }
    
    if "active" not in data:
        data["active"] = "default"
        
    return data


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


def get_active_profile() -> str:
    """Return the name of the active profile."""
    return load_config().get("active", "default")


def get_active_config() -> dict:
    """Return the settings for the currently active profile."""
    config = load_config()
    active_name = config["active"]
    return config["profiles"].get(active_name, {"discovery": "on"})
