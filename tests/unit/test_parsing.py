"""Tests for reading deployments from their bucket labels.

`gapp list` asks every project marked gapp=hosted for its deployment
buckets and reads gapp-deployment, gapp-version and gapp-owner from each;
env comes from the project's gapp-env label. Old per-solution project
labels are never read.
"""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_deployments_on_several_projects(sdk):
    """Deployments on different hosted projects all list, each with its env."""
    sdk.provider.host("p1", "my-app", env="prod")
    sdk.provider.host("p2", "status", env="dev")

    by_name = {a["name"]: a for a in sdk.list_apps()["apps"]}

    assert by_name["my-app"]["env"] == "prod"
    assert "owner" not in by_name["my-app"]
    assert by_name["my-app"]["contract_major"] == 4
    assert by_name["my-app"]["is_legacy"] is False

    assert by_name["status"]["env"] == "dev"
    assert by_name["status"]["contract_major"] == 4


def test_undefined_env(sdk):
    """A project with no gapp-env shows env=None for its deployments."""
    sdk.provider.host("p1", "my-app")

    app = sdk.list_apps()["apps"][0]

    assert app["name"] == "my-app"
    assert app["env"] is None
    assert app["contract_major"] == 4


def test_forward_compatibility(sdk):
    """A deployment labeled by a newer gapp build still lists, with its version."""
    sdk.provider.host("p1", "my-app", env="prod", version="9")

    app = sdk.list_apps()["apps"][0]

    assert app["name"] == "my-app"
    assert app["env"] == "prod"
    assert app["contract_major"] == 9


def test_hyphens_in_names(sdk):
    """Hyphens in deployment names are kept."""
    sdk.provider.host("p1", "multi-word-app")

    app = sdk.list_apps()["apps"][0]

    assert app["name"] == "multi-word-app"
    assert app["env"] is None


def test_old_project_labels_and_unlabeled_buckets_are_not_apps(sdk):
    """Clean break: gapp__<name>, gapp_<owner>_<name> and v-2 gapp-<name> project labels
    list nothing, and a bucket without gapp-deployment is not a deployment."""
    sdk.provider.project_labels["p1"] = {
        "gapp": "hosted", "gapp-env": "prod", "gapp__old-app": "v-3",
        "gapp_owner-a_other": "v-3", "gapp-legacy-app": "default",
    }
    sdk.provider.buckets["some-other-bucket"] = {"project": "p1", "labels": {"team": "x"}}

    assert sdk.list_apps()["apps"] == []


def test_projects_not_marked_hosted_are_not_listed(sdk):
    sdk.provider.host("p1", "my-app", hosted=False)
    res = sdk.list_apps()
    assert res["apps"] == [] and res["metadata"]["projects"]["count"] == 0
