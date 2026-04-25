"""Tests for underscore-delimited label parsing and forward compatibility."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_parsing_new_underscore_labels(sdk):
    """Verify sdk.list() correctly parses underscore-delimited keys/values."""
    # 1. Global app
    sdk.provider.project_labels["p1"] = {"gapp__my-app": "v-2_env-prod"}
    # 2. Scoped app
    sdk.provider.project_labels["p2"] = {"gapp_owner-a_status": "v-2_env-dev"}
    
    sdk.set_owner(None)
    res = sdk.list(wide=True)
    apps = res["apps"]
    
    assert apps[0]["name"] == "my-app"
    assert apps[0]["env"] == "prod"
    assert apps[0]["owner"] == "global"
    
    assert apps[1]["name"] == "status"
    assert apps[1]["env"] == "dev"
    assert apps[1]["owner"] == "owner-a"


def test_parsing_forward_compatibility(sdk):
    """Verify parsing ignores future segments in label values."""
    # Value has future segments like region and team
    sdk.provider.project_labels["p1"] = {"gapp__my-app": "v-2_env-prod_region-us-central1_team-alpha"}
    
    res = sdk.list(wide=True)
    app = res["apps"][0]
    
    assert app["name"] == "my-app"
    assert app["version"] == "v-2"
    assert app["env"] == "prod"


def test_parsing_with_hyphens(sdk):
    """Verify that hyphens in labels are correctly handled."""
    sdk.provider.project_labels["p1"] = {"gapp_owner-a_multi-word-app": "v-2"}
    
    sdk.set_owner("owner-a")
    res = sdk.list()
    app = res["apps"][0]
    
    assert app["name"] == "multi-word-app"
    assert app["owner"] == "owner-a"
