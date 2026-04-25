"""Tests for gapp.sdk.core — GCP-based discovery."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_list_solutions_from_labels(sdk):
    """Verify sdk.list() extracts all gapp solutions from GCP labels."""
    sdk.provider.project_labels["proj-123"] = {
        "gapp__api": "v-2",
        "gapp__worker": "v-2",
        "gapp_owner-a_status": "v-2_env-prod"
    }
    
    res = sdk.list(wide=True)
    apps = res["apps"]
    
    assert len(apps) == 3
    assert apps[0]["name"] == "api"
    assert apps[0]["owner"] == "global"
    
    status_app = next(a for a in apps if a["name"] == "status")
    assert status_app["project"] == "proj-123"
    assert status_app["owner"] == "owner-a"
    assert status_app["env"] == "prod"


def test_list_solutions_with_limit_reached(sdk):
    """Verify limit_reached warning is correctly reported."""
    # Add 3 projects
    sdk.provider.project_labels["p1"] = {"gapp__app1": "v-2"}
    sdk.provider.project_labels["p2"] = {"gapp__app2": "v-2"}
    sdk.provider.project_labels["p3"] = {"gapp__app3": "v-2"}
    
    # List with limit of 2
    res = sdk.list(project_limit=2, wide=True)
    assert any("limit reached" in w for w in res["warnings"])
