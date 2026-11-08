"""`gapp ci setup` registers a solution by dispatching the register workflow
in the CI repo and reporting the run's result.

The machine running it needs neither gcloud nor a GitHub API write: the
run validates and writes `gapp/solutions.yaml` with its own token. The one
exception is the first setup on a project, which creates the Workload
Identity resources and records them as the project's Environment; that
runs only where gcloud is, and a machine without gcloud stops and says so.
"""

import pytest

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
# Assembled at run time so no address literal sits in the source.
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])


def _no_gcloud(*args, **kwargs):
    raise AssertionError("gcloud must not be called on this path")


@pytest.fixture
def wired(monkeypatch, fake_gh):
    """setup_ci against the in-memory GitHub with gapp's files installed,
    every GCP step stubbed, and the solution resolved to my-app."""
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "_ensure_wif_pool", lambda project_id: "exists")
    monkeypatch.setattr(ci_mod, "_ensure_wif_provider", lambda project_id, owner: "exists")
    monkeypatch.setattr(ci_mod, "_ensure_deploy_sa", lambda project_id: "exists")
    monkeypatch.setattr(ci_mod, "_ensure_wif_binding", lambda project_id, ci_repo: "set")
    monkeypatch.setattr(ci_mod, "_get_project_number", lambda project_id: "123456789012")
    monkeypatch.setattr(ci_mod, "require_gcloud_account", lambda: None)
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": "my-app", "project_id": None, "repo_path": None,
    })
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution",
                        lambda self, *a, **kw: {"project_id": "my-proj", "env": None, "labels": {}, "status": "resolved"})
    fake_gh.install(CI_REPO)
    fake_gh.private["me/my-app"] = False
    fake_gh.variables[(CI_REPO, "GAPP_ALLOWED_OWNERS")] = "me, other-org"
    return fake_gh


@pytest.fixture
def no_gcloud(wired, monkeypatch):
    """The same machine with gcloud absent and every path to it exploding."""
    from gapp.admin.sdk.core import GappSDK
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", _no_gcloud)
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa",
                 "_ensure_wif_binding", "_get_project_number", "run_gcloud", "require_gcloud_account"):
        monkeypatch.setattr(ci_mod, step, _no_gcloud)
    monkeypatch.setenv("PATH", "/nonexistent")
    return wired


def _recorded(gh, project="my-proj"):
    gh.environments[(CI_REPO, project)] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}


def test_setup_without_gcloud_or_api_writes_registers_by_dispatching(no_gcloud):
    """The done-when case: a cloud session behind a restrictive proxy."""
    _recorded(no_gcloud)
    no_gcloud.api_writes = False
    no_gcloud.actions_api = False

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj", domain="mcp.example.com")

    assert res["gcloud_used"] is False
    assert res["dispatched"] is True and res["registered"] is True
    assert res["conclusion"] == "success"
    assert no_gcloud.dispatched == [(CI_REPO, "gapp-register.yml", {
        "solution": "my-app", "solution-repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com",
    })]
    assert res["run_id"] == "100" and res["run_url"].endswith("/actions/runs/100")
    assert res["messages"] == ["added 'my-app' -> me/my-app on project 'my-proj'"]
    assert res["next_step"] is None
    assert no_gcloud.puts == []                                    # nothing written from this machine
    assert res["environment_record"] == "exists (variables not checked from this machine)"
    assert no_gcloud.solutions(CI_REPO) == [{"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com"}]


def test_setup_reports_a_refused_register_run(no_gcloud):
    _recorded(no_gcloud)
    no_gcloud.actions_api = False          # the allowlist cannot be read here, so the run decides
    no_gcloud.register_outcome = lambda inputs: ("failure", ["inputs accepted", "refused: owner 'me' is not in GAPP_ALLOWED_OWNERS (other-org)"])

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")

    assert res["dispatched"] is True and res["registered"] is False
    assert res["conclusion"] == "failure"
    assert res["next_step"]["action"] == "fix and re-run ci setup"
    assert "owner 'me' is not in GAPP_ALLOWED_OWNERS" in res["next_step"]["hint"]
    assert res["run_url"] in res["next_step"]["hint"]


def test_setup_without_watch_returns_after_dispatch(no_gcloud):
    _recorded(no_gcloud)
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj", watch=False)
    assert res["dispatched"] is True and res["registered"] is None
    assert res["run_id"] == "100" and res["conclusion"] is None and res["next_step"] is None


def test_setup_without_project_and_without_gcloud_stops_naming_the_flag(no_gcloud):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "pass --project"
    assert "--project <project-id>" in res["next_step"]["hint"]
    assert no_gcloud.dispatched == []


def test_setup_without_the_register_workflow_points_at_ci_init(no_gcloud):
    del no_gcloud.files[(CI_REPO, ci_mod.REGISTER_WORKFLOW_PATH)]
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "ci init"
    assert f"gapp ci init {CI_REPO}" in res["next_step"]["hint"]


def test_setup_on_a_project_without_environment_never_runs_first_time_work_without_gcloud(no_gcloud):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "ci setup with gcloud"
    assert "gapp ci setup --project my-proj" in res["next_step"]["hint"]
    assert "machine with gcloud" in res["next_step"]["hint"]
    assert no_gcloud.env_writes == [] and no_gcloud.dispatched == []


