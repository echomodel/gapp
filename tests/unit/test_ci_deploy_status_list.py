"""`gapp ci deploy <name>` dispatches the generic deploy workflow for one
deployment; `gapp ci status <name>` and `gapp ci list` read the fleet. None
touches GCP, and every gap is an outcome.
"""

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import fleet

FLEET = "acme/fleet"
WIF = "projects/1/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])


def _no_gcloud(*args, **kwargs):
    raise AssertionError("the CI system must never call gcloud")


@pytest.fixture
def ci(monkeypatch, fleet_repo, fake_gh):
    monkeypatch.setattr(ci_mod, "run_gcloud", _no_gcloud)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    fake_gh.deploy(FLEET, "my-app", "me/my-app", "my-proj", domain="mcp.example.com")
    fake_gh.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    return fake_gh


# -- deploy --


def test_deploy_dispatches_the_generic_workflow_with_the_deployment_name(ci):
    res = ci_mod.deploy_deployment("my-app", ref="v1.0.0", watch=False)
    assert res["ok"] is True and res["workflow"] == "gapp-deploy.yml"
    assert ci.dispatched == [(FLEET, "gapp-deploy.yml", {"deployment": "my-app", "ref": "v1.0.0"})]
    assert res["run_id"] == "100" and res["run_url"].endswith("/actions/runs/100") and res["next_step"] is None
    assert res["repo"] == "me/my-app" and res["project"] == "my-proj"


