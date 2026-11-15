"""`gapp ci status` reads GitHub alone: the definition and the newest deploy
run for it. `--live` starts the status workflow, in which a runner asks the
project what is running. Neither writes anything."""

import json

from gapp import __version__
from gapp.admin.sdk import ci as ci_mod

from .conftest import job_log

FLEET = "acme/fleet"
STATUS = "gapp-status.yml"


def test_status_gives_the_definition_and_says_when_nothing_has_run(ci):
    res = ci_mod.status("my-app")
    assert res["ok"] is True and res["dispatched"] is False and ci.dispatched == [] and ci.puts == []
    assert (res["repo"], res["project"], res["ref"], res["gapp"]) == ("me/my-app", "my-proj", None, __version__)
    assert res["last_deploy"] == {"checked": True, "run": None, "note": None, "searched_all": True}
    assert res["lines"] == [f"my-app: the default branch of me/my-app, on my-proj, with gapp {__version__}.", "No deploy run yet."]
    assert res["next_step"] == {"action": "ci deploy", "hint": "gapp ci deploy --deployment my-app"}


def test_status_shows_the_newest_deploy_run_of_that_deployment_only(ci):
    ci.define(FLEET, "my-app-2", "me/my-app", "my-proj")
    ci.add_run(90, FLEET, "gapp-deploy.yml", "Deploy my-app aaa", "failure", created_at="2026-01-01T00:00:00Z")
    ci.add_run(91, FLEET, "gapp-deploy.yml", "Deploy my-app bbb", "success", created_at="2026-01-02T00:00:00Z")
    ci.add_run(92, FLEET, "gapp-deploy.yml", "Deploy my-app-2 ccc", "failure", created_at="2026-01-03T00:00:00Z")
    res = ci_mod.status("my-app")
    assert res["last_deploy"]["run"] == {"id": "91", "status": "completed", "conclusion": "success",
                                         "created_at": "2026-01-02T00:00:00Z",
                                         "url": "https://github.com/acme/fleet/actions/runs/91"}
    assert res["lines"][1] == "Last deploy run: success, 2026-01-02T00:00:00Z: https://github.com/acme/fleet/actions/runs/91"
    assert res["next_step"]["hint"] == "gapp ci status --deployment my-app --live"


def test_a_deploy_older_than_a_page_of_other_runs_is_still_found(ci):
    ci.add_run(10, FLEET, "gapp-deploy.yml", "Deploy my-app aaa", "success", created_at="2026-01-01T00:00:00Z")
    for i in range(11, 11 + 250):
        ci.add_run(i, FLEET, "gapp-deploy.yml", f"Deploy other-{i} x", "success")
    res = ci_mod.status("my-app")
    assert res["last_deploy"]["run"]["id"] == "10" and res["last_deploy"]["searched_all"] is True


def test_the_search_for_a_deploy_stops_at_a_bound_and_says_so(ci, monkeypatch):
    monkeypatch.setattr(ci_mod, "RUNS_SEARCHED", 200)
    ci.add_run(10, FLEET, "gapp-deploy.yml", "Deploy my-app aaa", "success")
    for i in range(11, 11 + 250):
        ci.add_run(i, FLEET, "gapp-deploy.yml", f"Deploy other-{i} x", "success")
    res = ci_mod.status("my-app")
    assert res["last_deploy"] == {"checked": True, "run": None, "note": None, "searched_all": False}
    assert res["lines"][1] == "No deploy run among the newest 200 deploy runs of acme/fleet."
    assert res["next_step"]["hint"] == "gapp ci status --deployment my-app --live"


def test_status_says_when_the_runs_could_not_be_read(ci):
    ci.fail_with["actions/workflows/gapp-deploy.yml/runs"] = "gh: HTTP 403: request blocked"
    res = ci_mod.status("my-app")
    assert res["ok"] is True and res["last_deploy"]["checked"] is False
    assert res["lines"][1].startswith("Last deploy run: Couldn't check the deploy runs of acme/fleet: GitHub returned 403")


def test_status_of_a_name_that_is_not_a_deployment_is_refused(ci):
    res = ci_mod.status("nope")
    assert res["refused"] is True and "'nope' is not a deployment in your fleet" in res["next_step"]["hint"]