def test_first_setup_on_a_project_runs_gcloud_and_records_the_environment(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["gcloud_used"] is True
    assert res["wif_pool"] == "exists" and res["binding"] == "set"
    assert res["environment_record"] == "created"
    assert wired.environments[(CI_REPO, "my-proj")] == {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    assert res["dispatched"] is True and res["registered"] is True
    assert wired.solutions(CI_REPO)[0]["project"] == "my-proj"


def test_setup_without_project_finds_it_by_label_when_gcloud_is_here(wired):
    _recorded(wired)
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["project_id"] == "my-proj"
    assert res["gcloud_used"] is False                 # the record exists; only the lookup ran
    assert wired.dispatched[0][2]["project"] == "my-proj"


def test_setup_with_project_but_incomplete_record_repairs_it_with_gcloud(wired):
    wired.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["gcloud_used"] is True
    assert res["environment_record"] == "created"
    assert wired.environments[(CI_REPO, "my-proj")]["GAPP_DEPLOY_SERVICE_ACCOUNT"] == SA


def test_setup_with_recorded_environment_makes_no_gcloud_call(wired, monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    _recorded(wired)
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", _no_gcloud)
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa",
                 "_ensure_wif_binding", "_get_project_number", "run_gcloud"):
        monkeypatch.setattr(ci_mod, step, _no_gcloud)
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["gcloud_used"] is False and res["registered"] is True
    assert res["environment_record"] == "exists"
    assert wired.env_writes == []


def test_gcloud_path_fails_by_name_when_the_configured_account_has_no_login(wired, monkeypatch):
    def refuse():
        raise RuntimeError("gapp is configured to use the account deployer@example.com ... gcloud auth login deployer@example.com")

    monkeypatch.setattr(ci_mod, "require_gcloud_account", refuse)
    with pytest.raises(RuntimeError, match="deployer@example.com"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert wired.dispatched == [] and wired.env_writes == []


def test_setup_with_project_verifies_the_project_hosts_the_solution(wired, monkeypatch):
    """Without a record, --project goes through the resolver with that project,
    which refuses a project that never had `gapp setup` for the solution."""
    from gapp.admin.sdk.core import GappSDK
    seen = {}

    def resolve(self, solution_name, env=None, project=None, allow_zero=False):
        seen["project"] = project
        raise RuntimeError("Solution 'my-app' is not deployed on project 'my-proj'. Run setup first.")

    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", resolve)
    with pytest.raises(RuntimeError, match="Run 'gapp setup --project <project-id>' first"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert seen["project"] == "my-proj"


# -- the entry's domain --


def test_rerun_without_domain_keeps_the_entry_domain(no_gcloud):
    _recorded(no_gcloud)
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["domain"] == "mcp.example.com"
    assert no_gcloud.dispatched[-1][2]["domain"] == "mcp.example.com"


def test_rerun_with_empty_domain_removes_it(no_gcloud):
    _recorded(no_gcloud)
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj", domain="")
    assert res["domain"] is None
    assert no_gcloud.dispatched[-1][2]["domain"] == ""
    assert "domain" not in no_gcloud.solutions(CI_REPO)[0]


# -- what this machine can refuse before dispatching --


def test_setup_refuses_a_solution_already_registered_on_another_project(no_gcloud):
    _recorded(no_gcloud)
    _recorded(no_gcloud, "other-proj")
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="other-proj")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "remove the old entry"
    assert "'my-proj'" in res["next_step"]["hint"] and "'other-proj'" in res["next_step"]["hint"]
    assert len(no_gcloud.dispatched) == 1


def test_setup_stops_when_the_owner_is_not_allowed_and_that_is_readable_here(no_gcloud):
    _recorded(no_gcloud)
    no_gcloud.variables[(CI_REPO, "GAPP_ALLOWED_OWNERS")] = "other-org"
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "configure variables"
    assert f"gh variable set GAPP_ALLOWED_OWNERS --repo {CI_REPO} --body other-org,me" in res["next_step"]["hint"]


def test_setup_private_repo_with_credentials_dispatches(no_gcloud):
    _recorded(no_gcloud)
    no_gcloud.private["me/my-app"] = True
    no_gcloud.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    no_gcloud.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["solution_repo_private"] is True
    assert res["app_credentials"]["app_id_present"] is True and res["app_credentials"]["private_key_present"] is True
    assert res["registered"] is True


def test_setup_private_repo_with_a_missing_credential_stops_and_names_it(no_gcloud):
    _recorded(no_gcloud)
    no_gcloud.private["me/my-app"] = True
    no_gcloud.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["dispatched"] is False
    assert res["next_step"]["action"] == "configure hosting app"
    assert "Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY" in res["next_step"]["hint"]


def test_setup_private_repo_dispatches_when_the_checks_cannot_run_here(no_gcloud):
    _recorded(no_gcloud)
    no_gcloud.private["me/my-app"] = True
    no_gcloud.actions_api = False
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["app_credentials"]["checked"] is False
    assert "not checked from this machine" in res["app_credentials"]["note"]
    assert res["dispatched"] is True


def test_setup_fails_when_visibility_cannot_be_read(no_gcloud):
    _recorded(no_gcloud)
    del no_gcloud.private["me/my-app"]
    with pytest.raises(RuntimeError, match="visibility of me/my-app"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")


def test_setup_has_no_per_solution_credential_name_options():
    import inspect
    params = inspect.signature(ci_mod.setup_ci).parameters
    assert "app_id_var" not in params and "app_key_secret" not in params and "out" not in params
    assert "project" in params and "watch" in params
