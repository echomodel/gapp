"""Tests for gapp.sdk.core — deployment and dry-run logic."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_deploy_dry_run_singular(tmp_path, monkeypatch, sdk):
    """Verify dry-run correctly resolves singular deployment info."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    # Mock project labeled with the new underscore format
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-2"}
    
    res = sdk.deploy(dry_run=True)
    
    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp__my-app"
    assert res["project_id"] == "proj-123"


def test_deploy_dry_run_workspace(tmp_path, monkeypatch, sdk):
    """Verify dry-run correctly unrolls multi-service workspace."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("paths: [services/api, services/worker]")
    
    api_dir = repo / "services/api"
    api_dir.mkdir(parents=True)
    (api_dir / "gapp.yaml").write_text("name: my-api")
    
    worker_dir = repo / "services/worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "gapp.yaml").write_text("name: my-worker")
    
    monkeypatch.chdir(repo)
    
    # Mock project labeled with the repo name
    sdk.provider.project_labels["proj-ws"] = {"gapp__app": "v-2"}
    
    res = sdk.deploy(dry_run=True)
    
    assert res["name"] == "app"
    assert len(res["services"]) == 2


def test_deploy_dry_run_with_owner(tmp_path, monkeypatch, sdk):
    """Verify dry-run includes owner and scoped label."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    sdk.set_owner("owner-a")
    # Mock project labeled with owner scope
    sdk.provider.project_labels["proj-123"] = {"gapp_owner-a_my-app": "v-2"}
    
    res = sdk.deploy(dry_run=True)
    
    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp_owner-a_my-app"
