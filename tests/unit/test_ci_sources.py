"""`gapp ci sources add|remove|list` manages the CI repo variable
GAPP_ALLOWED_SOURCES, the owners whose repos may deploy through the CI repo.
Needs `gh` with write access; every refusal is an outcome in words."""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
VAR = (CI_REPO, "GAPP_ALLOWED_SOURCES")


@pytest.fixture
def gh(monkeypatch, fake_gh):
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    return fake_gh


def test_list_without_the_variable_is_empty(gh):
    res = ci_mod.manage_sources("list")
    assert res["ok"] is True and res["owners"] == []


def test_add_creates_the_variable_then_appends(gh):
    res = ci_mod.manage_sources("add", "me")
    assert res["ok"] is True and res["changed"] is True and res["owners"] == ["me"]
    assert gh.variables[VAR] == "me"
    assert "may now deploy through acme/ci" in res["note"]
    res = ci_mod.manage_sources("add", "other-org")
    assert res["owners"] == ["me", "other-org"] and gh.variables[VAR] == "me,other-org"


def test_add_of_a_present_owner_is_a_no_op_with_a_note(gh):
    gh.variables[VAR] = "me, other-org"
    res = ci_mod.manage_sources("add", "ME")
    assert res["ok"] is True and res["changed"] is False
    assert "already allowed" in res["note"]
    assert gh.variables[VAR] == "me, other-org"


def test_remove_rewrites_the_list_and_deletes_it_when_empty(gh):
    gh.variables[VAR] = "me,other-org"
    res = ci_mod.manage_sources("remove", "other-org")
    assert res["ok"] is True and res["owners"] == ["me"] and gh.variables[VAR] == "me"
    res = ci_mod.manage_sources("remove", "me")
    assert res["owners"] == [] and VAR not in gh.variables


def test_remove_of_an_absent_owner_is_a_no_op_with_a_note(gh):
    res = ci_mod.manage_sources("remove", "nobody")
    assert res["ok"] is True and res["changed"] is False
    assert "nothing to remove" in res["note"]


def test_sources_without_an_owner_or_with_a_bad_action_stop(gh):
    assert ci_mod.manage_sources("add", "")["next_step"]["action"] == "pass the owner"
    assert ci_mod.manage_sources("frob", "me")["next_step"]["action"] == "choose an action"


def test_sources_without_gh_or_ci_init_stop(gh, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert ci_mod.manage_sources("list")["next_step"]["action"] == "install gh"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: None)
    assert ci_mod.manage_sources("list")["next_step"]["action"] == "ci init"


def test_sources_report_a_refused_write_with_the_gh_command(gh):
    gh.api_writes = False
    res = ci_mod.manage_sources("add", "me")
    assert res["ok"] is False and res["next_step"]["action"] == "sign in to gh with write access"
    assert "not allowed to do that in acme/ci" in res["next_step"]["hint"]
    assert "gh variable set GAPP_ALLOWED_SOURCES --repo acme/ci --body me" in res["next_step"]["hint"]
    assert "403" not in res["next_step"]["hint"]


def test_sources_report_an_unreadable_variable(gh):
    gh.actions_api = False
    res = ci_mod.manage_sources("list")
    assert res["ok"] is False and res["owners"] is None
    assert "Could not read GAPP_ALLOWED_SOURCES in acme/ci" in res["next_step"]["hint"]
