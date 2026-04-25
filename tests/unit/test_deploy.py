"""Tests for gapp.sdk.deploy — deployment and dry-run logic."""

import pytest
from pathlib import Path
from gapp.admin.sdk.deploy import deploy_solution
from gapp.admin.sdk.context import set_owner
from gapp.admin.sdk.cloud import get_provider


def test_deploy_dry_run_singular(tmp_path, monkeypatch):
    """Verify dry-run correctly resolves singular deployment info."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    provider = get_provider()
    # Mock project list finding a project with labels
    provider.project_labels["proj-123"] = {"gapp-my-app": "default"}
    
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp-my-app"
    assert res["project_id"] == "proj-123"
    assert res["status"] == "ready"
    assert len(res["services"]) == 1
    assert res["services"][0]["name"] == "my-app"


def test_deploy_dry_run_workspace(tmp_path, monkeypatch):
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
    
    provider = get_provider()
    # Update mock to find project labeled with repo name
    provider.project_labels["proj-ws"] = {"gapp-app": "default"}
    
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["name"] == "app" # Derived from folder name
    assert len(res["services"]) == 2
    assert {s["name"] for s in res["services"]} == {"my-api", "my-worker"}


def test_deploy_dry_run_with_owner(tmp_path, monkeypatch):
    """Verify dry-run includes owner and scoped label."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    set_owner("owner-a")
    provider = get_provider()
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp-owner-a-my-app"
