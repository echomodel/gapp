"""`gapp ci projects connect <project>` lets the fleet repo's runners sign in
to a project, and is the one `gapp ci` command that touches Google Cloud.
`gapp ci projects list` reads the fleet repo's record alone. Neither
prepares a project for deployments: that is `gapp project setup`."""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import config as config_mod

FLEET = "acme/fleet"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])
REQUIREMENTS = "needs gcloud signed in as an Owner of the project and `gh` signed in as an admin of the fleet repo"
STEPS = ("_enable_sign_in_apis", "_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa", "_ensure_wif_binding")


@pytest.fixture
def machine(monkeypatch, fleet_repo, fake_gh):
    """A workstation with gh and gcloud, a project that is set up, and every step in it stubbed to succeed."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("gh", "gcloud") else None)
    monkeypatch.setattr(ci_mod, "require_gcloud_account", lambda: "owner")
    monkeypatch.setattr(ci_mod, "_project_is_set_up", lambda project_id: True)
    monkeypatch.setattr(ci_mod, "_enable_sign_in_apis", lambda project_id: "enabled")
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda project_id: "created")
    monkeypatch.setattr(ci_mod, "_ensure_wif_provider", lambda project_id, owner: "created")
    monkeypatch.setattr(ci_mod, "_ensure_deploy_sa", lambda project_id: "created")
    monkeypatch.setattr(ci_mod, "_ensure_wif_binding", lambda project_id, fleet_repo: "set")
    monkeypatch.setattr(ci_mod, "_get_project_number", lambda project_id: "123456789012")
    return fake_gh


def _nothing_ran(monkeypatch):
    for step in STEPS:
        monkeypatch.setattr(ci_mod, step, lambda *a: pytest.fail("a step ran in the project"))


# -- connect --


def test_connect_makes_the_identity_and_records_it_as_the_projects_environment(machine):
    res = ci_mod.connect_project("my-proj")
    assert res["ok"] is True and res["changed"] is True and res["fleet_repo"] == FLEET
    assert (res["apis"], res["wif_pool"], res["wif_provider"], res["service_account"], res["binding"]) == (
        "enabled", "created", "created", "created", "set")
    assert res["environment_record"] == "created"
    assert machine.environments[(FLEET, "my-proj")] == {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    assert res["lines"] == ["Runners in acme/fleet can deploy to my-proj."]
    assert res["next_step"] == {"action": "ci deploy", "hint": "gapp ci deploy --deployment <name>"}


def test_the_provider_trusts_the_fleet_repos_owner_and_the_binding_its_repo(machine, monkeypatch):
    seen = {}
    monkeypatch.setattr(ci_mod, "_ensure_wif_provider", lambda project_id, owner: seen.setdefault("owner", owner) and "created")
    monkeypatch.setattr(ci_mod, "_ensure_wif_binding", lambda project_id, repo: seen.setdefault("repo", repo) and "set")
    ci_mod.connect_project("my-proj")
    assert seen == {"owner": "acme", "repo": FLEET}


def test_connect_enables_only_the_apis_a_sign_in_uses(monkeypatch):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(ci_mod, "run_gcloud", lambda args, **kw: calls.append(args) or Done())
    assert ci_mod._enable_sign_in_apis("my-proj") == "enabled"
    assert calls == [["services", "enable", "iam.googleapis.com", "iamcredentials.googleapis.com",
                      "sts.googleapis.com", "--project", "my-proj"]]


def test_connect_again_changes_nothing(machine, monkeypatch):
    ci_mod.connect_project("my-proj")
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa"):
        monkeypatch.setattr(ci_mod, step, lambda *a: "exists")
    machine.env_writes.clear()
    res = ci_mod.connect_project("my-proj")
    assert res["ok"] is True and res["changed"] is False and res["environment_record"] == "unchanged"
    assert [w for w in machine.env_writes if w[0] != "PUT"] == []
    assert res["lines"] == ["Runners in acme/fleet could already deploy to my-proj. Nothing changed."]


def test_connect_is_refused_on_a_project_that_is_not_set_up(machine, monkeypatch):
    monkeypatch.setattr(ci_mod, "_project_is_set_up", lambda project_id: False)
    _nothing_ran(monkeypatch)
    res = ci_mod.connect_project("my-proj")
    assert res["ok"] is False and res["refused"] is True and res["next_step"]["action"] == "project setup"
    assert res["next_step"]["hint"] == (
        "my-proj is not set up for gapp, so runners were not connected to it. Set it up, once per project: "
        "gapp project setup my-proj. Then run this again: gapp ci projects connect my-proj")
    assert (FLEET, "my-proj") not in machine.environments


def test_connect_does_none_of_project_setups_work(machine, monkeypatch):
    from gapp.admin.sdk import core
    monkeypatch.setattr(core.GappSDK, "project_setup", lambda *a, **k: pytest.fail("project setup ran"))
    assert ci_mod.connect_project("my-proj")["ok"] is True


@pytest.mark.parametrize("project", ["", "  ", "My Project", "x"])
def test_connect_refuses_what_is_not_a_project_id(machine, monkeypatch, project):
    _nothing_ran(monkeypatch)
    res = ci_mod.connect_project(project)
    assert res["refused"] is True and res["next_step"]["action"] == "pass the project"
    assert res["next_step"]["hint"].endswith("Name the project: gapp ci projects connect <project>")


def test_connect_is_refused_when_the_fleet_is_not_in_a_github_repo(machine, monkeypatch):
    _nothing_ran(monkeypatch)
    config_mod.set_fleet_repo(None, "fleet use")
    res = ci_mod.connect_project("my-proj")
    assert res["refused"] is True and res["next_step"]["action"] == "fleet use"


def test_connect_is_refused_for_a_login_that_is_not_the_fleet_repos_admin(machine, monkeypatch):
    _nothing_ran(monkeypatch)
    machine.admin[FLEET] = False
    res = ci_mod.connect_project("my-proj")
    assert res["refused"] is True and res["next_step"]["action"] == "sign in to gh as an admin"
    hint = res["next_step"]["hint"]
    assert "the signed-in `gh` account is not an admin of acme/fleet" in hint and REQUIREMENTS in hint
    assert hint.endswith("then run it again: gapp ci projects connect my-proj")


def test_connect_without_gcloud_is_refused(machine, monkeypatch):
    _nothing_ran(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    res = ci_mod.connect_project("my-proj")
    assert res["refused"] is True and res["next_step"]["action"] == "install gcloud" and REQUIREMENTS in res["next_step"]["hint"]


def test_connect_without_a_gcloud_sign_in_is_refused(machine, monkeypatch):
    def none():
        raise RuntimeError("gcloud has no active account.")
    _nothing_ran(monkeypatch)
    monkeypatch.setattr(ci_mod, "require_gcloud_account", none)
    res = ci_mod.connect_project("my-proj")
    assert res["refused"] is True and res["next_step"]["action"] == "sign in to gcloud as an Owner"
    assert res["next_step"]["hint"].startswith("gcloud has no active account.") and REQUIREMENTS in res["next_step"]["hint"]


@pytest.mark.parametrize("stderr, why", [
    ("ERROR: (gcloud.iam) PERMISSION_DENIED: Permission 'iam.workloadIdentityPools.create' denied",
     "the signed-in gcloud account is not an Owner of my-proj (permission denied)"),
    ("ERROR: (gcloud.iam) You do not currently have an active account selected. Please run: gcloud auth login",
     "gcloud has no working sign-in on this machine"),
    ("ERROR: (gcloud.iam) NOT_FOUND: Project my-proj not found", "project my-proj was not found by the signed-in gcloud account"),
])
def test_a_refused_step_stops_there_and_restates_the_requirements(machine, monkeypatch, stderr, why):
    def refuse(project_id):
        raise RuntimeError(stderr)
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", refuse)
    monkeypatch.setattr(ci_mod, "_ensure_wif_provider", lambda *a: pytest.fail("ran after a refused step"))
    res = ci_mod.connect_project("my-proj")
    assert res["ok"] is False and res["refused"] is False and res["next_step"]["action"] == "sign in to gcloud as an Owner"
    hint = res["next_step"]["hint"]
    assert hint.startswith(f"Could not create or confirm the Workload Identity pool for project my-proj: {why}.")
    assert REQUIREMENTS in hint and hint.endswith("then run it again: gapp ci projects connect my-proj")
    assert (FLEET, "my-proj") not in machine.environments


def test_a_command_gcloud_rejects_is_reported_as_a_defect_not_a_sign_in_problem(machine, monkeypatch):
    def refuse(project_id):
        raise RuntimeError("ERROR: (gcloud.iam) unrecognized arguments: --frob")
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", refuse)
    res = ci_mod.connect_project("my-proj")
    assert res["next_step"]["action"] == "report the gcloud error"
    assert "This is a gapp defect, not a sign-in or permission problem" in res["next_step"]["hint"]


def test_any_other_gcloud_error_is_given_as_it_is(machine, monkeypatch):
    def refuse(project_id):
        raise RuntimeError("ERROR: quota exceeded for pools")
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", refuse)
    res = ci_mod.connect_project("my-proj")
    assert res["next_step"]["action"] == "check the error"
    assert "ERROR: quota exceeded for pools. That is the error itself, not a sign-in problem." in res["next_step"]["hint"]


def test_an_environment_github_refuses_to_write_is_a_failure_that_keeps_the_project_work(machine):
    machine.api_writes = False
    res = ci_mod.connect_project("my-proj")
    assert res["ok"] is False and res["refused"] is False and res["wif_pool"] == "created"
    hint = res["next_step"]["hint"]
    assert hint.startswith("The project is ready, but its deploy identity could not be recorded in acme/fleet: "
                           "could not create the Environment 'my-proj': GitHub returned 403")
    assert hint.endswith("Then run it again; it only adds what is missing: gapp ci projects connect my-proj")


# -- list --


def test_list_names_the_projects_whose_environment_holds_a_deploy_identity(fleet_repo, fake_gh):
    fake_gh.connect(FLEET, "my-proj")
    fake_gh.connect(FLEET, "another-proj")
    fake_gh.environments[(FLEET, "production")] = {}          # an Environment gapp did not make
    res = ci_mod.list_projects()
    assert res["ok"] is True and res["projects"] == ["another-proj", "my-proj"] and res["unchecked"] == []
    assert res["lines"] == ["Runners in acme/fleet can deploy to:", "  another-proj", "  my-proj"]
    assert res["next_step"] is None


def test_list_with_no_project_names_connect(fleet_repo, fake_gh):
    res = ci_mod.list_projects()
    assert res["projects"] == [] and res["lines"] == ["Runners in acme/fleet are not connected to any project."]
    assert res["next_step"]["hint"] == "gapp ci projects connect <project>"


def test_list_reads_github_alone_and_never_runs_gcloud(ci):
    assert ci_mod.list_projects()["projects"] == ["my-proj"]


def test_list_says_which_environments_it_could_not_check(fleet_repo, fake_gh):
    fake_gh.connect(FLEET, "my-proj")
    fake_gh.fail_with["environments/my-proj/variables"] = "gh: HTTP 403: request blocked"
    res = ci_mod.list_projects()
    assert res["ok"] is True and res["projects"] == [] and res["unchecked"] == ["my-proj"]
    assert res["lines"][-1].startswith("Couldn't check my-proj:")


def test_list_that_cannot_read_the_environments_is_a_failure_in_githubs_words(fleet_repo, fake_gh):
    fake_gh.unreadable.add(FLEET)
    res = ci_mod.list_projects()
    assert res["ok"] is False and res["next_step"]["action"] == "check GitHub access"
    assert res["next_step"]["hint"].startswith(
        "Couldn't check the Environments of acme/fleet: GitHub returned 403: GitHub access to this repository is "
        "not enabled for this session. Request: GET repos/acme/fleet/environments")
