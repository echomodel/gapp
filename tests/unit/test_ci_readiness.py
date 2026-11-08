"""`gapp ci status` reports whether a CI deploy can clone the solution repo,
and `gapp ci trigger` refuses up front when it cannot.

A private solution repo is deployable only when its workflow passes the
hosting app's credentials and the CI repo holds the Actions variable and
secret the workflow names.
"""

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WORKFLOW_PATH = ".github/workflows/my-app.yml"


def _workflow(private=False, domain=None, id_var="HOSTING_GITHUB_APP_ID",
              key_secret="HOSTING_GITHUB_APP_PRIVATE_KEY"):
    job = {
        "uses": "example-org/gapp/.github/workflows/deploy.yml@v3.8.0",
        "with": {"solution-repo": "me/my-app", "ref": "${{ inputs.ref }}", "solution-name": "my-app"},
    }
    if domain:
        job["with"]["domain"] = domain
    if private:
        job["with"]["solution-repo-app-id"] = f"${{{{ vars.{id_var} }}}}"
        job["secrets"] = {"solution-repo-app-private-key": f"${{{{ secrets.{key_secret} }}}}"}
    return yaml.dump({"name": "Deploy my-app", "on": {"workflow_dispatch": {}}, "jobs": {"deploy": job}})


@pytest.fixture
def ci(monkeypatch, fake_gh):
    from gapp.admin.sdk.core import GappSDK

    monkeypatch.setattr(ci_mod, "_resolve_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": "my-app", "project_id": None, "repo_path": None,
    })
    return fake_gh


def test_status_without_workflow_reports_missing(ci):
    ci.private["me/my-app"] = False
    res = ci_mod.get_ci_status()
    assert res["repo"] == CI_REPO
    assert res["workflow"] is False
    assert res["solution_repo"] is None and res["next_step"] is None


def test_status_public_repo_is_ready(ci):
    ci.private["me/my-app"] = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(domain="mcp.example.com")
    res = ci_mod.get_ci_status()
    assert res["workflow"] is True
    assert res["solution_repo"] == "me/my-app"
    assert res["solution_repo_private"] is False
    assert res["domain"] == "mcp.example.com"
    assert res["app_credentials"] is None
    assert res["next_step"] is None


def test_status_private_repo_with_credentials_is_ready(ci):
    ci.private["me/my-app"] = True
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=True)
    ci.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))
    ci.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    res = ci_mod.get_ci_status()
    assert res["solution_repo_private"] is True
    assert res["app_credentials"]["app_id_present"] is True
    assert res["app_credentials"]["private_key_present"] is True
    assert res["next_step"] is None


def test_status_private_repo_missing_secret_names_it(ci):
    ci.private["me/my-app"] = True
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=True)
    ci.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))
    res = ci_mod.get_ci_status()
    assert res["app_credentials"]["private_key_present"] is False
    assert res["next_step"]["action"] == "configure hosting app"
    assert "Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY" in res["next_step"]["hint"]
    assert f"gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo {CI_REPO}" in res["next_step"]["hint"]


def test_status_private_repo_with_old_workflow_points_at_setup(ci):
    """A workflow written before private-repo support has no app inputs."""
    ci.private["me/my-app"] = True
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=False)
    res = ci_mod.get_ci_status()
    assert res["app_credentials"] is None
    assert res["next_step"]["action"] == "ci setup"
    assert "gapp ci setup --solution my-app" in res["next_step"]["hint"]


def test_status_reports_unreadable_visibility_instead_of_guessing(ci):
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=True)
    res = ci_mod.get_ci_status()
    assert res["solution_repo_private"] is None
    assert res["next_step"]["action"] == "check solution repo"
    assert "visibility of me/my-app" in res["next_step"]["hint"]


# -- trigger --


class _Dispatching:
    """Extend the fake GitHub so `gh workflow run` and the run lookup succeed."""

    def __init__(self, gh):
        self.gh = gh
        self.dispatched = []
        gh.other = self.other

    def other(self, cmd, kwargs):
        from tests.unit.conftest import _Result
        if cmd[:3] == ["gh", "workflow", "run"]:
            self.dispatched.append(cmd)
            return _Result(0, "")
        if cmd[:2] == ["gh", "api"] and cmd[2].endswith("/actions/runs"):
            return _Result(0, "42\n")
        raise AssertionError(f"unexpected gh command {cmd}")


@pytest.fixture
def trigger_env(monkeypatch, ci):
    import time
    monkeypatch.setattr(ci_mod, "get_ci_repo", lambda: CI_REPO)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return _Dispatching(ci)


