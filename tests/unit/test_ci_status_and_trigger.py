"""`gapp ci status` reads the CI repo's solutions file and names the gap;
`gapp ci trigger` dispatches the generic deploy workflow with a solution
name and refuses up front what the run would refuse.
"""

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WIF = "projects/1/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])


def _register(gh, name="my-app", repo="me/my-app", project="my-proj", domain=None):
    doc = yaml.safe_load(gh.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)]) or {}
    entry = {"name": name, "repo": repo, "project": project}
    if domain:
        entry["domain"] = domain
    doc["solutions"] = (doc.get("solutions") or []) + [entry]
    gh.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)] = yaml.dump(doc)


def _everything_set(gh):
    gh.variables[(CI_REPO, "GAPP_ALLOWED_OWNERS")] = "me"
    gh.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    gh.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))


@pytest.fixture
def ci(monkeypatch, fake_gh):
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "_resolve_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": name or "my-app", "project_id": None, "repo_path": None,
    })
    fake_gh.install(CI_REPO)
    fake_gh.private["me/my-app"] = False
    return fake_gh


# -- status --


def test_status_without_repo_points_at_init(monkeypatch, fake_gh):
    monkeypatch.setattr(ci_mod, "_resolve_ci_repo", lambda: None)
    res = ci_mod.get_ci_status()
    assert res["repo"] is None and res["next_step"]["action"] == "ci init"


