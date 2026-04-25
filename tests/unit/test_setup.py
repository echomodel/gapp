"""Tests for gapp.sdk.setup — GCP foundation provisioning."""

import pytest
from pathlib import Path
from gapp.admin.sdk.setup import setup_solution
from gapp.admin.sdk.context import set_owner
from gapp.admin.sdk.cloud import get_provider


def test_setup_enables_apis_and_creates_bucket(tmp_path, monkeypatch):
    """Verify setup_solution enables foundation APIs and creates the deterministic bucket."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    provider = get_provider()
    res = setup_solution(project_id="test-proj-123", provider=provider)
    
    assert res["name"] == "my-app"
    assert res["project_id"] == "test-proj-123"
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    
    # Verify core APIs were enabled
    apis = {call[1] for call in provider.apis_enabled}
    assert "run.googleapis.com" in apis
    assert "cloudbuild.googleapis.com" in apis
    
    # Verify bucket creation
    assert "gapp-my-app-test-proj-123" in provider.buckets


def test_setup_with_owner_namespace(tmp_path, monkeypatch):
    """Verify setup_solution uses the owner namespace in the bucket name and label key."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    set_owner("owner-a")
    provider = get_provider()
    res = setup_solution(project_id="test-proj-123", provider=provider)
    
    assert res["bucket"] == "gapp-owner-a-my-app-test-proj-123"
    assert res["label_status"] == "added"
    # Key: gapp_<owner>_<solution> (hyphens protected)
    assert "gapp_owner--a_my--app" in provider.project_labels["test-proj-123"]


def test_setup_with_env_scoping(tmp_path, monkeypatch):
    """Verify setup_solution supports environment names in bucket and labels."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    provider = get_provider()
    res = setup_solution(project_id="test-proj-123", env="prod", provider=provider)
    
    # env != 'default', so it should appear in the bucket name and label key
    assert res["bucket"] == "gapp-my-app-test-proj-123-prod"
    assert res["env"] == "prod"
    assert "gapp__my--app_prod" in provider.project_labels["test-proj-123"]
    # Label value: v-2_env-prod
    assert provider.project_labels["test-proj-123"]["gapp__my--app_prod"] == "v-2_env-prod"
