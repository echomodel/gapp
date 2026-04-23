"""Tests for gapp.sdk.deploy — deployment and dry-run logic."""

import subprocess
import pytest
from pathlib import Path
from gapp.admin.sdk.deploy import deploy_solution
from gapp.admin.sdk.context import set_owner


@pytest.fixture
def mock_deploy_discovery(monkeypatch):
    """Mock discovery for deployment tests."""
    def _mock_run(args, **kwargs):
        class MockProc:
            returncode = 0
            # Mock project list finding a project with labels
            stdout = '[{"projectId": "proj-123", "labels": {"gapp-my-app": "default"}}]'
        return MockProc()
    
    from gapp.admin.sdk import context
    monkeypatch.setattr(context, "run_gcloud", _mock_run)


def test_deploy_dry_run_singular(tmp_path, monkeypatch, mock_deploy_discovery):
    """Verify dry-run correctly resolves singular deployment info."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = deploy_solution(dry_run=True)
    
    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp-my-app"
    assert res["project_id"] == "proj-123"
    assert res["status"] == "ready"
    assert len(res["services"]) == 1
    assert res["services"][0]["name"] == "my-app"


def test_deploy_dry_run_workspace(tmp_path, monkeypatch, mock_deploy_discovery):
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
    
    # Update mock to find project labeled with repo name
    def _mock_run_workspace(args, **kwargs):
        class MockProc:
            returncode = 0
            stdout = '[{"projectId": "proj-ws", "labels": {"gapp-app": "default"}}]'
        return MockProc()
    from gapp.admin.sdk import context
    monkeypatch.setattr(context, "run_gcloud", _mock_run_workspace)
    
    res = deploy_solution(dry_run=True)
    
    assert res["name"] == "app" # Derived from folder name
    assert len(res["services"]) == 2
    assert {s["name"] for s in res["services"]} == {"my-api", "my-worker"}


def test_deploy_dry_run_with_owner(tmp_path, monkeypatch, mock_deploy_discovery):
    """Verify dry-run includes owner and scoped label."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    set_owner("owner-a")
    res = deploy_solution(dry_run=True)
    
    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp-owner-a-my-app"
