"""Tests for finding the project that hosts a deployment.

Discovery searches the projects marked gapp=hosted for the deployment's
bucket (labeled gapp-deployment=<id>); the project's gapp-env comes from the
same project listing. An explicit project is taken as given, with no search.
Old per-solution project labels (gapp__<name>, gapp_<owner>_<name>) are
never read.
"""

import pytest
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_resolve_single_match(sdk):
    """One hosted project with the deployment's bucket → resolves to it."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")

    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-1"
    assert res["env"] == "prod"


def test_resolve_zero_matches_raises(sdk):
    """No hosted project has the deployment → error naming the hosted search."""
    sdk.set_owner(None)
    with pytest.raises(RuntimeError, match="No deployment 'my-app' found in any project marked gapp=hosted"):
        sdk.resolve_project_for_solution("my-app")


def test_a_project_not_marked_hosted_is_not_searched(sdk):
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", hosted=False)
    with pytest.raises(RuntimeError, match="No deployment 'my-app'"):
        sdk.resolve_project_for_solution("my-app")


def test_old_project_labels_are_never_read(sdk):
    """Clean break: a gapp__<name> project label alone finds nothing."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {"gapp": "hosted", "gapp__my-app": "v-3"}
    with pytest.raises(RuntimeError, match="No deployment 'my-app'"):
        sdk.resolve_project_for_solution("my-app")


def test_resolve_multi_match_no_env_raises(sdk):
    """Multiple projects, no --env → ambiguous, list candidates."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")
    sdk.provider.host("proj-2", "my-app", env="dev")

    with pytest.raises(RuntimeError, match="Deployment 'my-app' exists on multiple projects") as exc:
        sdk.resolve_project_for_solution("my-app")
    assert "proj-1 (env=prod)" in str(exc.value) and "proj-2 (env=dev)" in str(exc.value)


def test_resolve_multi_match_env_narrows_to_one(sdk):
    """Multiple projects, --env narrows to exactly one → resolved."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")
    sdk.provider.host("proj-2", "my-app", env="dev")

    res = sdk.resolve_project_for_solution("my-app", env="prod")
    assert res["project_id"] == "proj-1"


def test_resolve_multi_match_env_zero_after_filter(sdk):
    """--env that matches none of the candidates → error listing what we DO have."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")
    sdk.provider.host("proj-2", "my-app", env="dev")

    with pytest.raises(RuntimeError, match="not found in env='staging'"):
        sdk.resolve_project_for_solution("my-app", env="staging")


def test_resolve_multi_match_same_env_corruption(sdk):
    """Multiple projects in the same named env → corruption refusal naming the bucket fix."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")
    sdk.provider.host("proj-2", "my-app", env="prod")

    with pytest.raises(RuntimeError, match="corruption") as exc:
        sdk.resolve_project_for_solution("my-app", env="prod")
    assert "gcloud storage buckets update gs://gapp-my-app-<pid> --remove-labels=gapp-deployment" in str(exc.value)


def test_first_only_stops_at_the_first_match(sdk):
    """Status asks with first_only: an ambiguous name returns a match instead of refusing."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")
    sdk.provider.host("proj-2", "my-app", env="dev")
    res = sdk.resolve_project_for_solution("my-app", first_only=True)
    assert res["project_id"] in ("proj-1", "proj-2")
    res = sdk.resolve_project_for_solution("my-app", env="dev", first_only=True)
    assert res["project_id"] == "proj-2"


def test_resolve_owner_namespacing(sdk):
    """alice's deployment and bob's deployment with the same name don't collide."""
    sdk.provider.host("proj-alice", "my-app", owner="alice", env="prod")
    sdk.provider.host("proj-bob", "my-app", owner="bob", env="dev")

    sdk.set_owner("alice")
    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-alice"

    sdk.set_owner("bob")
    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-bob"


def test_resolve_explicit_project_bypasses_discovery(sdk, monkeypatch):
    """--project takes the project as given: no hosted-project search."""
    sdk.set_owner(None)
    sdk.provider.project_labels["proj-1"] = {"gapp-env": "prod"}
    monkeypatch.setattr(sdk.provider, "list_projects", lambda *a, **k: pytest.fail("no search with --project"))
    monkeypatch.setattr(sdk.provider, "list_bucket_labels", lambda *a, **k: pytest.fail("no search with --project"))

    res = sdk.resolve_project_for_solution("my-app", project="proj-1")
    assert res["project_id"] == "proj-1"
    assert res["env"] == "prod"


def test_resolve_explicit_project_env_mismatch_refuses(sdk):
    """--project with --env that doesn't match P's gapp-env → refuse."""
    sdk.set_owner(None)
    sdk.provider.host("proj-1", "my-app", env="prod")

    with pytest.raises(RuntimeError, match="bound to env='prod'"):
        sdk.resolve_project_for_solution("my-app", project="proj-1", env="dev")


def test_resolve_discovery_off_requires_project(sdk):
    """If discovery is off, the resolver requires --project."""
    sdk.set_discovery("off")
    sdk.provider.host("proj-1", "my-app")

    with pytest.raises(RuntimeError, match="Discovery is OFF"):
        sdk.resolve_project_for_solution("my-app")
