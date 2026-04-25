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
        "gapp__api": "default",
        "gapp__worker": "default",
        "gapp_owner-a_status": "prod"
    }
    
    results_data = sdk.list(wide=True)
    solutions = []
    for p in results_data["projects"]:
        solutions.extend(p["solutions"])
    
    # Sort for consistent assertion
    solutions.sort(key=lambda s: s["name"])
    
    assert len(solutions) == 3
    assert solutions[0]["name"] == "api"
    assert solutions[1]["name"] == "status"
    assert solutions[2]["name"] == "worker"


def test_list_solutions_with_limit_reached(sdk):
    """Verify limit_reached flag is correctly reported."""
    # Add 3 projects
    sdk.provider.project_labels["p1"] = {"gapp__app1": "default"}
    sdk.provider.project_labels["p2"] = {"gapp__app2": "default"}
    sdk.provider.project_labels["p3"] = {"gapp__app3": "default"}
    
    # List with limit of 2
    res = sdk.list(project_limit=2, wide=True)
    assert res["limit_reached"] is True
