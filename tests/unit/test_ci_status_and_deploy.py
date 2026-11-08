"""`gapp ci status` is the CI system's complete report and never touches GCP;
`gapp ci deploy` dispatches the generic deploy workflow for a registered
solution. Both return outcomes, never exceptions, for every expected gap.
"""

import shutil

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WIF = "projects/1/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])


def _register(gh, name="my-app", repo="me/my-app", project="my-proj", domain=None, gapp_ref=None):
    doc = yaml.safe_load(gh.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)]) or {}
    entry = {"name": name, "repo": repo, "project": project}
    if domain:
        entry["domain"] = domain
    if gapp_ref:
        entry["gapp_ref"] = gapp_ref
    doc["solutions"] = (doc.get("solutions") or []) + [entry]
    gh.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)] = yaml.dump(doc)


def _everything_set(gh):
    gh.variables[(CI_REPO, "GAPP_ALLOWED_SOURCES")] = "me"
    gh.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    gh.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))


def _no_gcloud(*args, **kwargs):
    raise AssertionError("the CI system must never call gcloud")


@pytest.fixture
def ci(monkeypatch, fake_gh):
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "_resolve_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v3.9.0")
    monkeypatch.setattr(ci_mod, "run_gcloud", _no_gcloud)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": name or "my-app", "project_id": None, "repo_path": None,
    })
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", _no_gcloud)
    fake_gh.install(CI_REPO)
    return fake_gh


# -- status --


def test_status_without_gh_or_repo_is_an_outcome(monkeypatch, fake_gh):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    res = ci_mod.get_ci_status()
    assert res["ok"] is False and res["next_step"]["action"] == "install gh"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(ci_mod, "_resolve_ci_repo", lambda: None)
    res = ci_mod.get_ci_status()
    assert res["ok"] is False and res["repo"] is None and res["next_step"]["action"] == "ci init"


