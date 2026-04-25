"""Tests for gapp.sdk.solutions — GCP-based discovery."""

import pytest
from gapp.admin.sdk.solutions import list_solutions
from gapp.admin.sdk.cloud import get_provider


def test_list_solutions_from_labels():
    """Verify list_solutions extracts all gapp solutions from GCP labels."""
    provider = get_provider()
    provider.project_labels["proj-123"] = {
        "gapp-api": "default",
        "gapp-worker": "default",
        "gapp-owner-a-status": "prod"
    }
    
    results_data = list_solutions()
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
    assert status_app["label"] == "gapp-owner-a-status"


def test_list_solutions_with_limit_reached():
    """Verify limit_reached flag is correctly reported."""
    provider = get_provider()
    # Add 3 projects
    provider.project_labels["p1"] = {"gapp-app1": "default"}
    provider.project_labels["p2"] = {"gapp-app2": "default"}
    provider.project_labels["p3"] = {"gapp-app3": "default"}
    
    # List with limit of 2
    res = list_solutions(project_limit=2)
    assert res["total_projects"] == 2
    assert res["limit_reached"] is True
