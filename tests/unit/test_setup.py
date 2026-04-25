"""Tests for gapp.sdk.core — GCP foundation provisioning."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_setup_enables_apis_and_creates_bucket(tmp_path, monkeypatch, sdk):
    """Verify setup enables foundation APIs and creates the deterministic bucket."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = sdk.setup(project_id="test-proj-123")
    
    assert res["name"] == "my-app"
    assert res["project_id"] == "test-proj-123"
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    
    # Verify core APIs were enabled via provider
    apis = {call[1] for call in sdk.provider.apis_enabled}
    assert "run.googleapis.com" in apis
    assert "cloudbuild.googleapis.com" in apis
    
    # Verify bucket creation
    assert "gapp-my-app-test-proj-123" in sdk.provider.buckets


def test_setup_with_owner_namespace(tmp_path, monkeypatch, sdk):
    """Verify setup uses the owner namespace in the bucket name and label key."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    sdk.set_owner("owner-a")
    res = sdk.setup(project_id="test-proj-123")
    
    assert res["bucket"] == "gapp-owner-a-my-app-test-proj-123"
    assert res["label_status"] == "added"
    # Key: gapp_owner-a_my-app (clean underscores)
    assert "gapp_owner-a_my-app" in sdk.provider.project_labels["test-proj-123"]


def test_setup_with_env_scoping(tmp_path, monkeypatch, sdk):
    """Verify setup supports environment names in bucket and labels."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = sdk.setup(project_id="test-proj-123", env="prod")
    
    # env is no longer in the bucket name
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    assert res["env"] == "prod"
    # env lives in the key trailing segment; value is purely the contract version
    assert "gapp__my-app_prod" in sdk.provider.project_labels["test-proj-123"]
    assert sdk.provider.project_labels["test-proj-123"]["gapp__my-app_prod"] == "v-3"
