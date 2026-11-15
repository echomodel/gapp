"""`gapp ci deploy` starts the fleet repo's deploy workflow with a
deployment's name and nothing else, waits for that run, and reports what
the run said. What a run would refuse for want of setup is refused here
first, before any run is started, when GitHub confirms it; what cannot
be checked is left to the run."""

import datetime
import json

import pytest


from gapp import __version__
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import config as config_mod

from .conftest import job_log

FLEET = "acme/fleet"
DEPLOY = "gapp-deploy.yml"


def _started(ci):
    return [(workflow, inputs) for _, workflow, inputs in ci.dispatched]


def _refused_before_a_run(ci, res, action):
    assert res["ok"] is False and res["refused"] is True and res["next_step"]["action"] == action
    assert res["dispatched"] is False and ci.dispatched == []
    return res["next_step"]["hint"]


# -- a deploy that works --


def test_deploy_starts_the_run_with_the_name_and_nothing_else(ci):
    res = ci_mod.deploy("my-app")
    assert _started(ci) == [(DEPLOY, {"deployment": "my-app", "upgrade": "false"})]
    assert res["ok"] is True and res["dispatched"] is True and res["watched"] is True and res["conclusion"] == "success"
    assert (res["deployment"], res["repo"], res["project"], res["ref"], res["gapp"]) == (
        "my-app", "me/my-app", "my-proj", None, __version__)
    assert res["run_id"] == "100" and res["run_url"] == "https://github.com/acme/fleet/actions/runs/100"


def test_deploy_reports_what_the_run_deployed(ci):
    res = ci_mod.deploy("my-app")
    assert res["deployed"]["service_url"] == "https://my-app-abc-uc.a.run.app"
    assert res["lines"] == [
        "my-app is deployed to my-proj, built from the default branch at abc1234.",
        "URL: https://my-app-abc-uc.a.run.app",
        "Run: https://github.com/acme/fleet/actions/runs/100",
    ]
    assert res["next_step"] == {"action": "ci status", "hint": "gapp ci status --deployment my-app --live"}


def test_deploy_says_what_it_is_about_to_build_before_the_run_starts(ci):
    ci.define(FLEET, "pinned", "me/my-app", "my-proj", ref="v1.4.0")
    said = []
    ci_mod.deploy("pinned", progress=said.append)
    assert said == ["Deploying pinned: v1.4.0 of me/my-app, to my-proj."]


def test_upgrade_is_passed_to_the_run_and_its_result_reported(ci):
    def upgraded(inputs):
        assert inputs["upgrade"] == "true"
        return "success", ["result=" + json.dumps({"ref": None, "commit": "abc1234", "gapp": "4.1.0",
                                                   "upgraded_from": "4.0.0", "service_url": "https://x.example.com"})]
    ci.deploy_outcome = upgraded
    res = ci_mod.deploy("my-app", upgrade=True)
    assert res["ok"] is True and res["upgrade"] is True
    assert "Upgraded from gapp 4.0.0 to 4.1.0." in res["lines"]


def test_what_the_run_warned_about_is_relayed(ci):
    warning = "'my-app' sets OLD_SETTING, and this version's app.json does not list it. The app may not read it."
    ci.deploy_outcome = lambda inputs: ("success", ["result=" + json.dumps({"commit": "abc1234", "warnings": [warning]})])
    assert ci_mod.deploy("my-app")["lines"][0] == warning


def test_no_watch_returns_once_the_run_is_started(ci):
    res = ci_mod.deploy("my-app", watch=False)
    assert res["ok"] is True and res["watched"] is False and res["conclusion"] is None and res["deployed"] is None
    assert res["lines"] == [
        "Started a deploy of my-app (the default branch of me/my-app, to my-proj): https://github.com/acme/fleet/actions/runs/100"]
    assert res["next_step"]["hint"] == "gapp ci status --deployment my-app"


def test_inside_a_clone_the_deployment_is_the_one_whose_repo_it_is(ci, monkeypatch, tmp_path):
    from gapp.admin.sdk import definitions
    seen = {}

    def which(deployments, deployment, cwd, command):
        seen.update(deployment=deployment, cwd=cwd, command=command)
        return "my-app"
    monkeypatch.setattr(definitions, "which", which)
    res = ci_mod.deploy(cwd=tmp_path)
    assert res["ok"] is True and res["deployment"] == "my-app"
    assert seen == {"deployment": None, "cwd": tmp_path, "command": "gapp ci deploy"}


