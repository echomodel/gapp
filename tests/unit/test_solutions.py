"""Tests for label-based app discovery via list_apps."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_list_apps_from_labels(sdk):
    """list_apps extracts every gapp solution label across projects."""
    sdk.provider.project_labels["proj-123"] = {
        "gapp-env": "prod",
        "gapp__api": "v-3",
        "gapp__worker": "v-3",
        "gapp_owner-a_status": "v-3",
    }

    res = sdk.list_apps(wide=True)
    apps = res["apps"]

    assert len(apps) == 3
    by_name = {a["name"]: a for a in apps}

    assert by_name["api"]["owner"] == "global"
    assert by_name["api"]["env"] == "prod"

    assert by_name["status"]["project"] == "proj-123"
    assert by_name["status"]["owner"] == "owner-a"
    assert by_name["status"]["env"] == "prod"


def test_list_apps_owner_scoped(sdk):
    """Default list filters to active owner only."""
    sdk.provider.project_labels["p1"] = {
        "gapp_alice_my-app": "v-3",
        "gapp_bob_other-app": "v-3",
    }

    sdk.set_owner("alice")
    res = sdk.list_apps()
    apps = res["apps"]

    assert len(apps) == 1
    assert apps[0]["name"] == "my-app"
    assert apps[0]["owner"] == "alice"


def test_list_apps_with_limit_reached(sdk):
    sdk.provider.project_labels["p1"] = {"gapp__app1": "v-3"}
    sdk.provider.project_labels["p2"] = {"gapp__app2": "v-3"}
    sdk.provider.project_labels["p3"] = {"gapp__app3": "v-3"}

    res = sdk.list_apps(project_limit=2, wide=True)
    assert any("limit reached" in w for w in res["warnings"])


def test_list_apps_flags_same_env_duplicates(sdk):
    """Two projects hosting the same solution under the same named env are flagged."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp_alice_my-app": "v-3",
    }
    sdk.provider.project_labels["p2"] = {
        "gapp-env": "prod",
        "gapp_alice_my-app": "v-3",
    }

    sdk.set_owner("alice")
    res = sdk.list_apps()

    assert len(res["apps"]) == 2
    assert all(a["duplicate"] for a in res["apps"])
    assert any("duplicate" in w.lower() for w in res["warnings"])


def test_list_apps_undefined_env_not_flagged_as_duplicate(sdk):
    """Multiple undefined-env projects with same solution are not corruption."""
    sdk.provider.project_labels["p1"] = {"gapp_alice_my-app": "v-3"}
    sdk.provider.project_labels["p2"] = {"gapp_alice_my-app": "v-3"}

    sdk.set_owner("alice")
    res = sdk.list_apps()

    assert len(res["apps"]) == 2
    assert not any(a["duplicate"] for a in res["apps"])
