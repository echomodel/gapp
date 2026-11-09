"""`gapp ci init` is the only command that sets the fleet repo: it shows,
finds, sets or creates it, installs gapp's files, and retires an empty
gapp 3.9 solutions file. Every refusal is a plain sentence with the fix.
"""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import fleet

FLEET = "acme/fleet"


@pytest.fixture
def machine(monkeypatch, fake_gh, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    fake_gh.login = "me"
    return fake_gh


def test_init_shows_the_saved_fleet_repo_and_how_it_was_set(machine):
    config_mod.set_fleet_repo(FLEET, "gapp ci init acme/fleet")
    res = ci_mod.init_fleet()
    assert res["ok"] is True and res["fleet_repo"] == FLEET and res["set_by"] == "gapp ci init acme/fleet"
    assert machine.calls == []                                   # nothing asked of GitHub


def test_init_without_a_saved_repo_finds_the_one_tagged_repo_and_saves_it(machine):
    machine.private["me/fleet"] = True
    machine.topics["me/fleet"] = {"gapp-ci"}
    res = ci_mod.init_fleet()
    assert res["ok"] is True and res["fleet_repo"] == "me/fleet"
    assert "gapp-ci topic" in res["set_by"]
    assert config_mod.get_fleet_repo() == ("me/fleet", res["set_by"])
    searches = [c for c, _ in machine.calls if "search/repositories" in c]
    assert len(searches) == 1 and "q=topic:gapp-ci user:me" in searches[0]               # one REST search, the gh user


def test_init_with_an_owner_searches_that_owner(machine):
    machine.private["other-org/fleet"] = True
    machine.topics["other-org/fleet"] = {"gapp-ci"}
    res = ci_mod.init_fleet(owner="other-org")
    assert res["fleet_repo"] == "other-org/fleet"
    search = next(c for c, _ in machine.calls if "search/repositories" in c)
    assert "q=topic:gapp-ci user:other-org" in search


def test_init_lists_several_tagged_repos_and_stops(machine):
    for r in ("me/fleet-a", "me/fleet-b"):
        machine.private[r] = True
        machine.topics[r] = {"gapp-ci"}
    res = ci_mod.init_fleet()
    assert res["ok"] is False and res["found"] == ["me/fleet-a", "me/fleet-b"]
    assert "me/fleet-a, me/fleet-b" in res["next_step"]["hint"] and "gapp ci init <owner>/<repo>" in res["next_step"]["hint"]
    assert config_mod.get_fleet_repo() == (None, None)


def test_init_with_nothing_tagged_gives_the_create_guidance(machine):
    res = ci_mod.init_fleet()
    assert res["ok"] is False
    assert "no repo under your GitHub account carries the gapp-ci topic" in res["next_step"]["hint"]
    assert "gapp ci init <owner>/<repo> --create" in res["next_step"]["hint"]


def test_init_with_an_existing_repo_sets_it_and_installs_the_files(machine):
    machine.private[FLEET] = True
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is True and res["fleet_repo"] == FLEET and res["created"] is False
    assert config_mod.get_fleet_repo() == (FLEET, "gapp ci init acme/fleet")
    assert res["files"] == {ci_mod.DEPLOY_WORKFLOW_PATH: "created", ci_mod.REGISTER_WORKFLOW_PATH: "created",
                            ci_mod.STATUS_WORKFLOW_PATH: "created", fleet.DEPLOYMENTS_FILE: "created"}
    assert "gapp-ci" in machine.topics[FLEET]
    assert fleet.parse(machine.files[(FLEET, fleet.DEPLOYMENTS_FILE)]) == {}
    assert "deploy.yml@v4.0.0" in machine.files[(FLEET, ci_mod.DEPLOY_WORKFLOW_PATH)]
    assert res["next_step"] == {"action": "ci sources add", "hint": "gapp ci sources add <owner>"}


def test_init_is_idempotent_updates_outdated_workflows_and_never_touches_the_deployments_file(machine):
    machine.install(FLEET, gapp_ref="v3.9.0")
    machine.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    machine.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    res = ci_mod.init_fleet(FLEET)
    assert res["files"][ci_mod.DEPLOY_WORKFLOW_PATH] == "updated"
    assert res["files"][ci_mod.REGISTER_WORKFLOW_PATH] == "updated"
    assert res["files"][ci_mod.STATUS_WORKFLOW_PATH] == "updated"
    assert res["files"][fleet.DEPLOYMENTS_FILE] == "unchanged"
    assert "my-app" in machine.files[(FLEET, fleet.DEPLOYMENTS_FILE)]
    # my-proj has no Environment in the fleet yet, so init names it.
    assert res["next_step"] == {"action": "ci project setup", "hint": "gapp ci project setup my-proj"}
    writes = len(machine.puts)
    res = ci_mod.init_fleet(FLEET)
    assert all(s == "unchanged" for s in res["files"].values()) and len(machine.puts) == writes


def test_init_with_a_missing_repo_stops_with_the_create_message(machine):
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is False
    assert res["next_step"]["hint"] == "acme/fleet doesn't exist. Create it as a new private fleet repo: gapp ci init acme/fleet --create"
    assert config_mod.get_fleet_repo() == (None, None)


def test_init_create_makes_a_private_repo_under_an_org_through_rest(machine):
    res = ci_mod.init_fleet(FLEET, create=True)
    create = next(c for c, _ in machine.calls if "-X" in c and "POST" in c and c[c.index("-X") + 2].endswith("/repos"))
    assert create[create.index("-X") + 2] == "orgs/acme/repos"                       # acme is not the gh user


def test_init_create_under_the_gh_users_own_account_uses_user_repos(machine):
    res = ci_mod.init_fleet("me/fleet", create=True)
    assert res["ok"] is True and machine.created == ["me/fleet"]
    create = next(c for c, _ in machine.calls if "-X" in c and "POST" in c and c[c.index("-X") + 2].endswith("repos"))
    assert create[create.index("-X") + 2] == "user/repos"


def test_init_adds_the_topic_keeping_the_repos_other_topics(machine):
    machine.private[FLEET] = True
    machine.topics[FLEET] = {"infra"}
    ci_mod.init_fleet(FLEET)
    assert machine.topics[FLEET] == {"infra", "gapp-ci"}


def test_init_create_makes_a_private_tagged_populated_repo_and_sets_it(machine):
    res = ci_mod.init_fleet(FLEET, create=True)
    assert res["ok"] is True and res["created"] is True
    assert machine.created == [FLEET] and machine.private[FLEET] is True
    assert "gapp-ci" in machine.topics[FLEET]
    assert set(res["files"]) == {ci_mod.DEPLOY_WORKFLOW_PATH, ci_mod.REGISTER_WORKFLOW_PATH, ci_mod.STATUS_WORKFLOW_PATH, fleet.DEPLOYMENTS_FILE}
    assert config_mod.get_fleet_repo() == (FLEET, "gapp ci init acme/fleet --create")


def test_init_create_on_an_existing_repo_stops_with_the_use_message(machine):
    machine.private[FLEET] = True
    res = ci_mod.init_fleet(FLEET, create=True)
    assert res["ok"] is False and machine.created == []
    assert res["next_step"]["hint"] == "acme/fleet already exists, so it wasn't created. To use it as your fleet repo: gapp ci init acme/fleet"


def test_init_removes_an_empty_39_solutions_file_and_reports_a_non_empty_one(machine):
    machine.private[FLEET] = True
    machine.files[(FLEET, "gapp/solutions.yaml")] = "solutions: []\n"
    res = ci_mod.init_fleet(FLEET)
    assert res["legacy_solutions"] == {"kept": False, "count": 0}
    assert (FLEET, "gapp/solutions.yaml") not in machine.files and machine.deletes == [(FLEET, "gapp/solutions.yaml")]
    machine.files[(FLEET, "gapp/solutions.yaml")] = "solutions:\n  - name: old\n    repo: me/old\n    project: p-one\n"
    res = ci_mod.init_fleet(FLEET)
    assert res["legacy_solutions"] == {"kept": True, "count": 1}
    assert (FLEET, "gapp/solutions.yaml") in machine.files
    assert "gapp/solutions.yaml from gapp 3.9 with 1 solution" in res["next_step"]["hint"]
    assert "gapp ci setup <name> --repo <owner>/<repo> --project <project>" in res["next_step"]["hint"]


def test_init_where_api_writes_are_refused_still_sets_the_repo_and_names_the_machine(machine):
    machine.private[FLEET] = True
    machine.api_writes = False
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is False and res["files"] is None
    assert config_mod.get_fleet_repo()[0] == FLEET                 # set: it is the fleet repo now
    assert "could not be installed in it from this machine" in res["next_step"]["hint"]
    assert "GitHub returned 403" in res["next_step"]["hint"]
    assert f"gapp ci init {FLEET}" in res["next_step"]["hint"]


def test_init_reports_a_read_it_could_not_make_in_githubs_words(machine):
    machine.fail_with["repos/acme/fleet"] = "gh: HTTP 500: Internal Server Error (https://api.github.com/repos/acme/fleet)"
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is False
    assert res["next_step"]["hint"] == "Couldn't check acme/fleet: GitHub returned 500: Internal Server Error. Request: GET repos/acme/fleet"


def test_init_requires_owner_slash_name(machine):
    res = ci_mod.init_fleet("fleet")
    assert res["ok"] is False and "owner/name" in res["next_step"]["hint"]


def test_init_without_gh_is_an_outcome(monkeypatch, fake_gh):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is False and res["next_step"]["action"] == "install gh"
    assert fake_gh.calls == []


def test_init_on_a_ready_fleet_says_it_is_ready_instead_of_a_setup_step(machine):
    machine.install(FLEET)
    machine.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    machine.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    machine.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": "p", "GAPP_DEPLOY_SERVICE_ACCOUNT": "s"}
    res = ci_mod.init_fleet(FLEET)
    assert res["ok"] is not False and res["next_step"] is None
    assert res["ready"].startswith("The fleet is ready: 1 deployment(s), each project trusted.")


def test_init_names_the_project_whose_environment_lacks_the_deploy_identity(machine):
    machine.install(FLEET)
    machine.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    machine.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    machine.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": "p"}
    res = ci_mod.init_fleet(FLEET)
    assert res["next_step"] == {"action": "ci project setup", "hint": "gapp ci project setup my-proj"} and res["ready"] is None


def test_init_on_an_empty_fleet_points_at_project_setup_then_setup(machine):
    machine.install(FLEET)
    machine.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    res = ci_mod.init_fleet(FLEET)
    assert res["next_step"]["hint"].startswith("gapp ci project setup <project> (once per GCP project), then gapp ci setup")
