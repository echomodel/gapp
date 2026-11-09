"""`gapp ci project setup <project>` is the one CI command that touches GCP.

It creates or confirms the Workload Identity resources and records the
project's deploy identity as the fleet repo's Environment. Every failure is
an outcome that restates the two requirements (gcloud as an Owner of the
project, `gh` as an admin of the fleet repo); no raw gcloud or HTTP error
reaches the operator.
"""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod

FLEET = "acme/fleet"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])
REQUIREMENTS = "requires gcloud signed in as an Owner of the project and `gh` signed in as an admin of the fleet repo"


@pytest.fixture
def machine(monkeypatch, fleet_repo, fake_gh):
    """A workstation with gh and gcloud, every GCP step stubbed to succeed."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("gh", "gcloud") else None)
    monkeypatch.setattr(ci_mod, "require_gcloud_account", lambda: "owner@example.com")
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda project_id: "created")
    monkeypatch.setattr(ci_mod, "_ensure_wif_provider", lambda project_id, owner: "created")
    monkeypatch.setattr(ci_mod, "_ensure_deploy_sa", lambda project_id: "created")
    monkeypatch.setattr(ci_mod, "_ensure_wif_binding", lambda project_id, fleet_repo: "set")
    monkeypatch.setattr(ci_mod, "_get_project_number", lambda project_id: "123456789012")
    monkeypatch.setattr(ci_mod, "_mark_project_hosted", lambda project_id: "set")
    return fake_gh


def test_project_setup_creates_the_resources_and_records_the_environment(machine):
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is True and res["next_step"] is None and res["changed"] is True
    assert res["fleet_repo"] == FLEET and res["wif_pool"] == "created" and res["binding"] == "set"
    assert res["hosted"] == "set"
    assert res["environment_record"] == "created"
    assert machine.environments[(FLEET, "my-proj")] == {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}


def test_project_setup_is_idempotent(machine, monkeypatch):
    ci_mod.setup_ci_project("my-proj")
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa"):
        monkeypatch.setattr(ci_mod, step, lambda *a: "exists")
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is True and res["changed"] is False
    assert res["wif_pool"] == "exists" and res["environment_record"] == "unchanged"


def test_project_setup_without_a_project_gh_or_fleet_repo_stops(machine, monkeypatch):
    from gapp.admin.sdk import config as config_mod
    assert ci_mod.setup_ci_project("")["next_step"]["action"] == "pass the project"
    config_mod.set_fleet_repo(None)
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and res["next_step"]["hint"] == ci_mod.NO_FLEET_REPO_MESSAGE
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gcloud" if name == "gcloud" else None)
    assert ci_mod.setup_ci_project("my-proj")["next_step"]["action"] == "install gh"


def test_project_setup_when_gh_is_not_an_admin_stops_before_gcloud(machine, monkeypatch):
    machine.admin[FLEET] = False
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda p: pytest.fail("gcloud must not run"))
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "sign in to gh as an admin"
    assert "not an admin of acme/fleet" in res["next_step"]["hint"]
    assert REQUIREMENTS in res["next_step"]["hint"] and "gh auth login" in res["next_step"]["hint"]


def test_project_setup_when_gh_cannot_read_the_repo_says_so_in_githubs_words(machine):
    machine.fail_with["repos/acme/fleet"] = "gh: HTTP 401: Bad credentials (https://api.github.com/repos/acme/fleet)"
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False
    assert "Couldn't check your permission on acme/fleet: GitHub returned 401: Bad credentials. Request: GET repos/acme/fleet" in res["next_step"]["hint"]


def test_project_setup_without_gcloud_names_the_machine_to_run_from(machine, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "install gcloud"
    assert "every other `gapp ci` command works from here" in res["next_step"]["hint"]
    assert machine.env_writes == []


def test_project_setup_with_an_uncredentialed_account_fails_by_name(machine, monkeypatch):
    def refuse():
        raise RuntimeError("gapp is configured to use the account owner@example.com (gapp config account), but gcloud has no credential for it. Run `gcloud auth login owner@example.com`.")
    monkeypatch.setattr(ci_mod, "require_gcloud_account", refuse)
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "sign in to gcloud as an Owner"
    assert "gcloud auth login owner@example.com" in res["next_step"]["hint"] and REQUIREMENTS in res["next_step"]["hint"]


def test_project_setup_translates_gcloud_failures_into_the_owner_requirement(machine, monkeypatch):
    def denied(project_id):
        raise RuntimeError("ERROR: (gcloud.iam.workload-identity-pools.create) PERMISSION_DENIED: Permission 'iam.workloadIdentityPools.create' denied on resource")
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", denied)
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and "not an Owner of my-proj (permission denied)" in res["next_step"]["hint"]
    assert "PERMISSION_DENIED" not in res["next_step"]["hint"] and machine.env_writes == []
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda p: (_ for _ in ()).throw(RuntimeError("ERROR: UNAUTHENTICATED: Request had invalid authentication credentials")))
    assert "no working sign-in" in ci_mod.setup_ci_project("my-proj")["next_step"]["hint"]
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda p: (_ for _ in ()).throw(RuntimeError("ERROR: The project my-proj was not found (404)")))
    assert "was not found by the signed-in gcloud account" in ci_mod.setup_ci_project("my-proj")["next_step"]["hint"]


def test_project_setup_reports_a_refused_environment_write(machine):
    machine.api_writes = False
    res = ci_mod.setup_ci_project("my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "sign in to gh as an admin"
    assert "GCP is ready" in res["next_step"]["hint"]
    assert "could not create the Environment 'my-proj': GitHub returned 403" in res["next_step"]["hint"]
    assert res["wif_pool"] == "created"


def test_marking_the_project_hosted_keeps_its_other_labels(monkeypatch):
    """`gcloud projects update --update-labels` merges: gapp=hosted joins the project's labels."""
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(ci_mod, "run_gcloud", lambda args, **kw: calls.append(args) or Done())
    assert ci_mod._mark_project_hosted("my-proj") == "set"
    assert calls == [["projects", "update", "my-proj", "--update-labels=gapp=hosted"]]