# -- refused before a run is started --


def test_a_name_that_is_not_a_deployment_is_refused(ci):
    hint = _refused_before_a_run(ci, ci_mod.deploy("nope"), "name the deployment")
    assert "'nope' is not a deployment in your fleet" in hint


def test_a_fleet_on_this_machine_is_refused(ci):
    config_mod.set_fleet(repo=None)
    res = ci_mod.deploy("my-app")
    assert res["refused"] is True and res["next_step"]["action"] == "fleet use" and ci.dispatched == []


def test_a_definition_with_no_repo_is_refused_with_the_local_deploy(ci):
    ci.define(FLEET, "local-only", None, "my-proj")
    hint = _refused_before_a_run(ci, ci_mod.deploy("local-only"), "deploy from a clone")
    assert hint == ("'local-only' has no repo, so a runner has nothing to check out. "
                    "Deploy it from a clone of its repo: gapp deploy --deployment local-only")


def test_a_repo_that_is_not_on_github_is_refused_with_the_local_deploy(ci):
    ci.define(FLEET, "elsewhere", "git.example.com/team/app", "my-proj")
    hint = _refused_before_a_run(ci, ci_mod.deploy("elsewhere"), "deploy from a clone")
    assert "runs git.example.com/team/app, which is not a repo on GitHub" in hint
    assert hint.endswith("gapp deploy --deployment elsewhere")


def test_a_project_runners_are_not_connected_to_is_refused_with_connect(ci):
    ci.define(FLEET, "staging", "me/my-app", "other-proj")
    hint = _refused_before_a_run(ci, ci_mod.deploy("staging"), "ci projects connect")
    assert hint == ("Runners in acme/fleet are not connected to other-proj, so nothing was started. "
                    "Connect them, once per project: gapp ci projects connect other-proj")


def test_missing_workflows_are_refused_with_init(ci):
    del ci.files[(FLEET, ".github/workflows/gapp-deploy.yml")]
    hint = _refused_before_a_run(ci, ci_mod.deploy("my-app"), "ci init")
    assert hint == ("acme/fleet has no .github/workflows/gapp-deploy.yml, so there is nothing to start. "
                    "Install the workflows: gapp ci init")


@pytest.mark.parametrize("command", ["deploy", "status"])
def test_an_environment_this_login_cannot_read_still_starts_the_run(ci, command):
    """A token or proxy that may not read Environments (an agent session with
    GitHub access only) still deploys: the runner checks the project's deploy
    identity itself. Only a confirmed absence is refused before a run."""
    ci.fail_with["environments/my-proj"] = "gh: HTTP 403: request blocked"
    res = ci_mod.deploy("my-app") if command == "deploy" else ci_mod.status("my-app", live=True)
    assert res["dispatched"] is True and len(ci.dispatched) == 1
    assert "Couldn't check" not in " ".join(res.get("lines") or [])


# -- what the run said --


def test_a_run_that_refuses_is_a_refusal_in_the_runs_words_with_this_commands_name(ci):
    ci.deploy_outcome = lambda inputs: ("failure", [
        "refused='my-app' was last deployed by gapp 4.0.0 and its definition names 4.1.0. "
        "Deploy it with the upgrade: gapp deploy --deployment my-app --upgrade"])
    res = ci_mod.deploy("my-app")
    assert res["ok"] is False and res["refused"] is True and res["dispatched"] is True
    assert res["next_step"]["hint"] == (
        "The run refused to deploy 'my-app', and nothing was changed: 'my-app' was last deployed by gapp 4.0.0 and "
        "its definition names 4.1.0. Deploy it with the upgrade: gapp ci deploy --deployment my-app --upgrade "
        "(https://github.com/acme/fleet/actions/runs/100)")


def test_a_run_that_fails_reports_gapps_error_line(ci):
    ci.deploy_outcome = lambda inputs: ("failure", ["error=Cloud Build failed: step 2 exited with 1"])
    res = ci_mod.deploy("my-app")
    assert res["ok"] is False and res["refused"] is False and res["next_step"]["action"] == "read the run log"
    assert res["next_step"]["hint"] == (
        "'my-app' did not deploy: Cloud Build failed: step 2 exited with 1 (https://github.com/acme/fleet/actions/runs/100)")


