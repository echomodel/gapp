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
    
    # Extract solutions from results
    solutions = []
    for p in res["projects"]:
        solutions.extend(p["solutions"])
    
    # Sort for consistent assertion
    solutions.sort(key=lambda s: s["name"])
    
    assert solutions[0]["name"] == "my-app"
    assert solutions[0]["instance"] == "v-2_env-prod"
    
    assert solutions[1]["name"] == "status"
    assert solutions[1]["label"] == "gapp_owner-a_status"


def test_parsing_forward_compatibility(sdk):
    """Verify parsing ignores future segments in label values."""
    # Value has future segments like region and team
    sdk.provider.project_labels["p1"] = {"gapp__my-app": "v-2_env-prod_region-us-central1_team-alpha"}
    
    res = sdk.list(wide=True)
    sol = res["projects"][0]["solutions"][0]
    
    assert sol["name"] == "my-app"
    assert sol["instance"] == "v-2_env-prod_region-us-central1_team-alpha"


def test_parsing_with_hyphens(sdk):
    """Verify that hyphens in labels are correctly handled."""
    sdk.provider.project_labels["p1"] = {"gapp_owner-a_multi-word-app": "v-2"}
    
    sdk.set_owner("owner-a")
    res = sdk.list()
    sol = res["projects"][0]["solutions"][0]
    
    assert sol["name"] == "multi-word-app"
    assert sol["label"] == "gapp_owner-a_multi-word-app"