def test_trigger_refuses_private_repo_without_credentials(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = True
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=True)
    with pytest.raises(RuntimeError, match="HOSTING_GITHUB_APP_ID"):
        ci_mod.trigger_ci(watch=False)
    assert trigger_env.dispatched == []


def test_trigger_refuses_private_repo_with_old_workflow(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = True
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=False)
    with pytest.raises(RuntimeError, match="gapp ci setup --solution my-app"):
        ci_mod.trigger_ci(watch=False)
    assert trigger_env.dispatched == []


def test_trigger_dispatches_private_repo_with_credentials(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = True
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _workflow(private=True)
    gh.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))
    gh.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    res = ci_mod.trigger_ci(ref="v1.0.0", watch=False)
    assert res["run_id"] == "42"
    assert len(trigger_env.dispatched) == 1
    assert "ref=v1.0.0" in trigger_env.dispatched[0]


def test_trigger_dispatches_public_repo_as_before(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = False
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _workflow()
    res = ci_mod.trigger_ci(watch=False)
    assert res["run_id"] == "42"
    assert len(trigger_env.dispatched) == 1


# -- the Environment a workflow deploys through --


def _env_workflow(environment="my-proj", private=False):
    job = {
        "uses": "example-org/gapp/.github/workflows/deploy.yml@v3.9.0",
        "with": {"solution-repo": "me/my-app", "ref": "${{ inputs.ref }}",
                 "environment": environment, "solution-name": "my-app"},
    }
    if private:
        job["with"]["solution-repo-app-id"] = "${{ vars.HOSTING_GITHUB_APP_ID }}"
        job["secrets"] = {"solution-repo-app-private-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}
    return yaml.dump({"name": "Deploy my-app", "on": {"workflow_dispatch": {}}, "jobs": {"deploy": job}})


def test_status_reports_the_environment_and_its_variables(ci):
    ci.private["me/my-app"] = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow()
    ci.environments[(CI_REPO, "my-proj")] = {
        "GAPP_WIF_PROVIDER": "projects/1/locations/global/workloadIdentityPools/github/providers/github",
        "GAPP_DEPLOY_SERVICE_ACCOUNT": "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"]),
    }
    res = ci_mod.get_ci_status()
    assert res["environment"] == "my-proj"
    assert res["environment_record"] == {"exists": True, "wif_provider_present": True,
                                         "service_account_present": True, "checked": True, "note": None}
    assert res["next_step"] is None


def test_status_reports_an_unreadable_record_as_unchecked_not_missing(ci):
    ci.private["me/my-app"] = True
    ci.actions_api = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow(private=True)
    ci.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["exists"] is True
    assert res["environment_record"]["checked"] is False
    assert res["app_credentials"]["checked"] is False
    assert res["next_step"] is None


def test_status_names_a_missing_environment(ci):
    ci.private["me/my-app"] = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow()
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["exists"] is False
    assert res["next_step"]["action"] == "ci setup"
    assert "does not exist" in res["next_step"]["hint"]


def test_status_names_a_missing_environment_variable(ci):
    ci.private["me/my-app"] = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow()
    ci.environments[(CI_REPO, "my-proj")] = {"GAPP_WIF_PROVIDER": "projects/1/..."}
    res = ci_mod.get_ci_status()
    assert res["environment_record"]["wif_provider_present"] is True
    assert res["environment_record"]["service_account_present"] is False
    assert res["next_step"]["action"] == "ci setup"
    assert "GAPP_DEPLOY_SERVICE_ACCOUNT" in res["next_step"]["hint"]
    assert "gapp ci setup --solution my-app" in res["next_step"]["hint"]


def test_status_of_a_literal_workflow_has_no_environment(ci):
    ci.private["me/my-app"] = False
    ci.files[(CI_REPO, WORKFLOW_PATH)] = _workflow()
    res = ci_mod.get_ci_status()
    assert res["environment"] is None and res["environment_record"] is None
    assert res["next_step"] is None


def test_trigger_dispatches_when_the_checks_cannot_run_from_this_machine(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = True
    gh.actions_api = False
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow(private=True)
    gh.environments[(CI_REPO, "my-proj")] = {}
    res = ci_mod.trigger_ci(watch=False)
    assert res["run_id"] == "42"
    assert len(trigger_env.dispatched) == 1


def test_trigger_refuses_when_the_environment_record_is_incomplete(trigger_env):
    gh = trigger_env.gh
    gh.private["me/my-app"] = False
    gh.files[(CI_REPO, WORKFLOW_PATH)] = _env_workflow()
    gh.environments[(CI_REPO, "my-proj")] = {}
    with pytest.raises(RuntimeError, match="GAPP_WIF_PROVIDER"):
        ci_mod.trigger_ci(watch=False)
    assert trigger_env.dispatched == []
