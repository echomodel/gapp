"""Tests for listing deployments (list_apps) across the hosted projects."""

import time

import pytest
from gapp.admin.sdk import core as core_mod
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_list_apps_from_bucket_labels(sdk):
    """list_apps returns every deployment on every hosted project."""
    sdk.provider.host("proj-123", "api", env="prod")
    sdk.provider.host("proj-123", "worker", env="prod")
    sdk.provider.host("proj-123", "status", owner="owner-a", env="prod")

    apps = sdk.list_apps(all_owners=True)["apps"]

    assert len(apps) == 3
    by_name = {a["name"]: a for a in apps}

    assert by_name["api"]["owner"] == "global"
    assert by_name["api"]["env"] == "prod"

    assert by_name["status"]["project"] == "proj-123"
    assert by_name["status"]["owner"] == "owner-a"
    assert by_name["status"]["env"] == "prod"


def test_list_apps_owner_scoped(sdk):
    """Default list filters to the active owner only."""
    sdk.provider.host("p1", "my-app", owner="alice")
    sdk.provider.host("p1", "other-app", owner="bob")

    sdk.set_owner("alice")
    apps = sdk.list_apps()["apps"]

    assert len(apps) == 1
    assert apps[0]["name"] == "my-app"
    assert apps[0]["owner"] == "alice"


def test_list_apps_without_an_owner_shows_global_deployments(sdk):
    sdk.provider.host("p1", "mine")
    sdk.provider.host("p1", "theirs", owner="bob")
    sdk.set_owner(None)
    assert [a["name"] for a in sdk.list_apps()["apps"]] == ["mine"]


def test_list_apps_with_limit_reached(sdk):
    sdk.provider.host("p1", "app1")
    sdk.provider.host("p2", "app2")
    sdk.provider.host("p3", "app3")

    res = sdk.list_apps(project_limit=2, all_owners=True)
    assert any("limit reached" in w for w in res["warnings"])
    assert res["metadata"]["projects"]["count"] == 2


def test_list_apps_flags_same_env_duplicates(sdk):
    """Two projects hosting the same deployment under the same named env are flagged."""
    sdk.provider.host("p1", "my-app", owner="alice", env="prod")
    sdk.provider.host("p2", "my-app", owner="alice", env="prod")

    sdk.set_owner("alice")
    res = sdk.list_apps()

    assert len(res["apps"]) == 2
    assert all(a["duplicate"] for a in res["apps"])
    assert any("duplicate" in w.lower() for w in res["warnings"])


def test_list_apps_undefined_env_not_flagged_as_duplicate(sdk):
    """Multiple undefined-env projects with the same deployment are not corruption."""
    sdk.provider.host("p1", "my-app", owner="alice")
    sdk.provider.host("p2", "my-app", owner="alice")

    sdk.set_owner("alice")
    res = sdk.list_apps()

    assert len(res["apps"]) == 2
    assert not any(a["duplicate"] for a in res["apps"])


def test_list_apps_asks_the_hosted_projects_in_parallel(sdk, monkeypatch):
    """Each hosted project is asked for its buckets concurrently, not one after another."""
    for i in range(6):
        sdk.provider.host(f"p{i}", f"app{i}")
    original = sdk.provider.list_bucket_labels

    def slow(project_id):
        time.sleep(0.2)
        return original(project_id)

    monkeypatch.setattr(sdk.provider, "list_bucket_labels", slow)
    started = time.monotonic()
    assert len(sdk.list_apps(all_owners=True)["apps"]) == 6
    assert time.monotonic() - started < 0.2 * 6 / 2
    assert core_mod.SEARCH_WORKERS >= 6
