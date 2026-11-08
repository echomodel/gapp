"""`gapp ci setup` registers a solution by dispatching the register workflow
and never uses gcloud.

Every expected failure is an outcome (`ok` False, `next_step` with the exact
step), never an exception, a raw HTTP error or a raw gcloud error. The two
one-time steps come back as the verbatim messages the operator reads.
"""

import shutil

import pytest

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
# Assembled at run time so no address literal sits in the source.
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])

PROJECT_NOT_TRUSTED = (
    "CI repo acme/ci is not yet trusted to deploy to GCP project my-proj. One-time setup:\n"
    "\n"
    "1. Run `gapp ci project setup my-proj`\n"
    "   - Requires gcloud signed in as an Owner of my-proj\n"
    "   - Requires `gh` signed in as an admin of acme/ci\n"
    "2. Run this command again: gapp ci setup --project my-proj --solution my-app --solution-repo me/my-app"
)
SOURCE_NOT_ALLOWED = (
    "Source repos owned by me are not yet allowed to deploy through CI repo acme/ci. One-time setup:\n"
    "\n"
    "1. Run `gapp ci sources add me`\n"
    "   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo acme/ci --body <current-list>,me\n"
    "   - Requires `gh` signed in with write access to acme/ci\n"
    "2. Run this command again: gapp ci setup --project my-proj --solution my-app --solution-repo me/my-app"
)


def _no_gcloud(*args, **kwargs):
    raise AssertionError("gcloud must never be called by ci setup")


@pytest.fixture
def wired(monkeypatch, fake_gh):
    """setup_ci against the in-memory GitHub with gapp's files installed and
    the project trusted; every path to gcloud explodes."""
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa",
                 "_ensure_wif_binding", "_get_project_number", "run_gcloud", "require_gcloud_account"):
        monkeypatch.setattr(ci_mod, step, _no_gcloud)
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": "my-app", "project_id": None, "repo_path": None,
    })
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", _no_gcloud)
    monkeypatch.setenv("PATH", "/nonexistent")                     # no gcloud on this machine
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    fake_gh.install(CI_REPO)
    fake_gh.private["me/my-app"] = False
    fake_gh.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    return fake_gh


def _setup(**kw):
    kw.setdefault("solution", "my-app")
    kw.setdefault("solution_repo", "me/my-app")
    kw.setdefault("project", "my-proj")
    return ci_mod.setup_ci(**kw)


def test_setup_registers_without_gcloud_or_api_writes(wired):
    """The cloud-session case: no gcloud, API writes refused, Actions API unreadable."""
    wired.api_writes = False
    wired.actions_api = False
    res = _setup(domain="mcp.example.com")
    assert res["ok"] is True and res["registered"] is True
    assert res["dispatched"] is True and res["conclusion"] == "success" and res["result_code"] == "added"
    assert wired.dispatched == [(CI_REPO, "gapp-register.yml", {
        "solution": "my-app", "solution-repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com",
    })]
    assert res["run_url"].endswith("/actions/runs/100")
    assert res["next_step"] is None
    assert wired.puts == []                                        # nothing written from this machine
    assert wired.solutions(CI_REPO) == [{"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com"}]


def test_setup_makes_no_variables_read_before_dispatching(wired):
    """The allowlist is the register run's to check; no extra read, nothing in run logs."""
    _setup()
    assert not any("/actions/variables" in cmd[2] for cmd, _ in wired.calls if cmd[:2] == ["gh", "api"] and len(cmd) > 2)


