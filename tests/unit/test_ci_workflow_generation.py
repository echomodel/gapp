"""The caller workflow `gapp ci setup` writes into the CI repo.

Public solution repo: unchanged from before, except an optional `domain:`.
Private solution repo: the hosting GitHub App's id and private key are
passed through from the CI repo's Actions variable and secret, so the
runner can clone a repo its built-in token cannot see. Re-running setup
without a domain keeps the one already in the workflow.
"""

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

CI_REPO = "acme/ci"
WORKFLOW_PATH = ".github/workflows/my-app.yml"


@pytest.fixture
def pinned(monkeypatch):
    """Pin the two lookups `_generate_workflow` makes against the cloud."""
    monkeypatch.setattr(ci_mod, "_get_project_number", lambda project_id: "123456789012")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v3.8.0")


def _generate(**kwargs):
    return yaml.safe_load(ci_mod._generate_workflow(
        "my-app", "me/my-app", "my-proj", "example-org/gapp", **kwargs,
    ))


def test_public_repo_workflow_has_no_app_inputs_or_secrets(pinned):
    doc = _generate()
    job = doc["jobs"]["deploy"]
    assert job["uses"] == "example-org/gapp/.github/workflows/deploy.yml@v3.8.0"
    assert job["with"]["solution-repo"] == "me/my-app"
    assert "solution-repo-app-id" not in job["with"]
    assert "domain" not in job["with"]
    assert "secrets" not in job


def test_domain_is_written_as_the_domain_input(pinned):
    doc = _generate(domain="mcp.example.com")
    assert doc["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"
    assert "secrets" not in doc["jobs"]["deploy"]


def test_private_repo_workflow_passes_app_id_and_private_key(pinned):
    doc = _generate(app_id_var="HOSTING_GITHUB_APP_ID", app_key_secret="HOSTING_GITHUB_APP_PRIVATE_KEY")
    job = doc["jobs"]["deploy"]
    assert job["with"]["solution-repo-app-id"] == "${{ vars.HOSTING_GITHUB_APP_ID }}"
    assert job["secrets"] == {"solution-repo-app-private-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}


def test_app_id_and_key_must_be_given_together(pinned):
    with pytest.raises(ValueError, match="together"):
        _generate(app_id_var="HOSTING_GITHUB_APP_ID")


def test_parse_workflow_reads_back_what_generate_wrote(pinned):
    content = ci_mod._generate_workflow(
        "my-app", "me/my-app", "my-proj", "example-org/gapp",
        domain="mcp.example.com", app_id_var="MY_APP_ID", app_key_secret="MY_APP_KEY",
    )
    assert ci_mod._parse_workflow(content) == {
        "solution_repo": "me/my-app", "domain": "mcp.example.com",
        "app_id_var": "MY_APP_ID", "app_key_secret": "MY_APP_KEY",
    }
    assert ci_mod._parse_workflow(None) == {
        "solution_repo": None, "domain": None, "app_id_var": None, "app_key_secret": None,
    }
    assert ci_mod._parse_workflow("not: [valid")["solution_repo"] is None


# -- setup_ci end to end, against the in-memory GitHub --


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
    monkeypatch.setattr(GappSDK, "resolve_solution", lambda self, name=None, strict=True: {
        "name": "my-app", "project_id": None, "repo_path": None,
    })
    monkeypatch.setattr(GappSDK, "resolve_project_for_solution",
                        lambda self, *a, **kw: {"project_id": "my-proj", "env": None, "labels": {}, "status": "resolved"})
    fake_gh.private["me/my-app"] = False
    return fake_gh


def _written(gh):
    return yaml.safe_load(gh.files[(CI_REPO, WORKFLOW_PATH)])


def test_setup_public_repo_writes_plain_workflow(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["solution_repo_private"] is False
    assert res["app_credentials"] is None and res["next_step"] is None
    job = _written(wired)["jobs"]["deploy"]
    assert "solution-repo-app-id" not in job["with"] and "secrets" not in job


def test_setup_with_domain_writes_it(wired):
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    assert res["domain"] == "mcp.example.com"
    assert _written(wired)["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"


def test_rerun_without_domain_keeps_existing_domain(wired):
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
    assert res["domain"] == "mcp.example.com"
    assert _written(wired)["jobs"]["deploy"]["with"]["domain"] == "mcp.example.com"
    assert res["workflow"] == "unchanged"


def test_rerun_with_empty_domain_removes_it(wired):
    ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="mcp.example.com")
    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app", domain="")
    assert res["domain"] is None
    assert "domain" not in _written(wired)["jobs"]["deploy"]["with"]


def test_setup_private_repo_adds_app_inputs_with_default_names(wired):
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
    assert "HOSTING_GITHUB_APP_PRIVATE_KEY" in res["next_step"]["hint"]
    assert "HOSTING_GITHUB_APP_ID" not in res["next_step"]["hint"].split("Missing")[1].split(".")[0]


def test_setup_private_repo_honours_operator_credential_names(wired):
    wired.private["me/my-app"] = True
    wired.variables.add((CI_REPO, "MY_APP_ID"))
    wired.secrets.add((CI_REPO, "MY_APP_KEY"))

    res = ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app",
                          app_id_var="MY_APP_ID", app_key_secret="MY_APP_KEY")

    assert res["next_step"] is None
    job = _written(wired)["jobs"]["deploy"]
    assert job["with"]["solution-repo-app-id"] == "${{ vars.MY_APP_ID }}"
    assert job["secrets"]["solution-repo-app-private-key"] == "${{ secrets.MY_APP_KEY }}"


def test_setup_fails_when_visibility_cannot_be_read(wired):
    del wired.private["me/my-app"]
    with pytest.raises(RuntimeError, match="visibility of me/my-app"):
        ci_mod.setup_ci(solution="my-app", solution_repo="me/my-app")
