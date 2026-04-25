"""Tests for gapp.sdk.context — profile-aware context resolution."""

import subprocess
import os
import pytest
from pathlib import Path
from gapp.admin.sdk.context import (
    get_active_profile, set_active_profile, get_owner, set_owner,
    get_account, set_account, is_discovery_on, set_discovery,
    get_label_key, get_bucket_name, resolve_solution
)


@pytest.fixture(autouse=True)
def mock_gcloud_auth(monkeypatch):
    """Mock gcloud auth list to allow set_account to pass."""
    def _mock_run(args, **kwargs):
        class MockProc:
            returncode = 0
            stdout = "test-user@example.com\nother-user@example.com"
        return MockProc()
    monkeypatch.setattr(subprocess, "run", _mock_run)


def test_profile_switching():
    """Verify switching active profiles creates defaults if needed."""
    assert get_active_profile() == "default"
    
    set_active_profile("altostrat")
    assert get_active_profile() == "altostrat"
    assert is_discovery_on() is True


def test_owner_and_account_scoping():
    """Verify settings are scoped to the active profile."""
    set_active_profile("default")
    set_owner("owner-a")
    set_account("test-user@example.com")
    
    set_active_profile("work")
    set_owner("professional")
    set_account("other-user@example.com")
    
    assert get_owner() == "professional"
    assert get_account() == "other-user@example.com"
    
    set_active_profile("default")
    assert get_owner() == "owner-a"
    assert get_account() == "test-user@example.com"


def test_discovery_toggle():
    """Verify discovery policy can be turned off per profile."""
    assert is_discovery_on() is True
    set_discovery("off")
    assert is_discovery_on() is False


def test_label_key_generation():
    """Verify label key follows 'no defaults' rule and uses underscores."""
    # 1. No owner, default env
    set_owner(None)
    # gapp__<name> with hyphen protection
    assert get_label_key("my-app", env="default") == "gapp__my--app"
    
    # 2. With owner, default env
    set_owner("owner-a")
    assert get_label_key("my-app", env="default") == "gapp_owner--a_my--app"
    
    # 3. With owner and custom env
    assert get_label_key("my-app", env="prod") == "gapp_owner--a_my--app_prod"


def test_bucket_name_generation():
    """Verify bucket name follows 'no defaults' rule and uses hyphens."""
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
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: project-status")
    (repo / ".git").mkdir()
    
    # CRITICAL: We must ensure mock_git returns THIS path
    monkeypatch.chdir(repo)
    
    # Force mock_git to return EXACTLY our test repo
    def _mock_run(args, **kwargs):
        if "git" in args and "rev-parse" in args:
            class MockProc:
                returncode = 0
                stdout = str(repo) + "\n"
            return MockProc()
        return subprocess.run(args, **kwargs)
    monkeypatch.setattr(subprocess, "run", _mock_run)

    ctx = resolve_solution()
    assert ctx is not None
    assert ctx["name"] == "project-status"
    assert ctx["repo_path"] == str(repo)