def test_live_starts_the_status_workflow_and_reports_what_the_runner_found(ci):
    said = []
    res = ci_mod.status("my-app", live=True, progress=said.append)
    assert [(w, i) for _, w, i in ci.dispatched] == [(STATUS, {"deployment": "my-app"})]
    assert said == ["Asking my-proj what is live for my-app. This takes about as long as a runner start."]
    assert res["ok"] is True and res["conclusion"] == "success" and res["live"]["healthy"] is True
    assert res["lines"] == [
        "my-app, on my-proj",
        "  URL      https://my-app-abc-uc.a.run.app",
        "  Healthy  yes",
        f"  Deployed by gapp {__version__}",
        "Run: https://github.com/acme/fleet/actions/runs/100",
    ]


def test_live_shows_a_domain_waiting_on_dns_with_the_record_to_add(ci):
    live = {"deployment": "my-app", "project": "my-proj", "deployed": True, "url": "https://x.run.app", "healthy": True,
            "last_deployed_by": "4.0.0", "next_step": None,
            "domain": {"name": "mcp.example.com", "status": "pending_dns", "cname_target": "ghs.googlehosted.com."}}
    ci.status_outcome = lambda inputs: ("success", ["result=" + json.dumps(live)])
    lines = ci_mod.status("my-app", live=True)["lines"]
    assert "  Domain   mcp.example.com (pending_dns)" in lines
    assert "    CNAME  mcp.example.com -> ghs.googlehosted.com." in lines


def test_live_of_a_deployment_that_was_never_deployed_says_so(ci):
    live = {"deployment": "my-app", "project": "my-proj", "deployed": False, "url": None, "healthy": None,
            "domain": None, "next_step": "Not deployed yet. Deploy it: gapp deploy --deployment my-app"}
    ci.status_outcome = lambda inputs: ("success", ["result=" + json.dumps(live)])
    res = ci_mod.status("my-app", live=True)
    assert res["ok"] is True and res["lines"][:2] == ["my-app, on my-proj", "Not deployed yet. Deploy it: gapp deploy --deployment my-app"]


def test_live_is_refused_before_a_run_when_runners_are_not_connected_or_the_workflow_is_missing(ci):
    ci.define(FLEET, "staging", "me/my-app", "other-proj")
    res = ci_mod.status("staging", live=True)
    assert res["refused"] is True and res["next_step"]["action"] == "ci projects connect" and ci.dispatched == []
    del ci.files[(FLEET, ".github/workflows/gapp-status.yml")]
    res = ci_mod.status("my-app", live=True)
    assert res["refused"] is True and res["next_step"]["action"] == "ci init" and ci.dispatched == []


def test_live_works_for_a_deployment_with_no_repo(ci):
    ci.define(FLEET, "local-only", None, "my-proj")
    assert ci_mod.status("local-only", live=True)["ok"] is True
    assert ci_mod.status("local-only")["lines"][0].startswith("local-only: the default branch of no repo, on my-proj")


def test_a_live_run_that_fails_reports_gapps_error_line(ci):
    ci.status_outcome = lambda inputs: ("failure", ["error=Permission denied reading the service"])
    res = ci_mod.status("my-app", live=True)
    assert res["ok"] is False and res["live"] is None and res["next_step"]["action"] == "read the run log"
    assert res["next_step"]["hint"] == (
        "The status run for 'my-app' did not report what is live: Permission denied reading the service "
        "(https://github.com/acme/fleet/actions/runs/100)")


def test_a_live_run_that_says_nothing_points_at_its_log(ci):
    ci.status_outcome = lambda inputs: ("failure", [])
    hint = ci_mod.status("my-app", live=True)["next_step"]["hint"]
    assert hint == ("The status run for 'my-app' ended with 'failure' and reported nothing. "
                    "Its log is at https://github.com/acme/fleet/actions/runs/100")


def test_an_older_failed_status_run_is_never_reported_as_this_one(ci):
    ci.add_run(90, FLEET, STATUS, "Status my-app", "failure", job_log(["gapp-status: error=an old failure"]),
               created_at="2026-01-01T00:00:00Z")
    ci.run_appears_after = 2
    res = ci_mod.status("my-app", live=True)
    assert res["ok"] is True and res["run_id"] == "100"
