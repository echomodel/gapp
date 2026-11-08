"""CLI surface for `gapp ci` — thin wrappers over gapp.admin.sdk.ci."""

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import ci as ci_mod


def _status(**overrides):
    base = {
        "repo": "acme/deploy-workflows", "workflow": False, "solution_repo": None,
        "solution_repo_private": None, "domain": None, "environment": None,
        "environment_record": None, "app_credentials": None, "next_step": None,
    }
    base.update(overrides)
    return base


def test_ci_status_reports_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status())
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "acme/deploy-workflows" in result.output
    assert "missing" in result.output
    assert "Next Step: ci setup" in result.output


def test_ci_status_without_repo_points_at_init(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(repo=None))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "Next Step: ci init" in result.output


def test_ci_status_shows_private_repo_credentials_and_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        workflow=True, solution_repo="me/my-app", solution_repo_private=True,
        domain="mcp.example.com",
        app_credentials={
            "app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
            "app_id_present": True, "private_key_present": False,
        },
        next_step={"action": "configure hosting app", "hint": "Missing in acme/deploy-workflows: Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY."},
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "me/my-app (private)" in result.output
    assert "Domain:    mcp.example.com" in result.output
    assert "variable HOSTING_GITHUB_APP_ID present" in result.output
    assert "secret HOSTING_GITHUB_APP_PRIVATE_KEY missing" in result.output
    assert "Next Step: configure hosting app" in result.output
    assert "Ready: gapp ci trigger" not in result.output


def test_ci_status_ready_for_public_repo(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        workflow=True, solution_repo="me/my-app", solution_repo_private=False,
        environment="my-proj",
        environment_record={"wif_provider_present": True, "service_account_present": True},
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "me/my-app (public)" in result.output
    assert "Environment: my-proj (GAPP_WIF_PROVIDER present, GAPP_DEPLOY_SERVICE_ACCOUNT present)" in result.output
    assert "Ready: gapp ci trigger" in result.output


def test_ci_status_names_a_missing_environment_variable(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        workflow=True, solution_repo="me/my-app", environment="my-proj",
        environment_record={"wif_provider_present": True, "service_account_present": False},
        next_step={"action": "ci setup", "hint": "Environment 'my-proj' is missing GAPP_DEPLOY_SERVICE_ACCOUNT."},
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "GAPP_DEPLOY_SERVICE_ACCOUNT missing" in result.output
    assert "Next Step: ci setup" in result.output


def test_ci_init_passes_args_and_prints_result(monkeypatch):
    calls = {}

    def fake_init(repo, local_only=False):
        calls["repo"], calls["local_only"] = repo, local_only
        return {"repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "added", "repo_created": True}

    monkeypatch.setattr(ci_mod, "init_ci", fake_init)
    result = CliRunner().invoke(cli_main, ["ci", "init", "deploy-workflows", "--local-only"])
    assert result.exit_code == 0, result.output
    assert calls == {"repo": "deploy-workflows", "local_only": True}
    assert "acme/deploy-workflows" in result.output
    assert "repo created" in result.output
    assert "Hosting app" not in result.output
    assert "Next Step: gapp ci setup" in result.output


def test_ci_init_reports_the_hosting_app_credentials(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_ci", lambda repo, local_only=False: {
        "repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "already_set",
        "hosting_app": {"app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
                        "app_id_present": True, "private_key_present": False},
        "next_step": {"action": "configure hosting app",
                      "hint": "Not yet in acme/deploy-workflows: Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY. Only private solution repos need them."},
    })
    result = CliRunner().invoke(cli_main, ["ci", "init", "acme/deploy-workflows"])
    assert result.exit_code == 0, result.output
    assert "variable HOSTING_GITHUB_APP_ID present, secret HOSTING_GITHUB_APP_PRIVATE_KEY missing" in result.output
    assert "Only private solution repos need them" in result.output
    assert "Next Step: gapp ci setup" in result.output


def _setup_result(**overrides):
    base = {
        "solution": "my-app", "solution_repo": "me/my-app", "solution_repo_private": False,
        "project_id": "my-proj", "ci_repo": "acme/deploy-workflows", "domain": None,
        "environment": "my-proj", "environment_record": "created", "gcloud_used": True,
        "app_credentials": None, "wif_pool": "exists", "wif_provider": "created",
        "service_account": "exists", "binding": "exists", "workflow": "pushed", "next_step": None,
    }
    base.update(overrides)
    return base


def test_ci_setup_passes_args_and_prints_resources(monkeypatch):
    calls = {}

    def fake_setup(solution=None, solution_repo=None, domain=None, project=None):
        calls.update(solution=solution, solution_repo=solution_repo, domain=domain, project=project)
        return _setup_result()

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--solution", "my-app", "--solution-repo", "me/my-app"])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": "my-app", "solution_repo": "me/my-app", "domain": None, "project": None}
    assert "my-app (me/my-app, public) → my-proj via acme/deploy-workflows" in result.output
    assert "WIF pool exists" in result.output
    assert "Environment my-proj created" in result.output
    assert "Workflow pushed" in result.output
    assert "Next Step: gapp ci trigger" in result.output


def test_ci_setup_with_project_reports_no_gcloud(monkeypatch):
    calls = {}

    def fake_setup(solution=None, solution_repo=None, domain=None, project=None):
        calls.update(project=project, domain=domain)
        return _setup_result(gcloud_used=False, environment_record="used", wif_pool="recorded",
                             wif_provider="recorded", service_account="recorded", binding="recorded",
                             domain=domain)

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, [
        "ci", "setup", "--solution", "my-app", "--solution-repo", "me/my-app",
        "--project", "my-proj", "--domain", "mcp.example.com",
    ])
    assert result.exit_code == 0, result.output
    assert calls == {"project": "my-proj", "domain": "mcp.example.com"}
    assert "Environment my-proj recorded; no gcloud call made" in result.output
    assert "WIF pool" not in result.output
    assert "Domain mcp.example.com" in result.output


