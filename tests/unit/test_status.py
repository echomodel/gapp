"""Tests for gapp.sdk.core — infrastructure health check."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_status_uninitialized(tmp_path, monkeypatch, sdk):
    """Verify status returns init step if no gapp.yaml found."""
    monkeypatch.chdir(tmp_path)
    res = sdk.status()
    assert res.initialized is False
    assert res.next_step.action == "init"


def test_status_pending_setup(tmp_path, monkeypatch, sdk):
    """Verify status returns setup step if no project attached."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = sdk.status()
    assert res.initialized is True
    assert res.next_step.action == "setup"


def test_status_ready(tmp_path, monkeypatch, sdk):
    """Verify status returns ready if services are found."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    # Register project and mock outputs using clean underscores
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-2"}
    sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state/default")] = {"service_url": "https://my-app.run.app"}
    
    res = sdk.status()
    assert res.initialized is True
    assert res.deployment.project == "proj-123"
    assert len(res.deployment.services) == 1
    assert res.deployment.services[0].name == "my-app"
