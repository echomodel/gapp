"""`gapp ci sources add|remove|list` manages the fleet repo variable
GAPP_ALLOWED_SOURCES, the owners whose repos runners may deploy. Needs `gh`
with write access; every refusal is an outcome in GitHub's words."""

import pytest

from gapp.admin.sdk import ci as ci_mod

FLEET = "acme/fleet"
VAR = (FLEET, "GAPP_ALLOWED_SOURCES")


@pytest.fixture
def gh(fleet_repo, fake_gh):
    return fake_gh


def test_list_without_the_variable_is_empty_and_names_add(gh):
    res = ci_mod.manage_sources("list")
    assert res["ok"] is True and res["owners"] == [] and res["fleet_repo"] == FLEET
    assert res["lines"] == ["Runners in acme/fleet may not deploy any owner's repos yet."]
    assert res["next_step"]["hint"] == "gapp ci sources add <owner>"


def test_add_creates_the_variable_then_appends(gh):
    res = ci_mod.manage_sources("add", "me")
    assert res["ok"] is True and res["changed"] is True and res["owners"] == ["me"]
    assert gh.variables[VAR] == "me"
    assert res["lines"] == ["Runners in acme/fleet may deploy repos owned by me."]
    assert res["next_step"] == {"action": "add", "hint": "gapp add <name> --repo me/<repo> --project <project>"}
    res = ci_mod.manage_sources("add", "other-org")
    assert res["owners"] == ["me", "other-org"] and gh.variables[VAR] == "me,other-org"
    assert ci_mod.manage_sources("list")["lines"] == ["Runners may deploy repos owned by: me, other-org"]


def test_add_of_a_present_owner_and_remove_of_an_absent_one_change_nothing(gh):
    gh.variables[VAR] = "me, other-org"
    res = ci_mod.manage_sources("add", "ME")
    assert res["ok"] is True and res["changed"] is False and gh.variables[VAR] == "me, other-org"
    assert res["lines"] == ["Runners in acme/fleet could already deploy repos owned by ME. Nothing changed."]
    res = ci_mod.manage_sources("remove", "nobody")
    assert res["ok"] is True and res["changed"] is False
    assert res["lines"] == ["nobody was not on the list. Nothing changed."]


def test_remove_rewrites_the_list_and_deletes_it_when_empty(gh):
    gh.variables[VAR] = "me,other-org"
    res = ci_mod.manage_sources("remove", "other-org")
    assert res["ok"] is True and res["owners"] == ["me"] and gh.variables[VAR] == "me"
    assert res["lines"] == ["Runners in acme/fleet do not deploy repos owned by other-org."]
    res = ci_mod.manage_sources("remove", "me")
    assert res["owners"] == [] and VAR not in gh.variables


def test_sources_without_an_owner_a_bad_action_or_gh_are_refused(gh, monkeypatch):
    import shutil
    assert ci_mod.manage_sources("add", "")["next_step"]["action"] == "pass the owner"
    assert ci_mod.manage_sources("frob", "me")["next_step"]["action"] == "choose an action"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = ci_mod.manage_sources("list")
    assert res["refused"] is True and res["next_step"]["action"] == "install gh"


def test_sources_are_refused_when_the_fleet_is_not_in_a_github_repo(gh):
    from gapp.admin.sdk import config as config_mod
    config_mod.set_fleet(repo=None)
    res = ci_mod.manage_sources("list")
    assert res["ok"] is False and res["refused"] is True and res["next_step"]["action"] == "fleet use"
    assert "gapp fleet use <owner>/<repo>" in res["next_step"]["hint"]


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
