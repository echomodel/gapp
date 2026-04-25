"""Tests for underscore-delimited label parsing and forward compatibility."""

import pytest
from gapp.admin.sdk.deployments import list_deployments
from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.context import set_owner


def test_parsing_new_underscore_labels():
    """Verify list_deployments correctly parses underscore-delimited keys/values."""
    provider = get_provider()
    # 1. Global app
    provider.project_labels["p1"] = {"gapp__my--app": "v-2_env-prod"}
    # 2. Scoped app
    provider.project_labels["p2"] = {"gapp_owner--a_status": "v-2_env-dev"}
    
    set_owner(None)
    res = list_deployments(wide=True)
    
    # Extract solutions from results
    solutions = []
    for p in res["projects"]:
        solutions.extend(p["solutions"])
    
    # Sort for consistent assertion
    solutions.sort(key=lambda s: s["name"])
    
    assert solutions[0]["name"] == "my-app"
    assert solutions[0]["instance"] == "v-2_env-prod"
    
    assert solutions[1]["name"] == "status"
    assert solutions[1]["label"] == "gapp_owner--a_status"


def test_parsing_forward_compatibility():
    """Verify parsing ignores future segments in label values."""
    provider = get_provider()
    # Value has future segments like region and team
    provider.project_labels["p1"] = {"gapp__my--app": "v-2_env-prod_region-us-central1_team-alpha"}
    
    res = list_deployments(wide=True)
    sol = res["projects"][0]["solutions"][0]
    
    assert sol["name"] == "my-app"
    assert sol["instance"] == "v-2_env-prod_region-us-central1_team-alpha"


def test_parsing_protected_hyphens():
    """Verify that doubled hyphens in labels are correctly reversed to single hyphens."""
    provider = get_provider()
    provider.project_labels["p1"] = {"gapp_owner--a_multi--word--app": "v-2"}
    
    # We want to see 'owner-a' and 'multi-word-app' correctly handled.
    set_owner("owner-a")
    res = list_deployments()
    sol = res["projects"][0]["solutions"][0]
    
    assert sol["name"] == "multi-word-app"
    assert sol["label"] == "gapp_owner--a_multi--word--app"
