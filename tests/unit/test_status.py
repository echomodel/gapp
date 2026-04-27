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

    # Register project (env-blind label, optional gapp-env)
    sdk.provider.project_labels["proj-123"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    # Bucket name: gapp-{solution}-{project_id} — owner-blind, env-blind
    sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state")] = {"service_url": "https://my-app.run.app"}

    res = sdk.status()
    assert res.initialized is True
    assert res.deployment.project == "proj-123"
    assert len(res.deployment.services) == 1
    assert res.deployment.services[0].name == "my-app"


def test_status_with_explicit_solution_skips_local_discovery(tmp_path, monkeypatch, sdk):
    """status(name=...) skips git-root/gapp.yaml discovery entirely.

    Caller is in a directory with no gapp.yaml; passing a solution name
    must still resolve cleanly via cloud labels and TF outputs.
    """
    monkeypatch.chdir(tmp_path)  # no gapp.yaml here
    sdk.provider.project_labels["proj-123"] = {"gapp__remote-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-remote-app-proj-123", "terraform/state")] = {
        "service_url": "https://remote-app.run.app",
    }

    res = sdk.status(name="remote-app")
    assert res.initialized is True
    assert res.name == "remote-app"
    assert res.deployment.project == "proj-123"
    assert len(res.deployment.services) == 1
    assert res.deployment.services[0].url == "https://remote-app.run.app"


def test_status_tolerates_invalid_local_manifest(tmp_path, monkeypatch, sdk):
    """status doesn't fail validation on a stale or unsupported gapp.yaml.

    A repo with a manifest that wouldn't pass strict schema validation
    (e.g., legacy fields, v2 secret shape) must still be probable for
    deployment status. Status is read-only; manifest enforcement belongs
    in setup/deploy, not here.
    """
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    # Bogus field — would fail strict schema.
    (repo / "gapp.yaml").write_text(
        "name: stale-app\n"
        "service:\n"
        "  bogus_field: 1\n"
    )
    monkeypatch.chdir(repo)

    sdk.provider.project_labels["proj-123"] = {"gapp__stale-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-stale-app-proj-123", "terraform/state")] = {
        "service_url": "https://stale-app.run.app",
    }

    res = sdk.status()  # no exception
    assert res.initialized is True
    assert res.name == "stale-app"
    assert res.deployment.project == "proj-123"
