"""Tests for gapp projects set-env / clear-env."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_set_env_initial(sdk):
    """First-time set on a fresh project just stamps the label."""
    res = sdk.set_project_env("p1", env="prod")
    assert res["status"] == "added"
    assert sdk.provider.project_labels["p1"]["gapp-env"] == "prod"


def test_set_env_no_change(sdk):
    """Setting to the same value is a no-op."""
    sdk.provider.project_labels["p1"] = {"gapp-env": "prod"}
    res = sdk.set_project_env("p1", env="prod")
    assert res["status"] == "exists"


def test_set_env_overwrite_without_force_refuses(sdk):
    """Overwriting an existing value requires force=True."""
    sdk.provider.project_labels["p1"] = {"gapp-env": "prod"}
    with pytest.raises(RuntimeError, match="Refusing to change"):
        sdk.set_project_env("p1", env="dev")


def test_set_env_overwrite_with_force_succeeds(sdk):
    """force=True allows the rebind."""
    sdk.provider.project_labels["p1"] = {"gapp-env": "prod"}
    res = sdk.set_project_env("p1", env="dev", force=True)
    assert res["status"] == "updated"
    assert res["previous"] == "prod"
    assert sdk.provider.project_labels["p1"]["gapp-env"] == "dev"


def test_set_env_force_refuses_on_cross_project_corruption(sdk):
    """force=True still refuses if the rebind would create cross-project duplication."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp_alice_my-app": "v-3",
    }
    sdk.provider.project_labels["p2"] = {
        "gapp-env": "dev",
        "gapp_alice_my-app": "v-3",
    }

    # Trying to rebind p2 from dev → prod would create alice/my-app/prod on
    # both p1 and p2. Refuse even with force.
    with pytest.raises(RuntimeError, match="cross-project"):
        sdk.set_project_env("p2", env="prod", force=True)


def test_set_env_reserved_name_rejected(sdk):
    """env='default' is reserved and rejected."""
    with pytest.raises(ValueError, match="reserved"):
        sdk.set_project_env("p1", env="default")


def test_clear_env_removes_label(sdk):
    """clear_project_env deletes the gapp-env label."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    res = sdk.clear_project_env("p1")
    assert res["status"] == "removed"
    assert res["previous"] == "prod"
    assert "gapp-env" not in sdk.provider.project_labels["p1"]
    # Solution label untouched.
    assert "gapp__my-app" in sdk.provider.project_labels["p1"]


def test_clear_env_absent_is_noop(sdk):
    """clear_project_env on an undefined-env project is harmless."""
    sdk.provider.project_labels["p1"] = {"gapp__my-app": "v-3"}
    res = sdk.clear_project_env("p1")
    assert res["status"] == "absent"


def test_read_project_env(sdk):
    """read_project_env returns env or None."""
    sdk.provider.project_labels["p1"] = {"gapp-env": "prod"}
    sdk.provider.project_labels["p2"] = {"gapp__my-app": "v-3"}

    assert sdk.read_project_env("p1") == "prod"
    assert sdk.read_project_env("p2") is None
