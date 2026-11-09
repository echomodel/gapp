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
            "altostrat": {"account": "admin@example.com", "discovery": "off"}
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
            "default": {"account": "owner-a@example.com", "fleet_repo": None}
        }
    }
    save_config(config)
    
    # Check raw file content
    with open(get_config_file()) as f:
        raw = yaml.safe_load(f)
        assert "fleet_repo" not in raw["profiles"]["default"]


def test_migrate_legacy_solutions_yaml(tmp_path, monkeypatch):
    """Verify automatic migration from legacy solutions.yaml."""
    # We must remove the config.yaml created by the fixture to test migration
    config_file = get_config_file()
    if config_file.exists():
        config_file.unlink()
        
    legacy_file = get_legacy_file()
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Old structure: {name: {details...}, defaults: {owner: ...}, account: ...}
    legacy_data = {
        "project-status": {"project_id": "proj-123", "repo_path": "/ws/status"},
        "defaults": {"owner": "owner-a"},
        "account": "someone@example.com",
    }
    with open(legacy_file, "w") as f:
        yaml.dump(legacy_data, f)
        
    config = load_config()
    
    # The account migrates; the owner (namespaces removed in 4.0) does not.
    assert config["profiles"]["default"] == {"discovery": "on", "account": "someone@example.com"}


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


def test_an_owner_key_in_any_profile_is_an_error_naming_the_key_and_file():
    """gapp does not accept config it does not understand: owner namespaces were removed."""
    import pytest
    from gapp.admin.sdk.config import ConfigError
    path = get_config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"active": "default", "profiles": {
            "default": {"discovery": "on"}, "work": {"owner": "alice"}}}, f)
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert str(exc.value) == (
        f"Your gapp config {path} has an `owner:` key in profile 'work'. Owner namespaces were "
        f"removed in gapp 4.0, so gapp no longer accepts it: delete that line, then run the command again."
    )
