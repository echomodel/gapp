"""Tests for finding the project that hosts a deployment.

Discovery searches the projects marked gapp=hosted for the deployment's
bucket (labeled gapp-deployment=<id>), stopping at the second match. An
explicit project is taken as given, with no search and no label read. Old
per-solution project labels (gapp__<name>, gapp_<owner>_<name>) and the
removed gapp-env label are never read.
"""

import pytest
from gapp.admin.sdk.core import AmbiguousDeployment, GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

AMBIGUOUS = "'my-app' exists on more than one project. Pass --project to choose one; `gapp list` shows where each is."


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def test_resolve_single_match(sdk):
    """One hosted project with the deployment's bucket → resolves to it."""
    sdk.provider.host("proj-1", "my-app")

    res = sdk.resolve_project_for_solution("my-app")
    assert res["project_id"] == "proj-1"
    assert "env" not in res


def test_resolve_zero_matches_raises(sdk):
    """No hosted project has the deployment → error naming the hosted search."""
    with pytest.raises(RuntimeError, match="No deployment 'my-app' found in any project marked gapp=hosted"):
        sdk.resolve_project_for_solution("my-app")


def test_a_project_not_marked_hosted_is_not_searched(sdk):
    sdk.provider.host("proj-1", "my-app", hosted=False)
    with pytest.raises(RuntimeError, match="No deployment 'my-app'"):
        sdk.resolve_project_for_solution("my-app")


def test_old_project_labels_are_never_read(sdk):
    """Clean break: a gapp__<name> or gapp-env project label alone finds nothing."""
    sdk.provider.project_labels["proj-1"] = {"gapp": "hosted", "gapp__my-app": "v-3", "gapp-env": "prod"}
    with pytest.raises(RuntimeError, match="No deployment 'my-app'"):
        sdk.resolve_project_for_solution("my-app")


def test_a_name_on_two_projects_is_refused_with_the_approved_message(sdk):
    sdk.provider.host("proj-1", "my-app")
    sdk.provider.host("proj-2", "my-app")
    with pytest.raises(AmbiguousDeployment) as exc:
        sdk.resolve_project_for_solution("my-app")
    assert str(exc.value) == AMBIGUOUS


def test_resolve_explicit_project_bypasses_discovery(sdk, monkeypatch):
    """--project takes the project as given: no hosted-project search, no label read."""
    monkeypatch.setattr(sdk.provider, "list_projects", lambda *a, **k: pytest.fail("no search with --project"))
    monkeypatch.setattr(sdk.provider, "list_bucket_labels", lambda *a, **k: pytest.fail("no search with --project"))
    monkeypatch.setattr(sdk.provider, "get_project_labels", lambda *a, **k: pytest.fail("no label read"))

    res = sdk.resolve_project_for_solution("my-app", project="proj-1")
    assert res["project_id"] == "proj-1"


def test_resolve_discovery_off_requires_project(sdk):
    """If discovery is off, the resolver requires --project."""
    sdk.set_discovery("off")
    sdk.provider.host("proj-1", "my-app")

    with pytest.raises(RuntimeError, match="Discovery is OFF"):
        sdk.resolve_project_for_solution("my-app")


def test_resolvers_take_no_env():
    import inspect
    for fn in (GappSDK.resolve_project_for_solution, GappSDK.resolve_solution_with_project,
               GappSDK.discover_projects_for_solution, GappSDK.setup, GappSDK.deploy, GappSDK.status):
        assert "env" not in inspect.signature(fn).parameters, fn.__name__
