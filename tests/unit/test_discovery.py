"""Tests for gapp project discovery fallbacks and role roles."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_discovery_fallback_to_global_role(sdk):
    """Verify setup finds project via global gapp-env label."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-default"] = {"gapp-env": "default"}
    
    # We use a solution name that has no direct label
    ctx = sdk.resolve_full_context(solution="new-app", env="default")
    assert ctx["project_id"] == "proj-default"


def test_discovery_fallback_to_scoped_role(sdk):
    """Verify setup finds project via owner-scoped gapp-env label."""
    sdk.set_owner("kris")
    # Global role exists but should be ignored if scoped role matches
    sdk.provider.project_labels["proj-global"] = {"gapp-env": "default"}
    sdk.provider.project_labels["proj-kris"] = {"gapp-env_kris": "default"}
    
    ctx = sdk.resolve_full_context(solution="new-app", env="default")
    assert ctx["project_id"] == "proj-kris"


def test_discovery_env_specific_role(sdk):
    """Verify setup finds the right project for the requested environment."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-dev"] = {"gapp-env": "dev"}
    sdk.provider.project_labels["proj-prod"] = {"gapp-env": "prod"}
    
    ctx = sdk.resolve_full_context(solution="app", env="dev")
    assert ctx["project_id"] == "proj-dev"
    
    ctx = sdk.resolve_full_context(solution="app", env="prod")
    assert ctx["project_id"] == "proj-prod"


def test_discovery_precedence_app_over_role(sdk):
    """Verify app-specific label takes precedence over project role label."""
    sdk.set_owner(None)
    # Role designates proj-1, but app specifically claims proj-2
    sdk.provider.project_labels["proj-1"] = {"gapp-env": "default"}
    sdk.provider.project_labels["proj-2"] = {"gapp__my-app": "v-2"}
    
    ctx = sdk.resolve_full_context(solution="my-app", env="default")
    assert ctx["project_id"] == "proj-2"
