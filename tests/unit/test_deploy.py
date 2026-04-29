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


# -- --rebuild flag and --ref threading --


def _setup_real_deploy(tmp_path, monkeypatch, sdk, ref_sha="abc123def456"):
    """Set up a deployable repo + populate provider state for a real apply."""
    repo = _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["proj-123"] = {
        "gapp__my-app": "v-3",
    }
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123"}

    captured = {}

    def fake_resolve_ref(self, path, ref):
        captured["resolved_ref"] = ref
        return ref_sha

    def fake_prepare_build_dir(path, image, ep, ref="HEAD"):
        captured["build_ref"] = ref
        return str(tmp_path / "build"), ep

    monkeypatch.setattr(GappSDK, "_resolve_ref", fake_resolve_ref)
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(core_mod, "_prepare_build_dir", fake_prepare_build_dir)
    (tmp_path / "build").mkdir()

    builds_called = {"count": 0}
    original_submit = sdk.provider.submit_build_sync

    def counting_submit(*args, **kwargs):
        builds_called["count"] += 1
        return original_submit(*args, **kwargs)

    sdk.provider.submit_build_sync = counting_submit
    return repo, captured, builds_called


def test_deploy_skips_build_when_image_exists(tmp_path, monkeypatch, sdk):
    """Default behavior: existing image short-circuits docker build."""
    _, _, builds_called = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: True

    sdk.deploy()

    assert builds_called["count"] == 0


def test_deploy_rebuild_forces_build_even_when_image_exists(tmp_path, monkeypatch, sdk):
    """--rebuild bypasses the image-exists short-circuit."""
    _, _, builds_called = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: True

    sdk.deploy(rebuild=True)

    assert builds_called["count"] == 1


def test_deploy_ref_is_threaded_into_build(tmp_path, monkeypatch, sdk):
    """--ref reaches both _resolve_ref and _prepare_build_dir (no silent HEAD)."""
    _, captured, _ = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: False

    sdk.deploy(ref="v1.2.3")

    assert captured["resolved_ref"] == "v1.2.3"
    assert captured["build_ref"] == "v1.2.3"


def test_deploy_default_ref_is_head(tmp_path, monkeypatch, sdk):
    """No --ref → resolve and archive HEAD."""
    _, captured, _ = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: False

    sdk.deploy()

    assert captured["resolved_ref"] == "HEAD"
    assert captured["build_ref"] == "HEAD"