def test_deploy_of_an_unknown_name_is_an_outcome_naming_setup_and_list(ci):
    res = ci_mod.deploy_deployment("nope", watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "ci setup"
    assert "'nope' is not a deployment in acme/fleet" in res["next_step"]["hint"]
    assert "gapp ci setup nope --repo <owner>/<repo> --project <project>" in res["next_step"]["hint"]
    assert "gapp ci list" in res["next_step"]["hint"] and ci.dispatched == []


def test_deploy_without_the_deploy_workflow_points_at_ci_init(ci):
    del ci.files[(FLEET, ci_mod.DEPLOY_WORKFLOW_PATH)]
    res = ci_mod.deploy_deployment("my-app", watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"


def test_deploy_reports_a_refused_dispatch_in_githubs_words(ci):
    ci.dispatch_refusal = "gh: HTTP 403: Resource not accessible"
    res = ci_mod.deploy_deployment("my-app", watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "dispatch the deploy workflow"
    assert "GitHub returned 403: Resource not accessible" in res["next_step"]["hint"]


def test_deploy_watches_and_reports_the_conclusion_and_a_failed_run_with_its_link(ci):
    res = ci_mod.deploy_deployment("my-app", watch=True)
    assert res["ok"] is True and res["watched"] is True and res["conclusion"] == "success"
    original = ci._dispatch

    def failing(cmd):
        out = original(cmd)
        for run in ci.runs.values():
            run["conclusion"] = "failure"
        return out

    ci._dispatch = failing
    res = ci_mod.deploy_deployment("my-app", watch=True)
    assert res["ok"] is False and res["conclusion"] == "failure" and res["next_step"]["action"] == "read the run log"
    assert "'my-app' didn't deploy" in res["next_step"]["hint"] and res["run_url"] in res["next_step"]["hint"]


def test_deploy_without_a_name_gh_or_fleet_repo_is_an_outcome(ci, monkeypatch):
    import shutil
    from gapp.admin.sdk import config as config_mod
    assert ci_mod.deploy_deployment("", watch=False)["next_step"]["action"] == "pass the name"
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert ci_mod.deploy_deployment("my-app", watch=False)["next_step"]["action"] == "install gh"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    config_mod.set_fleet_repo(None)
    res = ci_mod.deploy_deployment("my-app", watch=False)
    assert res["next_step"]["action"] == "ci init" and res["next_step"]["hint"] == ci_mod.NO_FLEET_REPO_MESSAGE


def test_trigger_is_the_former_name_of_deploy(ci):
    res = ci_mod.deploy_deployment("my-app", watch=False)
    assert res["ok"] is True
    from click.testing import CliRunner
    from gapp.admin.cli.main import main as cli_main
    out = CliRunner().invoke(cli_main, ["ci", "trigger", "my-app", "--no-watch"]).output
    assert "is now `gapp ci deploy`" in out
    assert "trigger" not in CliRunner().invoke(cli_main, ["ci", "--help"]).output


# -- status --


def test_status_reports_one_deployment_with_its_last_deploy(ci):
    ci_mod.deploy_deployment("my-app", ref="v1.0.0", watch=False)
    ci_mod.deploy_deployment("other", watch=False) if ci.deploy(FLEET, "other", "me/other", "my-proj") is None else None
    res = ci_mod.status_deployment("my-app")
    assert res["ok"] is True and res["registered"] is True
    assert res["repo"] == "me/my-app" and res["project"] == "my-proj" and res["domain"] == "mcp.example.com"
    assert res["id"] == "my-app" and res["overlay"] == {"domain": "mcp.example.com"}
    run = res["last_deploy"]["run"]
    assert run["conclusion"] == "success" and run["ref"] == "v1.0.0" and run["url"].endswith("/actions/runs/100")
    assert res["environment_record"]["exists"] is True and res["environment_record"]["wif_provider_present"] is True
    assert res["next_step"] is None


def test_status_with_no_deploys_yet_and_of_an_unknown_name(ci):
    res = ci_mod.status_deployment("my-app")
    assert res["last_deploy"] == {"checked": True, "run": None, "note": None}
    res = ci_mod.status_deployment("nope")
    assert res["ok"] is False and res["registered"] is False and res["next_step"]["action"] == "ci setup"
    assert "gapp ci setup nope --repo <owner>/<repo> --project <project>" in res["next_step"]["hint"]


def test_status_names_an_untrusted_project_and_a_missing_environment_variable(ci):
    del ci.environments[(FLEET, "my-proj")]
    res = ci_mod.status_deployment("my-app")
    assert res["ok"] is True and res["environment_record"]["exists"] is False
    assert res["next_step"] == {"action": "ci project setup", "hint": "gapp ci project setup my-proj"}
    assert "has no Environment named 'my-proj'" in res["problem"]
    ci.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    res = ci_mod.status_deployment("my-app")
    assert res["environment_record"]["service_account_present"] is False
    assert "GAPP_DEPLOY_SERVICE_ACCOUNT" in res["problem"] and res["next_step"]["hint"] == "gapp ci project setup my-proj"


def test_status_reports_unchecked_variables_and_runs_instead_of_missing(ci):
    ci.actions_api = False
    ci.fail_with["actions/workflows/gapp-deploy.yml/runs"] = "gh: HTTP 403: request blocked"
    res = ci_mod.status_deployment("my-app")
    assert res["ok"] is True and res["next_step"] is None
    assert res["environment_record"]["exists"] is True and res["environment_record"]["checked"] is False
    assert res["last_deploy"]["checked"] is False
    assert res["last_deploy"]["note"].startswith("Couldn't check the deploy runs of acme/fleet: GitHub returned 403: request blocked.")


def test_status_never_reads_the_deployed_repo_or_gcp(ci):
    ci_mod.status_deployment("my-app")
    assert not any(cmd[:3] == ["gh", "api", "repos/me/my-app"] for cmd, _ in ci.calls)


# -- list --


def test_list_shows_every_deployment_and_filters_by_repo(ci):
    ci.deploy(FLEET, "my-app-staging", "me/my-app", "staging-proj")
    ci.deploy(FLEET, "other", "me/other", "my-proj")
    res = ci_mod.list_deployments()
    assert res["ok"] is True and [d["name"] for d in res["deployments"]] == ["my-app", "my-app-staging", "other"]
    assert res["deployments"][0] == {"name": "my-app", "id": "my-app", "repo": "me/my-app", "project": "my-proj",
                                     "domain": "mcp.example.com", "keys": ["domain"]}
    assert res["files"] is None and res["next_step"] is None
    res = ci_mod.list_deployments("me/my-app")
    assert [d["name"] for d in res["deployments"]] == ["my-app", "my-app-staging"]
    res = ci_mod.list_deployments("me/none")
    assert res["deployments"] == [] and res["next_step"]["action"] == "ci setup"


def test_list_of_an_empty_fleet_points_at_setup(ci):
    ci.files[(FLEET, fleet.DEPLOYMENTS_FILE)] = fleet.DEPLOYMENTS_FILE_INITIAL
    res = ci_mod.list_deployments()
    assert res["ok"] is True and res["deployments"] == []
    assert res["next_step"] == {"action": "ci setup", "hint": "gapp ci setup <name> --repo <owner>/<repo> --project <project>"}


def test_list_verbose_adds_files_variables_and_legacy_workflows(ci):
    ci.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    ci.files[(FLEET, ".github/workflows/legacy-app.yml")] = "jobs:\n  deploy:\n    uses: example-org/gapp/.github/workflows/deploy.yml@v3.8.0\n"
    ci.files[(FLEET, ".github/workflows/unrelated.yml")] = "name: lint\n"
    res = ci_mod.list_deployments(verbose=True)
    assert res["files"][ci_mod.DEPLOY_WORKFLOW_PATH] == {"present": True, "current": True, "note": None}
    assert res["files"][fleet.DEPLOYMENTS_FILE]["present"] is True
    assert res["variables"]["GAPP_ALLOWED_SOURCES"] is True and res["variables"]["HOSTING_GITHUB_APP_ID"] is False
    assert res["legacy_workflows"] == ["legacy-app"]
    ci.install(FLEET, gapp_ref="v3.9.0")
    res = ci_mod.list_deployments(verbose=True)
    assert res["files"][ci_mod.DEPLOY_WORKFLOW_PATH]["current"] is False
    assert res["next_step"] == {"action": "ci init", "hint": f"gapp ci init {FLEET}"} and "Not current" in res["problem"]
