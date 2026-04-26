"""Tests for project resolution from solution labels.

Discovery in v-3 is solution-label-driven, not env-label-driven. The query
is `labels:gapp_<owner>_<solution>` (or `labels:gapp__<solution>` for global)
and the project's gapp-env is read from the same response.
"""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_resolve_single_match(sdk):
    """One project hosting the solution → resolves to it."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-1"
    assert res["env"] == "prod"


def test_resolve_zero_matches_raises(sdk):
    """No project hosting the solution → error."""
    sdk.set_owner(None)
    with pytest.raises(RuntimeError, match="not deployed"):
        sdk.resolve_project_for_solution("my-app")


def test_resolve_multi_match_no_env_raises(sdk):
    """Multiple projects, no --env → ambiguous, list candidates."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }
    sdk.provider.project_labels["proj-2"] = {
        "gapp-env": "dev",
        "gapp__my-app": "v-3",
    }

    with pytest.raises(RuntimeError, match="multiple projects"):
        sdk.resolve_project_for_solution("my-app")


def test_resolve_multi_match_env_narrows_to_one(sdk):
    """Multiple projects, --env narrows to exactly one → resolved."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }
    sdk.provider.project_labels["proj-2"] = {
        "gapp-env": "dev",
        "gapp__my-app": "v-3",
    }

    res = sdk.resolve_project_for_solution("my-app", env="prod")
    assert res["project_id"] == "proj-1"


def test_resolve_multi_match_env_zero_after_filter(sdk):
    """--env that matches none of the candidates → error listing what we DO have."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }
    sdk.provider.project_labels["proj-2"] = {
        "gapp-env": "dev",
        "gapp__my-app": "v-3",
    }

    with pytest.raises(RuntimeError, match="not found in env='staging'"):
        sdk.resolve_project_for_solution("my-app", env="staging")


def test_resolve_multi_match_same_env_corruption(sdk):
    """Multiple projects in the same named env → corruption refusal."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }
    sdk.provider.project_labels["proj-2"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    with pytest.raises(RuntimeError, match="corruption"):
        sdk.resolve_project_for_solution("my-app", env="prod")


def test_resolve_owner_namespacing(sdk):
    """alice's solution and bob's solution with same name don't collide."""
    sdk.provider.project_labels["proj-alice"] = {
        "gapp-env": "prod",
        "gapp_alice_my-app": "v-3",
    }
    sdk.provider.project_labels["proj-bob"] = {
        "gapp-env": "dev",
        "gapp_bob_my-app": "v-3",
    }

    sdk.set_owner("alice")
    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-alice"

    sdk.set_owner("bob")
    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-bob"


def test_resolve_explicit_project_bypasses_discovery(sdk):
    """--project bypasses solution-label discovery; just verifies state."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    res = sdk.resolve_project_for_solution("my-app", project="proj-1")
    assert res["project_id"] == "proj-1"
    assert res["env"] == "prod"


def test_resolve_explicit_project_env_mismatch_refuses(sdk):
    """--project with --env that doesn't match P's gapp-env → refuse."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    with pytest.raises(RuntimeError, match="bound to env='prod'"):
        sdk.resolve_project_for_solution("my-app", project="proj-1", env="dev")


def test_resolve_discovery_off_requires_project(sdk):
    """If discovery is off, the resolver requires --project."""
    sdk.set_discovery("off")
    sdk.provider.project_labels["proj-1"] = {"gapp__my-app": "v-3"}

    with pytest.raises(RuntimeError, match="Discovery is OFF"):
        sdk.resolve_project_for_solution("my-app")
