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

    def failing(endpoint, payload):
        out = original(endpoint, payload)
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


def test_status_default_is_github_only_and_ends_with_the_live_hint(ci):
    res = ci_mod.status_deployment("my-app")
    assert res["live_hint"] == "For the live service state: gapp ci status my-app --live"
    assert not any(c[:3] == ["gh", "workflow", "run"] for c, _ in ci.calls)      # fast: nothing dispatched


def test_status_live_dispatches_the_status_workflow_and_returns_gapp_status_result(ci):
    res = ci_mod.status_live("my-app")
    assert res["ok"] is True and res["workflow"] == "gapp-status.yml"
    assert ci.dispatched == [(FLEET, "gapp-status.yml", {"deployment": "my-app"})]
    assert res["run_url"].endswith("/actions/runs/100") and res["conclusion"] == "success"
    assert res["live"]["deployment"]["services"][0]["url"].startswith("https://my-app-")
    assert res["live"]["deployment"]["pending"] is False and res["next_step"] is None
    assert ci.puts == []                                           # nothing stored in the fleet repo


def test_status_live_reports_a_failed_run_in_its_words_and_an_unknown_name(ci):
    ci.status_outcome = lambda inputs: ("failure", ["error=No Google Cloud identity this run can use."])
    res = ci_mod.status_live("my-app")
    assert res["ok"] is False and res["live"] is None and res["next_step"]["action"] == "read the run log"
    assert "No Google Cloud identity this run can use." in res["next_step"]["hint"] and res["run_url"] in res["next_step"]["hint"]
    res = ci_mod.status_live("nope")
    assert res["ok"] is False and res["next_step"]["action"] == "ci setup"
    del ci.files[(FLEET, ci_mod.STATUS_WORKFLOW_PATH)]
    res = ci_mod.status_live("my-app")
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"


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


# -- reading a run's log: only what the steps printed --

# A job log as GitHub returns it for the status workflow's resolve job on an
# unknown deployment: the step's script is echoed first (its message
# templates unexpanded), then what the step actually printed.
REAL_SHAPED_LOG = "\n".join([
    "﻿2026-11-09T05:30:00.0000000Z Requested labels: ubuntu-latest",
    "2026-11-09T05:30:05.1000000Z ##[group]Run set -euo pipefail",
    "2026-11-09T05:30:05.1000000Z \x1b[36;1mset -euo pipefail\x1b[0m",
    "2026-11-09T05:30:05.1000000Z \x1b[36;1mif [ \"$(yq 'has(strenv(DEPLOYMENT))' \"$DATA_FILE\")\" != \"true\" ]; then\x1b[0m",
    "2026-11-09T05:30:05.1000000Z \x1b[36;1m  echo \"gapp-status: '$DEPLOYMENT' is not a deployment in $DATA_FILE. Set it up first: gapp ci setup $DEPLOYMENT --repo <owner>/<repo> --project <project>\"\x1b[0m",
    "2026-11-09T05:30:05.1000000Z \x1b[36;1m  echo \"gapp-status: the Environment '$ENVIRONMENT' of this fleet repo has no $missing variable\"\x1b[0m",
    "2026-11-09T05:30:05.1000000Z \x1b[36;1mfi\x1b[0m",
    "2026-11-09T05:30:05.1000000Z shell: /usr/bin/bash -e {0}",
    "2026-11-09T05:30:05.1000000Z env:",
    "2026-11-09T05:30:05.1000000Z   DEPLOYMENT: nope",
    "2026-11-09T05:30:05.1000000Z ##[endgroup]",
    "2026-11-09T05:30:05.9000000Z gapp-status: 'nope' is not a deployment in gapp/deployments.yaml. Set it up first: gapp ci setup nope --repo <owner>/<repo> --project <project>",
    "2026-11-09T05:30:06.0000000Z ##[error]Process completed with exit code 1.",
])


def test_emitted_messages_skip_the_echoed_script_and_keep_what_the_step_printed():
    messages = ci_mod._emitted_messages(REAL_SHAPED_LOG, ci_mod.STATUS_LOG_PREFIX)
    assert messages == ["'nope' is not a deployment in gapp/deployments.yaml. Set it up first: "
                        "gapp ci setup nope --repo <owner>/<repo> --project <project>"]
    assert not any("$" in m for m in messages)


def test_status_live_failure_quotes_only_the_printed_message(ci):
    ci.status_outcome = lambda inputs: ("failure", [])
    original = ci._status

    def with_real_log(repo, inputs):
        return "failure", REAL_SHAPED_LOG

    ci._status = with_real_log
    res = ci_mod.status_live("my-app")
    assert res["ok"] is False
    assert "'nope' is not a deployment in gapp/deployments.yaml" in res["next_step"]["hint"]
    assert "$DEPLOYMENT" not in res["next_step"]["hint"] and "$missing" not in res["next_step"]["hint"]
    ci._status = original


def test_run_logs_are_read_through_rest_job_logs(ci):
    ci_mod.status_live("my-app")
    reads = [c[2] for c, _ in ci.calls if c[:2] == ["gh", "api"] and len(c) > 2]
    assert any(r.endswith("/jobs") for r in reads) and any("/actions/jobs/" in r and r.endswith("/logs") for r in reads)


def test_deploy_watch_polls_the_run_through_rest(ci):
    res = ci_mod.deploy_deployment("my-app", watch=True)
    assert res["ok"] is True and res["watched"] is True and res["conclusion"] == "success"
    assert all(c[:2] == ["gh", "api"] for c, _ in ci.calls)


def test_status_names_an_entry_key_gapp_yaml_does_not_have(ci):
    ci.deploy(FLEET, "typo-app", "me/typo-app", "my-proj", memroy="1Gi")
    res = ci_mod.status_deployment("typo-app")
    assert res["problem"] == ("The entry 'typo-app' in gapp/deployments.yaml has unknown key(s) memroy, "
                              "so a deploy would refuse it.")
    assert ci_mod.status_deployment("my-app")["problem"] is None
