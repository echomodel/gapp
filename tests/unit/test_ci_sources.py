"""`gapp ci sources add|remove|list` manages the fleet repo variable
GAPP_ALLOWED_SOURCES, the owners whose repos may be deployed from the fleet.
Needs `gh` with write access; every refusal is an outcome in GitHub's words."""

import pytest

from gapp.admin.sdk import ci as ci_mod

FLEET = "acme/fleet"
VAR = (FLEET, "GAPP_ALLOWED_SOURCES")


@pytest.fixture
def gh(fleet_repo, fake_gh):
    return fake_gh


def test_list_without_the_variable_is_empty(gh):
    res = ci_mod.manage_sources("list")
    assert res["ok"] is True and res["owners"] == [] and res["fleet_repo"] == FLEET


def test_add_creates_the_variable_then_appends(gh):
    res = ci_mod.manage_sources("add", "me")
    assert res["ok"] is True and res["changed"] is True and res["owners"] == ["me"]
    assert gh.variables[VAR] == "me"
    res = ci_mod.manage_sources("add", "other-org")
    assert res["owners"] == ["me", "other-org"] and gh.variables[VAR] == "me,other-org"


def test_add_of_a_present_owner_and_remove_of_an_absent_one_change_nothing(gh):
    gh.variables[VAR] = "me, other-org"
    res = ci_mod.manage_sources("add", "ME")
    assert res["ok"] is True and res["changed"] is False and gh.variables[VAR] == "me, other-org"
    res = ci_mod.manage_sources("remove", "nobody")
    assert res["ok"] is True and res["changed"] is False


def test_remove_rewrites_the_list_and_deletes_it_when_empty(gh):
    gh.variables[VAR] = "me,other-org"
    res = ci_mod.manage_sources("remove", "other-org")
    assert res["ok"] is True and res["owners"] == ["me"] and gh.variables[VAR] == "me"
    res = ci_mod.manage_sources("remove", "me")
    assert res["owners"] == [] and VAR not in gh.variables


def test_sources_without_an_owner_a_bad_action_gh_or_fleet_repo_stop(gh, monkeypatch):
    import shutil
    from gapp.admin.sdk import config as config_mod
    assert ci_mod.manage_sources("add", "")["next_step"]["action"] == "pass the owner"
    assert ci_mod.manage_sources("frob", "me")["next_step"]["action"] == "choose an action"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert ci_mod.manage_sources("list")["next_step"]["action"] == "install gh"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    config_mod.set_fleet_repo(None)
    assert ci_mod.manage_sources("list")["next_step"]["hint"] == ci_mod.NO_FLEET_REPO_MESSAGE


def test_sources_report_a_refused_write_with_the_gh_command(gh):
    gh.api_writes = False
    res = ci_mod.manage_sources("add", "me")
    assert res["ok"] is False and res["next_step"]["action"] == "sign in to gh with write access"
    assert "GitHub returned 403: request blocked: GitHub API writes are not allowed from this session" in res["next_step"]["hint"]
    assert "gh variable set GAPP_ALLOWED_SOURCES --repo acme/fleet --body me" in res["next_step"]["hint"]


def test_sources_report_an_unreadable_variable_as_could_not_check(gh):
    gh.actions_api = False
    res = ci_mod.manage_sources("list")
    assert res["ok"] is False and res["owners"] is None
    assert res["next_step"]["hint"].startswith(
        "Couldn't check GAPP_ALLOWED_SOURCES in acme/fleet: GitHub returned 403: request blocked: Actions variables "
        "and secrets are not readable from this session. Request: GET repos/acme/fleet/actions/variables/GAPP_ALLOWED_SOURCES")
