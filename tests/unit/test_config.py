"""Tests for gapp.sdk.config — XDG config management with profiles."""

from pathlib import Path
import yaml
from gapp.admin.sdk.config import (
    get_config_dir, get_config_file, load_config, save_config, 
    get_active_profile, get_active_config, get_legacy_file
)


def test_config_dir_uses_xdg(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = get_config_dir()
    assert result == tmp_path / "gapp"


def test_load_config_empty_returns_defaults():
    """No config file yet — returns default profile structure."""
    result = load_config()
    assert result["active"] == "default"
    assert result["profiles"]["default"]["discovery"] == "on"


def test_save_and_load_roundtrip():
    """Verify full config roundtrip with multiple profiles."""
    config = {
        "active": "altostrat",
        "profiles": {
            "default": {"account": "owner-a@example.com", "discovery": "on"},
            "altostrat": {"account": "admin@example.com", "discovery": "off", "owner": "user-a"}
        }
    }
    save_config(config)
    loaded = load_config()
    
    assert loaded["active"] == "altostrat"
    assert loaded["profiles"]["altostrat"]["account"] == "admin@example.com"
    assert loaded["profiles"]["default"]["account"] == "owner-a@example.com"


def test_save_prunes_missing_attributes():
    """save_config should not write 'null' values for missing fields."""
    config = {
        "active": "default",
        "profiles": {
            "default": {"account": "owner-a@example.com", "owner": None}
        }
    }
    save_config(config)
    
    # Check raw file content
    with open(get_config_file()) as f:
        raw = yaml.safe_load(f)
        assert "owner" not in raw["profiles"]["default"]


def test_migrate_legacy_solutions_yaml(tmp_path, monkeypatch):
    """Verify automatic migration from legacy solutions.yaml."""
    legacy_file = get_legacy_file()
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Old structure: {name: {details...}, defaults: {owner: ...}}
    legacy_data = {
        "project-status": {"project_id": "proj-123", "repo_path": "/ws/status"},
        "defaults": {"owner": "owner-a"}
    }
    with open(legacy_file, "w") as f:
        yaml.dump(legacy_data, f)
        
    config = load_config()
    
    # Verify migration results
    # (Note: we ripped out the registry, so solutions are currently IGNORED in migration, 
    # but the owner should be preserved if we kept that logic)
    assert config["profiles"]["default"]["owner"] == "owner-a"


def test_get_active_config_resolution():
    """Verify get_active_config returns settings for the right profile."""
    config = {
        "active": "work",
        "profiles": {
            "default": {"account": "personal@example.com"},
            "work": {"account": "professional@example.com"}
        }
    }
    save_config(config)
    
    active = get_active_config()
    assert active["account"] == "professional@example.com"
