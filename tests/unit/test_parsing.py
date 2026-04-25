"""Tests for underscore-delimited label parsing and forward compatibility."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_parsing_new_underscore_labels(sdk):
    """Verify list_apps correctly parses underscore-delimited keys (env in key, not value)."""
    # 1. Global app, prod env in key
    sdk.provider.project_labels["p1"] = {"gapp__my-app_prod": "v-3"}
    # 2. Scoped app, dev env in key
    sdk.provider.project_labels["p2"] = {"gapp_owner-a_status_dev": "v-3"}

    sdk.set_owner(None)
    res = sdk.list_apps(wide=True)
    apps = res["apps"]

    by_name = {a["name"]: a for a in apps}

    assert by_name["my-app"]["env"] == "prod"
    assert by_name["my-app"]["owner"] == "global"
    assert by_name["my-app"]["contract_major"] == 3
    assert by_name["my-app"]["is_legacy"] is False

    assert by_name["status"]["env"] == "dev"
    assert by_name["status"]["owner"] == "owner-a"
    assert by_name["status"]["contract_major"] == 3


def test_parsing_forward_compatibility(sdk):
    """Verify parser tolerates a label value stamped by a newer gapp build."""
    # A future v-9 build wrote this; we should still parse it (read ops are not gated).
    sdk.provider.project_labels["p1"] = {"gapp__my-app_prod": "v-9"}

    res = sdk.list_apps(wide=True)
    app = res["apps"][0]

    assert app["name"] == "my-app"
    assert app["env"] == "prod"
    assert app["contract_major"] == 9


def test_parsing_with_hyphens(sdk):
    """Verify that hyphens in solution and owner names are correctly handled."""
    sdk.provider.project_labels["p1"] = {"gapp_owner-a_multi-word-app": "v-3"}

    sdk.set_owner("owner-a")
    res = sdk.list_apps()
    app = res["apps"][0]

    assert app["name"] == "multi-word-app"
    assert app["owner"] == "owner-a"
    assert app["env"] == "default"


def test_parsing_legacy_label(sdk):
    """Legacy `gapp-<name>=<env>` labels are still parsed for read ops."""
    sdk.provider.project_labels["p1"] = {"gapp-old-app": "default"}

    sdk.set_owner(None)
    res = sdk.list_apps(wide=True)
    app = res["apps"][0]

    assert app["name"] == "old-app"
    assert app["is_legacy"] is True
    assert app["contract_major"] is None
    assert app["env"] == "default"


def test_parsing_skips_role_labels(sdk):
    """Role labels (gapp-env*) are not surfaced as apps in list_apps."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env_owner-a": "prod",
        "gapp_owner-a_my-app": "v-3",
    }

    sdk.set_owner("owner-a")
    res = sdk.list_apps()

    assert len(res["apps"]) == 1
    assert res["apps"][0]["name"] == "my-app"