def test_setup_without_project_stops(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "pass --project"
    assert "gapp ci setup --project <project>" in res["next_step"]["hint"]
    assert "never looks the project up in GCP" in res["next_step"]["hint"]


def test_setup_without_gh_stops(wired, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "install gh"
    assert "gh auth login" in res["next_step"]["hint"]


def test_setup_without_ci_init_stops(wired, monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: None)
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"
    assert "gapp ci init <owner>/<repo>" in res["next_step"]["hint"]


def test_setup_without_the_register_workflow_points_at_ci_init(wired):
    del wired.files[(CI_REPO, ci_mod.REGISTER_WORKFLOW_PATH)]
    res = _setup()
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "ci init"
    assert f"gapp ci init {CI_REPO}" in res["next_step"]["hint"]


def test_setup_on_an_untrusted_project_prints_the_message_verbatim(wired):
    del wired.environments[(CI_REPO, "my-proj")]
    res = _setup()
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "ci project setup"
    assert res["next_step"]["hint"] == PROJECT_NOT_TRUSTED
    assert wired.env_writes == [] and wired.dispatched == []


def test_setup_repeats_the_users_exact_command_line(wired):
    del wired.environments[(CI_REPO, "my-proj")]
    res = _setup(command="gapp ci setup -s my-app --project my-proj")
    assert res["next_step"]["hint"].endswith("2. Run this command again: gapp ci setup -s my-app --project my-proj")


def test_setup_refuses_a_solution_registered_on_another_project(wired):
    wired.environments[(CI_REPO, "other-proj")] = {}
    _setup(project="other-proj")
    res = _setup()
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "remove the old entry"
    assert "'other-proj'" in res["next_step"]["hint"] and "'my-proj'" in res["next_step"]["hint"]
    assert "gapp/solutions.yaml" in res["next_step"]["hint"]
    assert len(wired.dispatched) == 1


def test_setup_reports_a_refused_dispatch_in_words(wired):
    wired.dispatch_refusal = "HTTP 403: Resource not accessible by integration"
    res = _setup()
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "dispatch the register workflow"
    assert "not allowed to do that in acme/ci" in res["next_step"]["hint"]
    assert "HTTP" not in res["next_step"]["hint"]
    assert "gh auth login" in res["next_step"]["hint"]


def test_setup_when_the_source_owner_is_not_allowed_prints_the_message_verbatim(wired):
    wired.register_outcome = lambda inputs: ("failure", [
        "result=source-owner-not-allowed",
        "Source repos owned by me are not allowed to deploy through this CI repo; allow the owner with: gapp ci sources add me",
    ])
    res = _setup()
    assert res["ok"] is False and res["registered"] is False
    assert res["result_code"] == "source-owner-not-allowed"
    assert res["next_step"]["action"] == "ci sources add"
    assert res["next_step"]["hint"] == SOURCE_NOT_ALLOWED
    assert "<current-list>" in res["next_step"]["hint"]            # rendered literally, by design


def test_setup_prints_other_register_refusals_verbatim_with_the_run_link(wired):
    wired.register_outcome = lambda inputs: ("failure", [
        "result=repo-unreachable",
        "me/my-app is not readable with this run's token (private, or it does not exist), and the hosting app's HOSTING_GITHUB_APP_ID variable and HOSTING_GITHUB_APP_PRIVATE_KEY secret are not both set in this CI repo.",
    ])
    res = _setup()
    assert res["ok"] is False and res["result_code"] == "repo-unreachable"
    assert res["next_step"]["action"] == "fix and run ci setup again"
    assert "me/my-app is not readable with this run's token" in res["next_step"]["hint"]
    assert res["run_url"] in res["next_step"]["hint"]
    assert res["next_step"]["hint"].endswith("gapp ci setup --project my-proj --solution my-app --solution-repo me/my-app")


def test_setup_reports_a_run_that_did_not_finish_with_its_link(wired, monkeypatch):
    monkeypatch.setattr(ci_mod, "_wait_for_run", lambda ci_repo, run_id, timeout=900: ("in_progress", None))
    res = _setup()
    assert res["ok"] is False and res["registered"] is None
    assert res["next_step"]["action"] == "check the run"
    assert res["run_url"] in res["next_step"]["hint"]


def test_setup_reports_a_failed_run_without_a_result_code(wired):
    wired.register_outcome = lambda inputs: ("failure", [])
    res = _setup()
    assert res["ok"] is False and res["result_code"] is None
    assert "ended with no message" in res["next_step"]["hint"] and res["run_url"] in res["next_step"]["hint"]


def test_setup_without_watch_returns_after_dispatch(wired):
    res = _setup(watch=False)
    assert res["ok"] is True and res["dispatched"] is True and res["registered"] is None
    assert res["run_id"] == "100" and res["next_step"] is None


def test_setup_names_the_solution_repo_when_it_cannot_be_read(wired, monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    monkeypatch.setattr(GappSDK, "discover_github_repo", lambda self, ctx: None)
    res = ci_mod.setup_ci(solution="my-app", project="my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "name the solution repo"
    assert "--solution-repo <owner>/<repo>" in res["next_step"]["hint"]


# -- the entry's domain --


def test_rerun_without_domain_keeps_the_entry_domain(wired):
    _setup(domain="mcp.example.com")
    res = _setup()
    assert res["domain"] == "mcp.example.com"
    assert wired.dispatched[-1][2]["domain"] == "mcp.example.com"


def test_rerun_with_empty_domain_removes_it(wired):
    _setup(domain="mcp.example.com")
    res = _setup(domain="")
    assert res["domain"] is None
    assert wired.dispatched[-1][2]["domain"] == ""
    assert "domain" not in wired.solutions(CI_REPO)[0]


def test_setup_has_no_gcloud_era_parameters():
    import inspect
    params = inspect.signature(ci_mod.setup_ci).parameters
    assert "out" not in params and "app_id_var" not in params
    assert set(params) == {"solution", "solution_repo", "project", "domain", "watch", "command"}