def test_ci_setup_private_repo_prints_credentials_and_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "setup_ci", lambda **kw: _setup_result(
        solution_repo_private=True,
        app_credentials={"app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
                         "app_id_present": False, "private_key_present": False},
        next_step={"action": "configure hosting app", "hint": "Missing in acme/deploy-workflows: Actions variable HOSTING_GITHUB_APP_ID, Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY."},
    ))
    result = CliRunner().invoke(cli_main, ["ci", "setup"])
    assert result.exit_code == 0, result.output
    assert "private" in result.output
    assert "variable HOSTING_GITHUB_APP_ID missing" in result.output
    assert "Next Step: configure hosting app" in result.output
    assert "Next Step: gapp ci trigger" not in result.output


def test_ci_setup_has_no_credential_name_flags():
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--app-id-var", "X"])
    assert result.exit_code != 0
    assert "No such option" in result.output
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--help"])
    assert "--project" in result.output and "--app-key-secret" not in result.output


def test_ci_trigger_maps_no_watch_and_prints_run(monkeypatch):
    calls = {}

    def fake_trigger(solution=None, ref="main", watch=True):
        calls.update(solution=solution, ref=ref, watch=watch)
        return {
            "solution": "my-app", "ci_repo": "acme/deploy-workflows", "workflow": "my-app.yml",
            "ref": ref, "run_id": "42", "run_url": "https://github.com/acme/deploy-workflows/actions/runs/42",
            "watched": watch, "conclusion": "success" if watch else None,
        }

    monkeypatch.setattr(ci_mod, "trigger_ci", fake_trigger)
    result = CliRunner().invoke(cli_main, ["ci", "trigger", "--ref", "v1.2.3", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": None, "ref": "v1.2.3", "watch": False}
    assert "actions/runs/42" in result.output
    assert "Conclusion" not in result.output


def test_ci_commands_surface_sdk_errors(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("No CI repo configured. Run 'gapp ci init <repo-name>' first.")

    monkeypatch.setattr(ci_mod, "trigger_ci", boom)
    result = CliRunner().invoke(cli_main, ["ci", "trigger"])
    assert result.exit_code == 1
    assert "No CI repo configured" in result.output


def test_deploy_passes_replace_domain(monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    calls = {}

    def fake_deploy(self, **kwargs):
        calls.update(kwargs)
        return {"name": "my-app"}

    monkeypatch.setattr(GappSDK, "deploy", fake_deploy)
    result = CliRunner().invoke(cli_main, ["deploy", "--replace-domain"])
    assert result.exit_code == 0, result.output
    assert calls["replace_domain"] is True

    result = CliRunner().invoke(cli_main, ["deploy"])
    assert result.exit_code == 0, result.output
    assert calls["replace_domain"] is False
