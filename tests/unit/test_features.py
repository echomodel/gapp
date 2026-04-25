"""Tests for gapp.sdk.core — high-level feature logic."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


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
    assert ctx["project_id"] is None # Discovery skipped
    
    # Policy: Managed mode
    sdk.set_discovery("on")
    ctx = sdk.resolve_full_context()
    assert ctx["project_id"] == "proj-123" # Discovery found it
