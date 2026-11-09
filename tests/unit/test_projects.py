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
    sdk.provider.host("p1", "my-app", env="prod")
    sdk.provider.host("p2", "my-app", env="dev")

    # Trying to rebind p2 from dev → prod would create my-app/prod on
    # both p1 and p2. Refuse even with force.
    with pytest.raises(RuntimeError, match="cross-project") as exc:
        sdk.set_project_env("p2", env="prod", force=True)
    assert "deployment 'my-app' also exists on project 'p1'" in str(exc.value)
    assert "gcloud storage buckets update gs://gapp-my-app-p1 --remove-labels=gapp-deployment" in str(exc.value)


def test_set_env_force_allows_a_rebind_with_no_duplicate(sdk):
    sdk.provider.host("p1", "other-app", env="prod")
    sdk.provider.host("p2", "my-app", env="dev")
    assert sdk.set_project_env("p2", env="prod", force=True)["status"] == "updated"


def test_set_env_refusal_counts_the_projects_deployments(sdk):
    sdk.provider.host("p1", "a", env="prod")
    sdk.provider.host("p1", "b", env="prod")
    with pytest.raises(RuntimeError, match=r"\(2 solution\(s\) on this project would be reclassified"):
        sdk.set_project_env("p1", env="dev")


def test_set_env_reserved_name_rejected(sdk):
    """env='default' is reserved and rejected."""
    with pytest.raises(ValueError, match="reserved"):
        sdk.set_project_env("p1", env="default")


def test_clear_env_removes_label(sdk):
    """clear_project_env deletes the gapp-env label."""
    sdk.provider.host("p1", "my-app", env="prod")

    res = sdk.clear_project_env("p1")
    assert res["status"] == "removed"
    assert res["previous"] == "prod"
    assert "gapp-env" not in sdk.provider.project_labels["p1"]
    # The hosted mark is untouched.
    assert sdk.provider.project_labels["p1"]["gapp"] == "hosted"


def test_clear_env_absent_is_noop(sdk):
    """clear_project_env on an undefined-env project is harmless."""
    sdk.provider.host("p1", "my-app")
    res = sdk.clear_project_env("p1")
    assert res["status"] == "absent"


def test_read_project_env(sdk):
    """read_project_env returns env or None."""
    sdk.provider.project_labels["p1"] = {"gapp-env": "prod"}
    sdk.provider.host("p2", "my-app")

    assert sdk.read_project_env("p1") == "prod"
    assert sdk.read_project_env("p2") is None