def test_a_run_the_workflow_stopped_reports_what_the_workflow_said(ci):
    said = "runners in this repo are not connected to my-proj, so 'my-app' was not deployed. Run: gapp ci projects connect my-proj"
    ci.deploy_outcome = lambda inputs: ("failure", [said])
    res = ci_mod.deploy("my-app")
    assert res["ok"] is False and res["messages"] == [said]
    assert res["next_step"]["hint"].startswith(f"'my-app' did not deploy: {said}")


def test_the_line_naming_what_the_run_deploys_is_not_taken_for_a_reason(ci):
    ci.deploy_outcome = lambda inputs: ("failure", ["my-app -> me/my-app (the default branch) on my-proj, with gapp 4.0.0"])
    res = ci_mod.deploy("my-app")
    assert res["messages"] == []
    assert res["next_step"]["hint"] == (
        "'my-app' did not deploy: the run ended with 'failure' and printed no reason. "
        "Its log is at https://github.com/acme/fleet/actions/runs/100")


def test_a_ref_the_app_repo_does_not_have_names_setting_one_that_exists(ci):
    ci.define(FLEET, "pinned", "me/my-app", "my-proj", ref="v9.9.9")
    ci.deploy_outcome = lambda inputs: ("failure", [])
    res = ci_mod.deploy("pinned")
    assert res["next_step"]["action"] == "set the ref"
    assert res["next_step"]["hint"] == (
        "'pinned' builds 'v9.9.9', and me/my-app has no branch, tag or commit of that name, so the run could not "
        "check it out (https://github.com/acme/fleet/actions/runs/100). Name one that exists: "
        "gapp set ref <ref> --deployment pinned")


def test_the_scripts_github_echoes_into_the_log_are_never_read_as_messages(ci):
    echoed = 'echo "gapp-deploy: error=this is the script, not what it printed"'
    log = job_log(["gapp-deploy: result={}"], script=echoed)
    assert ci_mod._emitted_messages(log, "gapp-deploy:") == ["result={}"]


# -- a dispatch is tied to the run it created --


def _ago(seconds):
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return then.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_deploy_waits_for_its_own_run_past_an_older_failed_one(ci):
    ci.add_run(90, FLEET, DEPLOY, "Deploy my-app", "failure", job_log(["gapp-deploy: error=an old failure"]), created_at=_ago(600))
    ci.run_appears_after = 3
    res = ci_mod.deploy("my-app")
    assert res["ok"] is True and res["run_id"] == "100" and ci.request_ids == [res["request_id"]]


def test_an_older_run_carrying_the_same_id_is_never_accepted(ci, monkeypatch):
    class Fixed:
        hex = "abc123def456" + "0" * 20
    monkeypatch.setattr(ci_mod.uuid, "uuid4", lambda: Fixed)
    ci.dispatch_creates_run = False
    ci.add_run(90, FLEET, DEPLOY, "Deploy my-app abc123def456", "failure", created_at=_ago(3600))
    monkeypatch.setattr(ci_mod, "RUN_APPEAR_TIMEOUT", 0)
    res = ci_mod.deploy("my-app")
    assert res["ok"] is False and res["run_id"] is None and res["dispatched"] is True
    assert res["next_step"]["action"] == "check the run"
    assert "no run carrying its request id abc123def456 appeared" in res["next_step"]["hint"]
    assert res["next_step"]["hint"].endswith("gapp ci status --deployment my-app")


def test_a_dispatch_github_refuses_is_a_failure_in_githubs_words(ci):
    ci.dispatch_refusal = "gh: HTTP 403: Resource not accessible by integration"
    res = ci_mod.deploy("my-app")
    assert res["ok"] is False and res["dispatched"] is False and res["next_step"]["action"] == "start the deploy run"
    hint = res["next_step"]["hint"]
    assert hint.startswith("The deploy run could not be started in acme/fleet: GitHub returned 403: Resource not accessible by integration.")
    assert hint.endswith("run it again: gapp ci deploy --deployment my-app")


def test_workflows_older_than_this_gapp_are_named_with_init(ci):
    ci.dispatch_refusal = 'gh: HTTP 422: Unexpected inputs provided: ["upgrade"]'
    hint = ci_mod.deploy("my-app")["next_step"]["hint"]
    assert "bring the workflows up to this gapp's with `gapp ci init`" in hint
