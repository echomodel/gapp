"""The caller workflow `gapp ci setup` writes into the CI repo, and the
per-project deploy identity it records there.

Every generated workflow deploys through the CI repo's GitHub Environment
named after the project id (`environment: <project-id>`); the reusable
workflow reads `GAPP_WIF_PROVIDER` and `GAPP_DEPLOY_SERVICE_ACCOUNT` from
that Environment, so the caller carries no per-project literals. A private
solution repo additionally passes the hosting GitHub App's fixed Actions
variable and secret. Re-running setup without a domain keeps the one
already in the workflow.

The first setup on a project needs gcloud and records the Environment;
with `project` given and the Environment complete, setup makes no GCP
call at all.
"""

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WORKFLOW_PATH = ".github/workflows/my-app.yml"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
# Assembled at run time so no address literal sits in the source.
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])
OTHER_SA = "@".join(["other", "my-proj.iam.gserviceaccount.com"])


@pytest.fixture
def pinned(monkeypatch):
    """Pin the one lookup `_generate_workflow` makes against GitHub."""
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v3.9.0")


def _generate(**kwargs):
    return yaml.safe_load(ci_mod._generate_workflow(
        "my-app", "me/my-app", "my-proj", "example-org/gapp", **kwargs,
    ))


def test_workflow_deploys_through_the_project_environment(pinned):
    doc = _generate()
    job = doc["jobs"]["deploy"]
    assert job["uses"] == "example-org/gapp/.github/workflows/deploy.yml@v3.9.0"
    assert job["with"]["solution-repo"] == "me/my-app"
    assert job["with"]["environment"] == "my-proj"
    # No per-project literals: the Environment holds the identity.
    assert "workload-identity-provider" not in job["with"]
    assert "service-account" not in job["with"]
    assert "solution-repo-app-id" not in job["with"]
    assert "domain" not in job["with"]
    assert "secrets" not in job


