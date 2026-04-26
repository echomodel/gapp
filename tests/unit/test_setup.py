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
    """Setup enables foundation APIs and creates the bucket; never writes gapp-env."""
    _repo(tmp_path, monkeypatch)

    res = sdk.setup(project_id="test-proj-123")

    assert res["name"] == "my-app"
    assert res["project_id"] == "test-proj-123"
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    assert res["env"] is None  # project had no gapp-env binding

    apis = {call[1] for call in sdk.provider.apis_enabled}
    assert "run.googleapis.com" in apis
    assert "cloudbuild.googleapis.com" in apis

    assert "gapp-my-app-test-proj-123" in sdk.provider.buckets
    # Solution label present, gapp-env label NOT written by setup.
    assert "gapp__my-app" in sdk.provider.project_labels["test-proj-123"]
    assert "gapp-env" not in sdk.provider.project_labels["test-proj-123"]


def test_setup_with_owner_namespace_drops_owner_from_bucket(tmp_path, monkeypatch, sdk):
    """Bucket name is owner-blind; only the solution label carries the owner."""
    _repo(tmp_path, monkeypatch)
    sdk.set_owner("owner-a")

    res = sdk.setup(project_id="test-proj-123")

    assert res["bucket"] == "gapp-my-app-test-proj-123"
    assert res["label_status"] == "added"
    assert "gapp_owner-a_my-app" in sdk.provider.project_labels["test-proj-123"]


def test_setup_env_verifies_against_project_binding(tmp_path, monkeypatch, sdk):
    """If --env is passed, it must match the project's bound gapp-env."""
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["test-proj-123"] = {"gapp-env": "prod"}

    # Match → ok
    res = sdk.setup(project_id="test-proj-123", env="prod")
    assert res["env"] == "prod"

    # Mismatch → refuse
    sdk.provider.project_labels.pop("test-proj-123", None)
    sdk.provider.project_labels["test-proj-123"] = {"gapp-env": "prod"}
    with pytest.raises(RuntimeError, match="bound to env='prod'"):
        sdk.setup(project_id="test-proj-123", env="dev")


def test_setup_undefined_env_with_no_env_arg_succeeds(tmp_path, monkeypatch, sdk):
    """No --env on a project with no gapp-env label proceeds."""
    _repo(tmp_path, monkeypatch)
    res = sdk.setup(project_id="test-proj-123")
    assert res["env"] is None


def test_setup_undefined_env_with_env_arg_refuses(tmp_path, monkeypatch, sdk):
    """--env on an undefined-env project refuses (must run set-env first)."""
    _repo(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="undefined"):
        sdk.setup(project_id="test-proj-123", env="prod")


def test_setup_layer1_cross_owner_check(tmp_path, monkeypatch, sdk):
    """Setup refuses if a different owner already has the same solution name."""
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["test-proj-123"] = {
        "gapp_alice_my-app": "v-3",
    }

    sdk.set_owner("bob")
    with pytest.raises(RuntimeError, match="already has a solution"):
        sdk.setup(project_id="test-proj-123")


def test_setup_layer1_force_overrides(tmp_path, monkeypatch, sdk):
    """force=True bypasses the cross-owner refusal."""
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["test-proj-123"] = {
        "gapp_alice_my-app": "v-3",
    }

    sdk.set_owner("bob")
    res = sdk.setup(project_id="test-proj-123", force=True)
    assert "gapp_bob_my-app" in sdk.provider.project_labels["test-proj-123"]
    # alice's label is untouched
    assert "gapp_alice_my-app" in sdk.provider.project_labels["test-proj-123"]


def test_setup_idempotent(tmp_path, monkeypatch, sdk):
    """Re-running setup on an already-installed solution is a no-op write."""
    _repo(tmp_path, monkeypatch)
    sdk.setup(project_id="test-proj-123")

    res = sdk.setup(project_id="test-proj-123")
    assert res["label_status"] == "exists"


def test_setup_reserved_env_name_rejected(tmp_path, monkeypatch, sdk):
    """--env=default is rejected as reserved."""
    _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reserved"):
        sdk.setup(project_id="test-proj-123", env="default")
