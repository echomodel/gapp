"""Tests for gapp.sdk.context — profile-aware context resolution."""

import subprocess
from pathlib import Path
import pytest
from gapp.admin.sdk.context import (
    get_active_profile, set_active_profile, get_owner, set_owner,
    get_account, set_account, is_discovery_on, set_discovery,
    get_label_key, get_bucket_name, resolve_solution
)


def test_profile_switching():
    """Verify switching active profiles creates defaults if needed."""
    assert get_active_profile() == "default"
    
    set_active_profile("altostrat")
    assert get_active_profile() == "altostrat"
    assert is_discovery_on() is True  # Default for new profile


def test_owner_and_account_scoping():
    """Verify settings are scoped to the active profile."""
    set_active_profile("default")
    set_owner("owner-a")
    set_account("owner-a@example.com")
    
    set_active_profile("work")
    set_owner("professional")
    set_account("pro@example.com")
    
    assert get_owner() == "professional"
    assert get_account() == "pro@example.com"
    
    set_active_profile("default")
    assert get_owner() == "owner-a"
    assert get_account() == "owner-a@example.com"


def test_discovery_toggle():
    """Verify discovery policy can be turned off per profile."""
    assert is_discovery_on() is True
    set_discovery("off")
    assert is_discovery_on() is False


def test_label_key_generation():
    """Verify label key follows 'no defaults' rule."""
    # 1. No owner, default env
    set_owner(None)
    assert get_label_key("my-app", env="default") == "gapp-my-app"
    
    # 2. With owner, default env
    set_owner("owner-a")
    assert get_label_key("my-app", env="default") == "gapp-owner-a-my-app"
    
    # 3. With owner and custom env
    assert get_label_key("my-app", env="prod") == "gapp-owner-a-my-app-prod"


def test_bucket_name_generation():
    """Verify bucket name follows 'no defaults' rule."""
    # 1. No owner, default env
    set_owner(None)
    assert get_bucket_name("my-app", "proj-123", env="default") == "gapp-my-app-proj-123"
    
    # 2. With owner, default env
    set_owner("owner-a")
    assert get_bucket_name("my-app", "proj-123", env="default") == "gapp-owner-a-my-app-proj-123"
    
    # 3. With owner and custom env
    assert get_bucket_name("my-app", "proj-123", env="prod") == "gapp-owner-a-my-app-proj-123-prod"


def test_resolve_solution_from_cwd(tmp_path, monkeypatch):
    """Verify resolve_solution reads the local gapp.yaml."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "gapp.yaml").write_text("name: project-status")
    
    # We also need a .git root for discovery to work
    (tmp_path / ".git").mkdir()
    
    ctx = resolve_solution()
    assert ctx["name"] == "project-status"
    assert ctx["repo_path"] == str(tmp_path)