def test_domain_is_written_as_the_domain_input(pinned):
    doc = _generate(domain="mcp.example.com")
    assert doc["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"
    assert "secrets" not in doc["jobs"]["deploy"]


def test_private_repo_workflow_uses_the_fixed_app_names(pinned):
    doc = _generate(private=True)
    job = doc["jobs"]["deploy"]
    assert job["with"]["solution-repo-app-id"] == "${{ vars.HOSTING_GITHUB_APP_ID }}"
    assert job["secrets"] == {"solution-repo-app-private-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}


def test_parse_workflow_reads_back_what_generate_wrote(pinned):
    content = ci_mod._generate_workflow(
        "my-app", "me/my-app", "my-proj", "example-org/gapp",
        domain="mcp.example.com", private=True,
    )
    assert ci_mod._parse_workflow(content) == {
        "solution_repo": "me/my-app", "domain": "mcp.example.com", "environment": "my-proj",
        "app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
    }
    assert ci_mod._parse_workflow(None) == {
        "solution_repo": None, "domain": None, "environment": None,
        "app_id_var": None, "app_key_secret": None,
    }
    assert ci_mod._parse_workflow("not: [valid")["solution_repo"] is None


# -- the Environment record --


def test_environment_record_round_trip(fake_gh):
    assert ci_mod._read_environment_record(CI_REPO, "my-proj") is None
    assert ci_mod._write_environment_record(CI_REPO, "my-proj", WIF, SA) == "created"
    assert ci_mod._read_environment_record(CI_REPO, "my-proj") == {"wif_provider": WIF, "service_account": SA}
    assert ci_mod._write_environment_record(CI_REPO, "my-proj", WIF, SA) == "unchanged"
    assert ci_mod._write_environment_record(CI_REPO, "my-proj", WIF, OTHER_SA) == "updated"
    assert fake_gh.environments[(CI_REPO, "my-proj")]["GAPP_DEPLOY_SERVICE_ACCOUNT"].startswith("other@")


def test_environment_record_is_incomplete_without_both_variables(fake_gh):
    fake_gh.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    assert ci_mod._read_environment_record(CI_REPO, "my-proj") is None
    assert ci_mod._environment_record_status(CI_REPO, "my-proj") == {
        "wif_provider_present": True, "service_account_present": False,
    }


# -- setup_ci end to end, against the in-memory GitHub --


def _no_gcloud(*args, **kwargs):
    raise AssertionError("gcloud must not be called on this path")


@pytest.fixture
def wired(monkeypatch, pinned, fake_gh):
    """setup_ci with every GCP step stubbed and the solution resolved to my-app."""
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
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
    fake_gh.private["me/my-app"] = False
    return fake_gh


def _written(gh):
    return yaml.safe_load(gh.files[(CI_REPO, WORKFLOW_PATH)])


def test_first_setup_runs_gcloud_and_records_the_environment(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["gcloud_used"] is True
    assert res["environment"] == "my-proj"
    assert res["environment_record"] == "created"
    assert wired.environments[(CI_REPO, "my-proj")] == {
        "GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA,
    }
    job = _written(wired)["jobs"]["deploy"]
    assert job["with"]["environment"] == "my-proj"
    assert "workload-identity-provider" not in job["with"]
    assert res["app_credentials"] is None and res["next_step"] is None


def test_setup_with_project_and_recorded_environment_needs_no_gcloud(wired, monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    wired.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    # Every path to gcloud explodes: project discovery, the WIF steps, the project number.
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution", _no_gcloud)
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa",
                 "_ensure_wif_binding", "_get_project_number"):
        monkeypatch.setattr(ci_mod, step, _no_gcloud)
    monkeypatch.setattr(ci_mod, "run_gcloud", _no_gcloud)
    monkeypatch.setenv("PATH", "/nonexistent")   # gcloud absent from PATH

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")

    assert res["gcloud_used"] is False
    assert res["project_id"] == "my-proj"
    assert res["environment_record"] == "used"
    assert res["wif_pool"] == "recorded"
    assert _written(wired)["jobs"]["deploy"]["with"]["environment"] == "my-proj"
    assert [w for w in wired.env_writes] == []   # the record is read, never rewritten


def test_setup_with_project_but_incomplete_record_falls_back_to_gcloud(wired):
    wired.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF}
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["gcloud_used"] is True
    assert res["environment_record"] == "created"
    assert wired.environments[(CI_REPO, "my-proj")]["GAPP_DEPLOY_SERVICE_ACCOUNT"] == SA


def test_gcloud_path_fails_by_name_when_the_configured_account_has_no_login(wired, monkeypatch):
    def refuse():
        raise RuntimeError("gapp is configured to use the account deployer@example.com ... gcloud auth login deployer@example.com")

    monkeypatch.setattr(ci_mod, "require_gcloud_account", refuse)
    with pytest.raises(RuntimeError, match="deployer@example.com"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert (CI_REPO, WORKFLOW_PATH) not in wired.files      # stopped before any write


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


def test_setup_with_domain_writes_it(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    assert res["domain"] == "mcp.example.com"
    assert _written(wired)["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"


def test_rerun_without_domain_keeps_existing_domain(wired):
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", project="my-proj")
    assert res["domain"] == "mcp.example.com"
    assert res["gcloud_used"] is False                     # the first run recorded the Environment
    assert _written(wired)["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"
    assert res["workflow"] == "unchanged"


def test_rerun_with_empty_domain_removes_it(wired):
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="")
    assert res["domain"] is None
    assert "domain" not in _written(wired)["jobs"]["deploy"]["with"]


def test_setup_private_repo_adds_app_inputs_with_the_fixed_names(wired):
    wired.private["me/my-app"] = True
    wired.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))
    wired.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")

    assert res["solution_repo_private"] is True
    assert res["next_step"] is None
    assert res["app_credentials"] == {
        "app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
        "app_id_present": True, "private_key_present": True,
    }
    job = _written(wired)["jobs"]["deploy"]
    assert job["with"]["solution-repo-app-id"] == "${{ vars.HOSTING_GITHUB_APP_ID }}"
    assert job["secrets"]["solution-repo-app-private-key"] == "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"


def test_setup_private_repo_names_missing_credentials_in_next_step(wired):
    wired.private["me/my-app"] = True
    wired.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")

    # The workflow is still written; only the trigger has to wait.
    assert (CI_REPO, WORKFLOW_PATH) in wired.files
    assert res["app_credentials"]["private_key_present"] is False
    assert res["next_step"]["action"] == "configure hosting app"
    assert "Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY" in res["next_step"]["hint"]


def test_setup_fails_when_visibility_cannot_be_read(wired):
    del wired.private["me/my-app"]
    with pytest.raises(RuntimeError, match="visibility of me/my-app"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")


def test_setup_has_no_per_solution_credential_name_options():
    import inspect
    params = inspect.signature(ci_mod.setup_ci).parameters
    assert "app_id_var" not in params and "app_key_secret" not in params
    assert "project" in params
