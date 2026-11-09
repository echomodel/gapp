"""A dispatch is tied to the run it created by a request id at the end of the
run name. gapp waits for that run, never reports an older run's result, and
says so plainly when the run does not appear in time.
"""

import datetime

import pytest

from gapp.admin.sdk import ci as ci_mod

from .conftest import job_log

FLEET = "acme/fleet"
WIF = "projects/1/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])
FIXED_ID = "abc123def456"


def _no_gcloud(*args, **kwargs):
    raise AssertionError("the CI system must never call gcloud")


class _FixedUuid:
    hex = FIXED_ID + "0" * 20


@pytest.fixture
def ci(monkeypatch, fleet_repo, fake_gh):
    monkeypatch.setattr(ci_mod, "run_gcloud", _no_gcloud)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    fake_gh.deploy(FLEET, "my-app", "me/my-app", "my-proj", domain="mcp.example.com")
    fake_gh.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    fake_gh.private["me/my-app"] = fake_gh.private["me/my-other-app"] = False
    fake_gh.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    return fake_gh


def _ago(seconds):
    then = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds)
    return then.strftime("%Y-%m-%dT%H:%M:%SZ")


def _old_failed_run(ci, workflow, name, message, created_at=None):
    """A completed, failed run from before the dispatch: the newest the workflow has."""
    ci.add_run(90, FLEET, workflow, name, "failure",
               job_log([f"{workflow.split('.')[0]}: error={message}"]), created_at=created_at or _ago(600))


# -- an older completed run, and the new run listed only after a few polls --


def test_status_live_waits_for_its_own_run_past_an_older_failed_one(ci):
    _old_failed_run(ci, "gapp-status.yml", "Status my-app", "an old failure")
    ci.run_appears_after = 3
    res = ci_mod.status_live("my-app")
    assert res["ok"] is True and res["run_id"] == "100" and res["conclusion"] == "success"
    assert res["run_url"].endswith("/actions/runs/100") and res["live"]["deployment"]["pending"] is False
    assert ci.request_ids == [res["request_id"]] and res["request_id"]
    assert "an old failure" not in str(res)


def test_deploy_waits_for_its_own_run_past_an_older_failed_one(ci):
    _old_failed_run(ci, "gapp-deploy.yml", "Deploy my-app (main)", "an old failure")
    ci.run_appears_after = 3
    res = ci_mod.deploy_deployment("my-app", watch=True)
    assert res["ok"] is True and res["run_id"] == "100" and res["conclusion"] == "success"


def test_setup_waits_for_its_own_register_run_past_an_older_failed_one(ci):
    _old_failed_run(ci, "gapp-register.yml", "Register my-app", "an old failure")
    ci.run_appears_after = 3
    res = ci_mod.setup_deployment(None, repo="me/my-other-app", project="my-proj")
    assert res["ok"] is True, res["next_step"]
    assert res["run_id"] == "100" and res["result_code"] == "added"
    assert "an old failure" not in str(res)


# -- no run appears in time --


@pytest.mark.parametrize("call", [
    lambda: ci_mod.status_live("my-app"),
    lambda: ci_mod.deploy_deployment("my-app", watch=True),
    lambda: ci_mod.setup_deployment(None, repo="me/my-other-app", project="my-proj"),
])
def test_no_run_within_the_timeout_is_a_plain_outcome_with_no_result(ci, monkeypatch, call):
    monkeypatch.setattr(ci_mod, "RUN_APPEAR_TIMEOUT", 0)
    ci.dispatch_creates_run = False
    res = call()
    assert res["ok"] is False and res["run_id"] is None and res["next_step"]["action"] == "check the run"
    assert f"no run carrying its request id {res['request_id']} appeared within 0 seconds" in res["next_step"]["hint"]
    assert res.get("conclusion") is None and res.get("live") is None


# -- only older runs exist: never reported --


@pytest.mark.parametrize("workflow,name,call", [
    ("gapp-status.yml", "Status my-app", lambda: ci_mod.status_live("my-app")),
    ("gapp-deploy.yml", "Deploy my-app (main)", lambda: ci_mod.deploy_deployment("my-app", watch=True)),
    ("gapp-register.yml", "Register my-app", lambda: ci_mod.setup_deployment(None, repo="me/my-other-app", project="my-proj")),
])
def test_an_older_run_is_never_reported_even_one_carrying_the_same_id(ci, monkeypatch, workflow, name, call):
    monkeypatch.setattr(ci_mod, "RUN_APPEAR_TIMEOUT", 0)
    monkeypatch.setattr(ci_mod.uuid, "uuid4", lambda: _FixedUuid())
    ci.dispatch_creates_run = False
    _old_failed_run(ci, workflow, name, "an old failure")
    ci.add_run(91, FLEET, workflow, f"{name} {FIXED_ID}", "failure",
               job_log(["gapp-status: error=a stale failure"]), created_at=_ago(3600))
    res = call()
    assert res["request_id"] == FIXED_ID
    assert res["ok"] is False and res["run_id"] is None and res["next_step"]["action"] == "check the run"
    assert "an old failure" not in str(res) and "a stale failure" not in str(res)


def test_a_run_created_just_before_the_dispatch_by_the_clock_skew_is_accepted(ci, monkeypatch):
    sent = datetime.datetime.now(datetime.timezone.utc)
    ci.add_run(95, FLEET, "gapp-status.yml", f"Status my-app {FIXED_ID}", "success",
               created_at=(sent - datetime.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    dispatch = ci_mod.Dispatch(FIXED_ID, sent)
    assert ci_mod._find_run(FLEET, "gapp-status.yml", dispatch, timeout=0) == "95"
    assert ci_mod._find_run(FLEET, "gapp-status.yml", ci_mod.Dispatch("someotherid0", sent), timeout=0) is None


# -- a fleet repo whose workflows predate the request id --


def test_a_fleet_workflow_without_the_request_id_input_points_at_ci_init(ci):
    ci.dispatch_refusal = 'gh: Unexpected inputs provided: ["request_id"] (HTTP 422)'
    res = ci_mod.deploy_deployment("my-app", watch=False)
    assert res["ok"] is False and ci.runs == {}
    assert f"update gapp's files with `gapp ci init {FLEET}`" in res["next_step"]["hint"]
