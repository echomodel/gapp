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
    # Mock project labeled with the new underscore format
    provider.project_labels["proj-123"] = {"gapp__my--app": "v-2"}
    
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp__my--app"
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
    # Mock project labeled with the repo name (underscore format)
    provider.project_labels["proj-ws"] = {"gapp__app": "v-2"}
    
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["name"] == "app"
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
    # Mock project labeled with owner scope
    provider.project_labels["proj-123"] = {"gapp_owner--a_my--app": "v-2"}
    
    res = deploy_solution(dry_run=True, provider=provider)
    
    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp_owner--a_my--app"