def test_status_is_the_complete_ci_report(ci):
    _everything_set(ci)
    _register(ci, domain="mcp.example.com", gapp_ref="v3.9.1")
    _register(ci, name="other", repo="me/other", project="other-proj")
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    res = ci_mod.get_ci_status()
    assert res["ok"] is True and res["next_step"] is None
    assert res["files"] == {
        ci_mod.DEPLOY_WORKFLOW_PATH: {"present": True, "current": True},
        ci_mod.REGISTER_WORKFLOW_PATH: {"present": True, "current": True},
        ci_mod.SOLUTIONS_FILE: {"present": True, "current": None},
    }
    assert res["variables"] == {"GAPP_ALLOWED_SOURCES": True, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": True}
    assert res["solutions"] == [
        {"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com", "gapp_ref": "v3.9.1", "environment_exists": True},
        {"name": "other", "repo": "me/other", "project": "other-proj", "domain": None, "gapp_ref": None, "environment_exists": False},
    ]
    assert res["environments"] == {"my-proj": True, "other-proj": False}
    assert res["solution"] == "my-app" and res["registered"] is True
    assert res["solution_repo"] == "me/my-app" and res["domain"] == "mcp.example.com" and res["gapp_ref"] == "v3.9.1"
    assert res["environment_record"]["exists"] is True and res["environment_record"]["wif_provider_present"] is True


def test_status_names_missing_and_outdated_files(ci):
    _everything_set(ci)
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    del ci.files[(CI_REPO, ci_mod.REGISTER_WORKFLOW_PATH)]
    res = ci_mod.get_ci_status()
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"
    assert ci_mod.REGISTER_WORKFLOW_PATH in res["next_step"]["hint"]
    ci.install(CI_REPO, gapp_ref="v3.8.0")        # an older install
    _register(ci)
    res = ci_mod.get_ci_status()
    assert res["files"][ci_mod.DEPLOY_WORKFLOW_PATH] == {"present": True, "current": False}
    assert res["next_step"]["action"] == "ci init" and "Not current" in res["next_step"]["hint"]


def test_status_names_the_missing_sources_list(ci):
    res = ci_mod.get_ci_status()
    assert res["variables"]["GAPP_ALLOWED_SOURCES"] is False
    assert res["next_step"]["action"] == "configure variables"
    assert "gapp ci sources add <owner>" in res["next_step"]["hint"]


def test_status_of_an_unregistered_solution_points_at_setup(ci):
    _everything_set(ci)
    res = ci_mod.get_ci_status()
    assert res["registered"] is False
    assert res["next_step"]["action"] == "ci setup"
    assert "gapp ci setup --project <project>" in res["next_step"]["hint"]


def test_status_lists_solutions_still_on_legacy_workflows(ci):
    _everything_set(ci)
    ci.files[(CI_REPO, ".github/workflows/legacy-app.yml")] = yaml.dump({"jobs": {"deploy": {
        "uses": "example-org/gapp/.github/workflows/deploy.yml@v3.8.0", "with": {"solution-repo": "me/legacy-app"}}}})
    ci.files[(CI_REPO, ".github/workflows/unrelated.yml")] = "name: lint\n"
    res = ci_mod.get_ci_status(solution="legacy-app")
    assert res["legacy_workflows"] == ["legacy-app"]
    assert res["registered"] is False
    assert "legacy-app.yml" in res["next_step"]["hint"] and "Actions tab" in res["next_step"]["hint"]


def test_status_names_an_untrusted_project_for_a_registered_solution(ci):
    _everything_set(ci)
    _register(ci)
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["exists"] is False
    assert res["next_step"]["action"] == "ci project setup"
    assert "gapp ci project setup my-proj" in res["next_step"]["hint"]


def test_status_names_a_missing_environment_variable(ci):
    _everything_set(ci)
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["service_account_present"] is False
    assert "GAPP_DEPLOY_SERVICE_ACCOUNT" in res["next_step"]["hint"]
    assert "gapp ci project setup my-proj" in res["next_step"]["hint"]


def test_status_reports_unchecked_instead_of_missing_where_the_actions_api_is_unreadable(ci):
    ci.actions_api = False
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.get_ci_status()
    assert res["variables"] == {"GAPP_ALLOWED_SOURCES": None, "HOSTING_GITHUB_APP_ID": None, "HOSTING_GITHUB_APP_PRIVATE_KEY": None}
    assert res["variables_checked"] is False
    assert res["environment_record"]["exists"] is True and res["environment_record"]["checked"] is False
    assert res["ok"] is True and res["next_step"] is None


def test_status_never_reads_the_solution_repo_or_gcp(ci):
    _everything_set(ci)
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    ci_mod.get_ci_status()
    assert not any(cmd[:3] == ["gh", "api", "repos/me/my-app"] for cmd, _ in ci.calls)


# -- deploy --


def test_deploy_dispatches_the_generic_workflow_with_the_solution(ci):
    _register(ci)
    res = ci_mod.deploy_ci(ref="v1.0.0", watch=False)
    assert res["ok"] is True and res["workflow"] == "gapp-deploy.yml"
    assert ci.dispatched == [(CI_REPO, "gapp-deploy.yml", {"solution": "my-app", "ref": "v1.0.0"})]
    assert res["run_id"] == "100" and res["run_url"].endswith("/actions/runs/100")
    assert res["next_step"] is None


def test_deploy_of_an_unregistered_solution_is_an_outcome(ci):
    res = ci_mod.deploy_ci(watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "ci setup"
    assert "'my-app' is not registered in acme/ci" in res["next_step"]["hint"]
    assert "gapp ci setup --solution my-app --solution-repo <owner>/<repo> --project <project>" in res["next_step"]["hint"]
    assert ci.dispatched == []


def test_deploy_without_the_deploy_workflow_points_at_ci_init(ci):
    _register(ci)
    del ci.files[(CI_REPO, ci_mod.DEPLOY_WORKFLOW_PATH)]
    res = ci_mod.deploy_ci(watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"


def test_deploy_reports_a_refused_dispatch_in_words(ci):
    _register(ci)
    ci.dispatch_refusal = "HTTP 403: Resource not accessible"
    res = ci_mod.deploy_ci(watch=False)
    assert res["ok"] is False and res["next_step"]["action"] == "dispatch the deploy workflow"
    assert "not allowed to do that in acme/ci" in res["next_step"]["hint"] and "HTTP" not in res["next_step"]["hint"]


def test_deploy_watches_and_reports_the_conclusion(ci):
    _register(ci)
    res = ci_mod.deploy_ci(watch=True)
    assert res["ok"] is True and res["watched"] is True and res["conclusion"] == "success"


def test_deploy_reports_a_failed_run_with_its_link(ci):
    _register(ci)
    original = ci._dispatch

    def failing(cmd):
        out = original(cmd)
        for run in ci.runs.values():
            run["conclusion"] = "failure"
        return out

    ci._dispatch = failing
    res = ci_mod.deploy_ci(watch=True)
    assert res["ok"] is False and res["conclusion"] == "failure"
    assert res["next_step"]["action"] == "read the run log"
    assert res["run_url"] in res["next_step"]["hint"]


def test_deploy_without_gh_or_ci_init_or_solution_is_an_outcome(ci, monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert ci_mod.deploy_ci(watch=False)["next_step"]["action"] == "install gh"
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: None)
    assert ci_mod.deploy_ci(watch=False)["next_step"]["action"] == "name the solution"
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: None)
    assert ci_mod.deploy_ci(watch=False)["next_step"]["action"] == "ci init"


def test_trigger_is_the_former_name_of_deploy(ci):
    _register(ci)
    res = ci_mod.trigger_ci(watch=False)
    assert res["ok"] is True and res["workflow"] == "gapp-deploy.yml"
