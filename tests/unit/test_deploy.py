"""Tests for gapp.sdk.core — deploy and dry-run."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _repo(tmp_path, monkeypatch, contents="name: my-app"):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text(contents)
    monkeypatch.chdir(repo)
    return repo


def test_deploy_dry_run_singular(tmp_path, monkeypatch, sdk):
    """Dry-run resolves a single-match deployment."""
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["proj-123"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    res = sdk.deploy(dry_run=True)

    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp__my-app"
    assert res["project_id"] == "proj-123"
    assert res["env"] == "prod"


def test_deploy_dry_run_workspace(tmp_path, monkeypatch, sdk):
    """Dry-run unrolls multi-service workspace."""
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

    sdk.provider.project_labels["proj-ws"] = {"gapp__app": "v-3"}

    res = sdk.deploy(dry_run=True)

    assert res["name"] == "app"
    assert len(res["services"]) == 2


def test_deploy_dry_run_with_owner(tmp_path, monkeypatch, sdk):
    """Dry-run includes owner-scoped label."""
    _repo(tmp_path, monkeypatch)
    sdk.set_owner("owner-a")
    sdk.provider.project_labels["proj-123"] = {
        "gapp_owner-a_my-app": "v-3",
    }

    res = sdk.deploy(dry_run=True)

    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp_owner-a_my-app"
    assert res["env"] is None  # project has no gapp-env binding


def test_deploy_dry_run_no_setup_pending(tmp_path, monkeypatch, sdk):
    """Dry-run with no project resolved still returns a preview."""
    _repo(tmp_path, monkeypatch)
    res = sdk.deploy(dry_run=True)

    assert res["dry_run"] is True
    assert res["status"] == "pending_setup"
    assert res["project_id"] is None
