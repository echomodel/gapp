"""`gapp ci init` installs the deploy and status workflows in the fleet repo
and nothing else. It needs the fleet to be kept in a GitHub repo; where the
fleet lives is `gapp fleet use`'s to say."""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import entryfile

FLEET = "acme/fleet"
PATHS = [".github/workflows/gapp-deploy.yml", ".github/workflows/gapp-status.yml"]


@pytest.fixture
def bare(monkeypatch, fleet_repo, fake_gh):
    """The fleet repo holding its fleet file and no workflows yet."""
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    for path in PATHS:
        del fake_gh.files[(FLEET, path)]
    return fake_gh


def test_init_installs_the_two_workflows_and_nothing_else(bare):
    fleet_file = bare.files[(FLEET, entryfile.FILE_NAME)]
    res = ci_mod.init_workflows()
    assert res["ok"] is True and res["fleet_repo"] == FLEET
    assert res["files"] == {path: "created" for path in PATHS}
    assert sorted(path for _, path, _ in bare.puts) == PATHS
    assert bare.files[(FLEET, entryfile.FILE_NAME)] == fleet_file
    assert res["lines"] == ["Installed the deploy and status workflows in acme/fleet."]
    deploy = bare.files[(FLEET, PATHS[0])]
    assert "uses: example-org/gapp/.github/workflows/deploy.yml@v4.0.0" in deploy and "__GAPP" not in deploy


def test_init_names_allowing_an_owner_next_then_connecting_a_project(bare):
    assert ci_mod.init_workflows()["next_step"] == {"action": "ci sources add", "hint": "gapp ci sources add <owner>"}
    bare.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    assert ci_mod.init_workflows()["next_step"]["hint"] == "gapp ci projects connect <project>"


def test_init_again_writes_nothing_when_the_workflows_are_this_gapps(bare):
    ci_mod.init_workflows()
    bare.puts.clear()
    res = ci_mod.init_workflows()
    assert res["ok"] is True and set(res["files"].values()) == {"unchanged"} and bare.puts == []
    assert res["lines"] == ["The workflows in acme/fleet are this gapp's already. Nothing changed."]


def test_init_brings_older_workflows_up_to_this_gapps(bare, monkeypatch):
    ci_mod.init_workflows()
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.1")
    res = ci_mod.init_workflows()
    # Only the deploy workflow names a gapp release: the reusable workflow it calls.
    assert res["files"] == {PATHS[0]: "updated", PATHS[1]: "unchanged"}
    assert res["lines"] == ["Updated the workflows in acme/fleet to this gapp's."]
    assert "deploy.yml@v4.0.1" in bare.files[(FLEET, PATHS[0])]


def test_init_is_refused_when_the_fleet_is_a_file_on_this_machine(bare):
    config_mod.set_fleet_repo(None, "fleet use")
    res = ci_mod.init_workflows()
    assert res["ok"] is False and res["refused"] is True and res["next_step"]["action"] == "fleet use"
    assert res["next_step"]["hint"].startswith("Your fleet is kept in a file on this machine (")
    assert res["next_step"]["hint"].endswith("Move it to one: gapp fleet use <owner>/<repo>")
    assert bare.puts == []


def test_init_is_refused_when_the_fleet_repo_is_not_on_github(bare):
    config_mod.set_fleet_repo("git.example.com/team/fleet", "fleet use")
    res = ci_mod.init_workflows()
    assert res["refused"] is True
    assert "git.example.com/team/fleet, which is not a repo on GitHub" in res["next_step"]["hint"]
    assert bare.calls == []


def test_init_without_gh_is_refused(bare, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = ci_mod.init_workflows()
    assert res["refused"] is True and res["next_step"]["action"] == "install gh"


def test_a_refused_write_is_a_failure_in_githubs_words(bare):
    bare.api_writes = False
    res = ci_mod.init_workflows()
    assert res["ok"] is False and res["refused"] is False and res["next_step"]["action"] == "install the workflows"
    hint = res["next_step"]["hint"]
    assert "GitHub returned 403: request blocked: GitHub API writes are not allowed from this session" in hint
    assert hint.endswith("Then run it again: gapp ci init")


def test_variables_this_machine_cannot_read_are_reported_as_not_checked(bare):
    bare.actions_api = False
    res = ci_mod.init_workflows()
    assert res["ok"] is True and set(res["variables"].values()) == {None}
    assert res["variables_note"].startswith("not checked:")
