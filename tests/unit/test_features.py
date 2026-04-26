"""Tests for gapp.sdk.core — high-level feature logic."""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_discovery_policy_enforcement(tmp_path, monkeypatch, sdk):
    """When discovery is OFF, the resolver refuses without --project."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: project-status")
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)

    sdk.provider.project_labels["proj-123"] = {"gapp__project-status": "v-3"}

    sdk.set_discovery("off")
    with pytest.raises(RuntimeError, match="Discovery is OFF"):
        sdk.resolve_project_for_solution("project-status")

    sdk.set_discovery("on")
    res = sdk.resolve_project_for_solution("project-status")
    assert res["project_id"] == "proj-123"
