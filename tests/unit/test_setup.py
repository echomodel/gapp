"""Tests for gapp.sdk.core — setup (foundation provisioning)."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _repo(tmp_path, monkeypatch, name="my-app"):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text(f"name: {name}")
    monkeypatch.chdir(repo)
    return repo


def test_setup_enables_apis_and_creates_bucket(tmp_path, monkeypatch, sdk):
    """Setup enables foundation APIs, creates the bucket and marks the project hosted."""
    _repo(tmp_path, monkeypatch)

    res = sdk.setup(project_id="test-proj-123")

    assert res["name"] == "my-app"
    assert res["project_id"] == "test-proj-123"
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    assert "env" not in res

    apis = {call[1] for call in sdk.provider.apis_enabled}
    assert "run.googleapis.com" in apis
    assert "cloudbuild.googleapis.com" in apis

    assert "gapp-my-app-test-proj-123" in sdk.provider.buckets
    # The bucket carries the deployment labels; the project only the hosted
    # mark: no per-deployment project label.
    assert sdk.provider.buckets["gapp-my-app-test-proj-123"]["labels"] == {
        "gapp-deployment": "my-app", "gapp-version": "4"}
    assert sdk.provider.project_labels["test-proj-123"] == {"gapp": "hosted"}
    assert res["label_status"] == "added"


def test_setup_has_no_owner_check_or_force(tmp_path, monkeypatch, sdk):
    """Owner namespaces are gone: setup over an existing deployment just re-labels it."""
    import inspect
    _repo(tmp_path, monkeypatch)
    sdk.provider.host("test-proj-123", "my-app")
    sdk.provider.buckets["gapp-my-app-test-proj-123"]["labels"]["gapp-owner"] = "alice"   # stray
    sdk.setup(project_id="test-proj-123")
    assert "force" not in inspect.signature(sdk.setup).parameters


def test_setup_idempotent(tmp_path, monkeypatch, sdk):
    """Re-running setup on an already-installed solution is a no-op write."""
    _repo(tmp_path, monkeypatch)
    sdk.setup(project_id="test-proj-123")

    writes = len([w for w in sdk.provider.label_writes if w[0] == "project"])
    res = sdk.setup(project_id="test-proj-123")
    assert res["label_status"] == "exists"
    assert len([w for w in sdk.provider.label_writes if w[0] == "project"]) == writes


def test_setup_marks_the_project_hosted_keeping_its_labels(tmp_path, monkeypatch, sdk):
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["test-proj-123"] = {"team": "x"}
    sdk.setup(project_id="test-proj-123")
    assert sdk.provider.project_labels["test-proj-123"] == {"team": "x", "gapp": "hosted"}


def test_setup_on_a_ci_runner_never_writes_project_labels(tmp_path, monkeypatch, sdk):
    """A CI run sets up everything but the project mark, which an Owner writes."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _repo(tmp_path, monkeypatch)
    res = sdk.setup(project_id="test-proj-123")
    assert res["label_status"] == "skipped"
    assert not [w for w in sdk.provider.label_writes if w[0] == "project"]
    assert "gapp-my-app-test-proj-123" in sdk.provider.buckets
    assert sdk.provider.buckets["gapp-my-app-test-proj-123"]["labels"]["gapp-deployment"] == "my-app"
    assert ("test-proj-123", "run.googleapis.com") in sdk.provider.apis_enabled
    assert sdk.provider.iam_bindings


def test_setup_refuses_a_deployment_a_newer_gapp_deployed(tmp_path, monkeypatch, sdk):
    _repo(tmp_path, monkeypatch)
    sdk.provider.host("test-proj-123", "my-app", version="5")
    with pytest.raises(RuntimeError, match="was deployed by gapp 5.x \\(service label gapp-version=5\\)"):
        sdk.setup(project_id="test-proj-123")


def test_setup_takes_no_env(tmp_path, monkeypatch, sdk):
    import inspect
    assert "env" not in inspect.signature(sdk.setup).parameters
