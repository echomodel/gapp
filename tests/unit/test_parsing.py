"""Tests for env-blind solution label parsing.

In v-3 the env is a project property (gapp-env label), not part of the
solution label key. The parser reads owner and solution name from the
key and expects env from the project's gapp-env value (passed in).
"""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_parsing_owned_and_global(sdk):
    """Owned solution label and global solution label both parse correctly."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }
    sdk.provider.project_labels["p2"] = {
        "gapp-env": "dev",
        "gapp_owner-a_status": "v-3",
    }

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


def test_parsing_undefined_env(sdk):
    """A project with no gapp-env shows env=None for its solutions."""
    sdk.provider.project_labels["p1"] = {"gapp__my-app": "v-3"}

    sdk.set_owner(None)
    res = sdk.list_apps(wide=True)
    app = res["apps"][0]

    assert app["name"] == "my-app"
    assert app["env"] is None
    assert app["contract_major"] == 3


def test_parsing_forward_compatibility(sdk):
    """Parser tolerates a label value stamped by a newer gapp build."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-9",
    }

    res = sdk.list_apps(wide=True)
    app = res["apps"][0]

    assert app["name"] == "my-app"
    assert app["env"] == "prod"
    assert app["contract_major"] == 9


def test_parsing_with_hyphens(sdk):
    """Hyphens in solution and owner names are correctly handled."""
    sdk.provider.project_labels["p1"] = {
        "gapp_owner-a_multi-word-app": "v-3",
    }

    sdk.set_owner("owner-a")
    res = sdk.list_apps()
    app = res["apps"][0]

    assert app["name"] == "multi-word-app"
    assert app["owner"] == "owner-a"
    assert app["env"] is None


def test_parsing_legacy_label(sdk):
    """Legacy v-2 `gapp-<name>=<env>` labels are still parsed for read ops."""
    sdk.provider.project_labels["p1"] = {"gapp-old-app": "default"}

    sdk.set_owner(None)
    res = sdk.list_apps(wide=True)
    app = res["apps"][0]

    assert app["name"] == "old-app"
    assert app["is_legacy"] is True
    assert app["contract_major"] is None
    assert app["env"] == "default"


def test_parsing_skips_project_env_label(sdk):
    """gapp-env (and v-2 gapp-env_<owner>) are not surfaced as apps."""
    sdk.provider.project_labels["p1"] = {
        "gapp-env": "prod",
        "gapp-env_owner-a": "prod",  # legacy v-2 role label
        "gapp_owner-a_my-app": "v-3",
    }

    sdk.set_owner("owner-a")
    res = sdk.list_apps()

    assert len(res["apps"]) == 1
    assert res["apps"][0]["name"] == "my-app"
    assert res["apps"][0]["env"] == "prod"
