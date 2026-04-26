"""Tests for gapp.sdk.core — config/identity and naming."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_profile_switching(sdk):
    """Verify switching active profiles creates defaults if needed."""
    assert sdk.get_active_profile() == "default"
    sdk.set_active_profile("altostrat")
    assert sdk.get_active_profile() == "altostrat"
    assert sdk.is_discovery_on() is True


def test_owner_and_account_scoping(sdk):
    """Verify settings are scoped to the active profile."""
    sdk.set_active_profile("default")
    sdk.set_owner("owner-a")
    sdk.set_account("test-user@example.com")

    sdk.set_active_profile("work")
    sdk.set_owner("professional")
    sdk.set_account("other-user@example.com")

    assert sdk.get_owner() == "professional"
    assert sdk.get_account() == "other-user@example.com"

    sdk.set_active_profile("default")
    assert sdk.get_owner() == "owner-a"
    assert sdk.get_account() == "test-user@example.com"


def test_discovery_toggle(sdk):
    """Verify discovery policy can be turned off per profile."""
    assert sdk.is_discovery_on() is True
    sdk.set_discovery("off")
    assert sdk.is_discovery_on() is False


def test_label_key_generation(sdk):
    """Solution label keys are env-blind. Owner segment empty for global."""
    sdk.set_owner(None)
    assert sdk.get_label_key("my-app") == "gapp__my-app"

    sdk.set_owner("owner-a")
    assert sdk.get_label_key("my-app") == "gapp_owner-a_my-app"


def test_bucket_name_is_owner_blind_and_env_blind(sdk):
    """Bucket name is just gapp-{solution}-{project_id}."""
    sdk.set_owner(None)
    assert sdk.get_bucket_name("my-app", "proj-123") == "gapp-my-app-proj-123"

    sdk.set_owner("owner-a")
    # Owner makes no difference to the bucket name — it lives at label/identity layer.
    assert sdk.get_bucket_name("my-app", "proj-123") == "gapp-my-app-proj-123"


def test_resolve_solution_from_cwd(tmp_path, monkeypatch, sdk):
    """resolve_solution reads the local gapp.yaml."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: project-status")
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)

    ctx = sdk.resolve_solution()
    assert ctx is not None
    assert ctx["name"] == "project-status"
