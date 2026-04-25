"""Tests for gapp.sdk.core — context resolution and discovery."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_profile_switching(sdk):
    """Verify switching active profiles creates defaults if needed."""
    assert sdk.get_active_profile() == "default"
    sdk.set_active_profile("altostrat")
    assert sdk.get_active_profile() == "altostrat"
    assert sdk.is_discovery_on() is True


def test_owner_and_account_scoping(sdk):
    """Verify settings are scoped to the active profile."""
    sdk.set_active_profile("default")
    sdk.set_owner("owner-a")
    sdk.set_account("test-user@example.com")
    
    sdk.set_active_profile("work")
    sdk.set_owner("professional")
    sdk.set_account("other-user@example.com")
    
    assert sdk.get_owner() == "professional"
    assert sdk.get_account() == "other-user@example.com"
    
    sdk.set_active_profile("default")
    assert sdk.get_owner() == "owner-a"
    assert sdk.get_account() == "test-user@example.com"


def test_discovery_toggle(sdk):
    """Verify discovery policy can be turned off per profile."""
    assert sdk.is_discovery_on() is True
    sdk.set_discovery("off")
    assert sdk.is_discovery_on() is False


def test_label_key_generation(sdk):
    """Verify label key uses underscores and no double-hyphens."""
    # 1. No owner
    sdk.set_owner(None)
    assert sdk.get_label_key("my-app") == "gapp__my-app"
    
    # 2. With owner
    sdk.set_owner("owner-a")
    assert sdk.get_label_key("my-app") == "gapp_owner-a_my-app"


def test_bucket_name_generation(sdk):
    """Verify bucket name is Environment-Blind."""
    sdk.set_owner(None)
    assert sdk.get_bucket_name("my-app", "proj-123") == "gapp-my-app-proj-123"
    
    sdk.set_owner("owner-a")
    # env is ignored in bucket name now
    assert sdk.get_bucket_name("my-app", "proj-123") == "gapp-owner-a-my-app-proj-123"


def test_resolve_solution_from_cwd(tmp_path, monkeypatch, sdk):
    """Verify resolve_solution reads the local gapp.yaml."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: project-status")
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    
    ctx = sdk.resolve_solution()
    assert ctx is not None
    assert ctx["name"] == "project-status"


def test_discovery_policy_enforcement(tmp_path, monkeypatch, sdk):
    """Verify that when discovery is OFF, resolve_full_context skips label queries."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: project-status")
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    
    # Policy: Blind mode
    sdk.set_discovery("off")
    
    # Mock label in cloud using clean underscores
    sdk.provider.project_labels["proj-123"] = {"gapp__project-status": "v-2"}
    
    ctx = sdk.resolve_full_context()
    assert ctx["project_id"] is None # Correct: discovery skipped
    
    # Policy: Managed mode
    sdk.set_discovery("on")
    ctx = sdk.resolve_full_context()
    assert ctx["project_id"] == "proj-123" # Correct: discovery found it