def test_status_lists_files_variables_and_solutions(ci):
    _everything_set(ci)
    _register(ci, domain="mcp.example.com")
    _register(ci, name="other", repo="me/other", project="other-proj")
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    res = ci_mod.get_ci_status()
    assert res["files"] == {ci_mod.DEPLOY_WORKFLOW_PATH: True, ci_mod.REGISTER_WORKFLOW_PATH: True, ci_mod.SOLUTIONS_FILE: True}
    assert res["variables"] == {"GAPP_ALLOWED_OWNERS": True, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": True}
    assert res["solutions"] == [
        {"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com", "gapp_ref": None, "environment_exists": True},
        {"name": "other", "repo": "me/other", "project": "other-proj", "domain": None, "gapp_ref": None, "environment_exists": False},
    ]
    assert res["solution"] == "my-app" and res["registered"] is True and res["workflow"] is True
    assert res["solution_repo"] == "me/my-app" and res["solution_repo_private"] is False
    assert res["domain"] == "mcp.example.com" and res["environment"] == "my-proj"
    assert res["environment_record"]["exists"] is True and res["environment_record"]["wif_provider_present"] is True
    assert res["next_step"] is None


def test_status_names_missing_files(ci):
    _everything_set(ci)
    del ci.files[(CI_REPO, ci_mod.REGISTER_WORKFLOW_PATH)]
    res = ci_mod.get_ci_status()
    assert res["files"][ci_mod.REGISTER_WORKFLOW_PATH] is False
    assert res["next_step"]["action"] == "ci init"
    assert ci_mod.REGISTER_WORKFLOW_PATH in res["next_step"]["hint"]


def test_status_names_the_missing_allowlist_before_anything_else(ci):
    res = ci_mod.get_ci_status()
    assert res["variables"]["GAPP_ALLOWED_OWNERS"] is False
    assert res["next_step"]["action"] == "configure variables"
    assert "GAPP_ALLOWED_OWNERS" in res["next_step"]["hint"]


def test_status_of_an_unregistered_solution_points_at_setup(ci):
    _everything_set(ci)
    res = ci_mod.get_ci_status()
    assert res["registered"] is False and res["workflow"] is False
    assert res["next_step"]["action"] == "ci setup"
    assert "gapp ci setup --project <project-id>" in res["next_step"]["hint"]


def test_status_lists_solutions_still_on_their_own_workflow(ci):
    _everything_set(ci)
    ci.files[(CI_REPO, ".github/workflows/legacy-app.yml")] = yaml.dump({"jobs": {"deploy": {
        "uses": "example-org/gapp/.github/workflows/deploy.yml@v3.8.0", "with": {"solution-repo": "me/legacy-app"}}}})
    ci.files[(CI_REPO, ".github/workflows/unrelated.yml")] = "name: lint\n"
    res = ci_mod.get_ci_status(solution="legacy-app")
    assert res["legacy_workflows"] == ["legacy-app"]
    assert res["registered"] is False and res["workflow"] is True
    assert "legacy-app.yml" in res["next_step"]["hint"] and "keeps working" in res["next_step"]["hint"]


def test_status_names_a_registered_solution_whose_environment_is_missing(ci):
    _everything_set(ci)
    _register(ci)
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["exists"] is False
    assert res["next_step"]["action"] == "ci setup with gcloud"
    assert "gapp ci setup --project my-proj" in res["next_step"]["hint"]


def test_status_names_a_missing_environment_variable(ci):
    _everything_set(ci)
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["service_account_present"] is False
    assert "GAPP_DEPLOY_SERVICE_ACCOUNT" in res["next_step"]["hint"]


def test_status_private_repo_missing_secret_names_it(ci):
    ci.variables[(CI_REPO, "GAPP_ALLOWED_OWNERS")] = "me"
    ci.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    ci.private["me/my-app"] = True
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    res = ci_mod.get_ci_status()
    assert res["solution_repo_private"] is True
    assert res["app_credentials"]["private_key_present"] is False
    assert res["next_step"]["action"] == "configure hosting app"
    assert f"gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo {CI_REPO}" in res["next_step"]["hint"]


def test_status_reports_unchecked_instead_of_missing_where_the_actions_api_is_unreadable(ci):
    ci.actions_api = False
    ci.private["me/my-app"] = True
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.get_ci_status()
    assert res["variables"] == {"GAPP_ALLOWED_OWNERS": None, "HOSTING_GITHUB_APP_ID": None, "HOSTING_GITHUB_APP_PRIVATE_KEY": None}
    assert res["variables_checked"] is False
    assert res["environment_record"]["exists"] is True and res["environment_record"]["checked"] is False
    assert res["app_credentials"]["checked"] is False
    assert res["next_step"] is None


def test_status_reports_unreadable_visibility_instead_of_guessing(ci):
    _everything_set(ci)
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    del ci.private["me/my-app"]
    res = ci_mod.get_ci_status()
    assert res["solution_repo_private"] is None
    assert res["next_step"]["action"] == "check solution repo"


# -- trigger --


def test_trigger_dispatches_the_generic_workflow_with_the_solution(ci):
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.trigger_ci(ref="v1.0.0", watch=False)
    assert res["workflow"] == "gapp-deploy.yml" and res["legacy_workflow"] is False
    assert ci.dispatched == [(CI_REPO, "gapp-deploy.yml", {"solution": "my-app", "ref": "v1.0.0"})]
    assert res["run_id"] == "100" and res["run_url"].endswith("/actions/runs/100")
    assert res["next_step"] is None


def test_trigger_refuses_an_unregistered_solution(ci):
    with pytest.raises(RuntimeError, match="not registered"):
        ci_mod.trigger_ci(watch=False)
    assert ci.dispatched == []


def test_trigger_refuses_when_the_project_environment_is_missing(ci):
    _register(ci)
    with pytest.raises(RuntimeError, match="no Environment of that name"):
        ci_mod.trigger_ci(watch=False)
    assert ci.dispatched == []


def test_trigger_refuses_a_private_repo_whose_credential_is_missing(ci):
    ci.private["me/my-app"] = True
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    with pytest.raises(RuntimeError, match="HOSTING_GITHUB_APP_ID"):
        ci_mod.trigger_ci(watch=False)
    assert ci.dispatched == []


def test_trigger_dispatches_a_private_repo_when_the_check_cannot_run_here(ci):
    ci.private["me/my-app"] = True
    ci.actions_api = False
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.trigger_ci(watch=False)
    assert res["run_id"] == "100" and len(ci.dispatched) == 1


def test_trigger_falls_back_to_a_solutions_own_workflow_and_says_to_register(ci):
    ci.files[(CI_REPO, ".github/workflows/legacy-app.yml")] = "name: Deploy legacy-app\n"
    res = ci_mod.trigger_ci(solution="legacy-app", ref="main", watch=False)
    assert res["workflow"] == "legacy-app.yml" and res["legacy_workflow"] is True
    assert ci.dispatched == [(CI_REPO, "legacy-app.yml", {"ref": "main"})]
    assert res["next_step"]["action"] == "ci setup"


def test_trigger_watches_and_reports_the_conclusion(ci):
    _register(ci)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.trigger_ci(watch=True)
    assert res["watched"] is True and res["conclusion"] == "success"
