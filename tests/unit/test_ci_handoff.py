"""`gapp ci deploy` and `gapp ci status --live` refuse, before a run is
started, a definition the fleet repo's workflows cannot run: one on a gapp
older than the workflows' minimum, or one on a gapp that needs newer
workflows (ci.RUNNER_HANDOFF_CHANGES)."""

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import entryfile

FLEET = "acme/fleet"


def _on(fake_gh, version):
    whole = entryfile.parse(fake_gh.files[(FLEET, entryfile.FILE_NAME)])
    whole["deployments"]["my-app"]["gapp"] = version
    fake_gh.files[(FLEET, entryfile.FILE_NAME)] = entryfile.dump(whole)


def _workflows_of(fake_gh, ref):
    for path, content in ci_mod._workflow_files("example-org/gapp", ref).items():
        fake_gh.files[(FLEET, path)] = content


def _start(command):
    return ci_mod.deploy("my-app") if command == "deploy" else ci_mod.status("my-app", live=True)


@pytest.mark.parametrize("command", ["deploy", "status"])
def test_a_definition_older_than_the_workflows_minimum_is_refused_with_upgrade(ci, fake_gh, command):
    _workflows_of(fake_gh, "v4.0.0rc15")
    _on(fake_gh, "4.0.0rc14")
    res = _start(command)
    assert res["refused"] is True and res["dispatched"] is False and ci.dispatched == []
    assert res["next_step"]["action"] == "upgrade"
    assert res["next_step"]["hint"] == (
        "'my-app' is defined with gapp 4.0.0rc14, and the workflows in acme/fleet (gapp 4.0.0rc15's) need "
        "4.0.0rc15 or later. Upgrade it first: gapp upgrade --deployment my-app, then "
        "gapp ci deploy --deployment my-app --upgrade")


@pytest.mark.parametrize("command", ["deploy", "status"])
def test_workflows_older_than_a_hand_off_change_the_definition_needs_are_refused_with_ci_init(ci, fake_gh, command):
    _workflows_of(fake_gh, "v4.0.0rc14")
    _on(fake_gh, "4.0.0rc15")
    res = _start(command)
    assert res["refused"] is True and res["dispatched"] is False and ci.dispatched == []
    assert res["next_step"]["action"] == "ci init"
    assert "the workflows in acme/fleet are gapp 4.0.0rc14's, which cannot run it: gapp 4.0.0rc15 changed" in (
        res["next_step"]["hint"])


@pytest.mark.parametrize("workflows, definition", [
    ("v4.0.0rc15", "4.0.0rc15"),
    ("v4.0.0", "4.0.0rc15"),          # newer workflows, same hand-off
    ("v4.0.0rc15", "4.0.0"),          # newer definition, same hand-off
])
def test_matching_hand_offs_are_started(ci, fake_gh, workflows, definition):
    _workflows_of(fake_gh, workflows)
    _on(fake_gh, definition)
    assert ci_mod.deploy("my-app")["dispatched"] is True


def test_workflows_pinned_to_a_commit_are_left_to_the_run_to_check(ci, fake_gh):
    _workflows_of(fake_gh, "c0ffee" * 6 + "c0ff")   # a commit, not a release tag
    _on(fake_gh, "4.0.0rc14")
    assert ci_mod.deploy("my-app")["dispatched"] is True


def test_the_installed_workflows_carry_the_minimum():
    for content in ci_mod._workflow_files("example-org/gapp", "v4.0.0").values():
        assert "__GAPP_RUNNER_MIN__" not in content
        assert f'minimum="{ci_mod.RUNNER_MIN_GAPP}"' in content
