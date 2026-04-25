"""Tests for gapp.sdk.solutions — GCP-based discovery."""

import pytest
from gapp.admin.sdk.solutions import list_solutions
from gapp.admin.sdk.cloud import get_provider


def test_list_solutions_from_labels():
    """Verify list_solutions extracts all gapp solutions from GCP labels."""
    provider = get_provider()
    # Use new underscore format
    provider.project_labels["proj-123"] = {
        "gapp__api": "default",
        "gapp__worker": "default",
        "gapp_owner--a_status": "prod"
    }
    
    results_data = list_solutions(wide=True)
    solutions = results_data["solutions"]
    
    # Sort for consistent assertion
    solutions.sort(key=lambda s: s["name"])
    
    assert len(solutions) == 3
    assert solutions[0]["name"] == "api"
    assert solutions[1]["name"] == "status"
    assert solutions[2]["name"] == "worker"
    
    # Verify metadata
    status_app = next(s for s in solutions if s["name"] == "status")
    assert status_app["project_id"] == "proj-123"
    assert status_app["label"] == "gapp_owner--a_status"


def test_list_solutions_with_limit_reached():
    """Verify limit_reached flag is correctly reported."""
    provider = get_provider()
    # Add 3 projects
    provider.project_labels["p1"] = {"gapp__app1": "default"}
    provider.project_labels["p2"] = {"gapp__app2": "default"}
    provider.project_labels["p3"] = {"gapp__app3": "default"}
    
    # List with limit of 2 (mocked)
    # The Dummy provider returns all, but we tell list_solutions the limit is 2
    res = list_solutions(project_limit=2, wide=True)
    # The dummy provider list_projects currently ignores limit, but res["limit_reached"] 
    # is calculated by len(projects_data) >= project_limit.
    # In Dummy, it returns 3, so 3 >= 2 is True.
    assert res["limit_reached"] is True
