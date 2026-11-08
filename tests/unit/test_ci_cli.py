"""CLI surface for `gapp ci` — thin wrappers over gapp.admin.sdk.ci."""

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import ci as ci_mod

FILES = {".github/workflows/gapp-deploy.yml": True, ".github/workflows/gapp-register.yml": True, "gapp/solutions.yaml": True}
VARS = {"GAPP_ALLOWED_OWNERS": True, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": True}


def _status(**overrides):
    base = {
        "repo": "acme/deploy-workflows", "files": FILES, "variables": VARS, "variables_checked": True,
        "variables_note": None, "solutions": [], "legacy_workflows": [], "solution": "my-app",
        "registered": False, "workflow": False, "solution_repo": None, "solution_repo_private": None,
        "domain": None, "environment": None, "environment_record": None, "app_credentials": None,
        "next_step": {"action": "ci setup", "hint": "'my-app' is not registered."},
    }
    base.update(overrides)
    return base


def test_ci_status_reports_files_variables_and_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status())
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "acme/deploy-workflows" in result.output
    assert "gapp-deploy.yml present, gapp-register.yml present, solutions.yaml present" in result.output
    assert "GAPP_ALLOWED_OWNERS present" in result.output
    assert "my-app (not registered)" in result.output
    assert "Next Step: ci setup" in result.output


def test_ci_status_without_repo_points_at_init(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        repo=None, files=None, variables=None, solution=None, next_step={"action": "ci init", "hint": "Run gapp ci init."}))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "Next Step: ci init" in result.output


def test_ci_status_lists_solutions_and_is_ready(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        solutions=[{"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com",
                    "gapp_ref": None, "environment_exists": True}],
        legacy_workflows=["old-app"],
        registered=True, workflow=True, solution_repo="me/my-app", solution_repo_private=False,
        domain="mcp.example.com", environment="my-proj",
        environment_record={"exists": True, "wif_provider_present": True, "service_account_present": True, "checked": True},
        next_step=None,
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "my-app: me/my-app → my-proj (Environment present, domain mcp.example.com)" in result.output
    assert "Own workflows (register, then delete): old-app" in result.output
    assert "me/my-app (public)" in result.output
    assert "Environment: my-proj (GAPP_WIF_PROVIDER present, GAPP_DEPLOY_SERVICE_ACCOUNT present)" in result.output
    assert "Ready: gapp ci trigger" in result.output


def test_ci_status_shows_private_repo_credentials_and_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        registered=True, workflow=True, solution_repo="me/my-app", solution_repo_private=True,
        app_credentials={"app_id_var": "HOSTING_GITHUB_APP_ID", "app_key_secret": "HOSTING_GITHUB_APP_PRIVATE_KEY",
                         "app_id_present": True, "private_key_present": False},
        next_step={"action": "configure hosting app", "hint": "Missing in acme/deploy-workflows: Actions secret HOSTING_GITHUB_APP_PRIVATE_KEY."},
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "me/my-app (private)" in result.output
    assert "secret HOSTING_GITHUB_APP_PRIVATE_KEY missing" in result.output
    assert "Next Step: configure hosting app" in result.output
    assert "Ready: gapp ci trigger" not in result.output


def test_ci_status_reports_unchecked_variables(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        variables={k: None for k in VARS}, variables_checked=False, variables_note="not checked from this machine"))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert "Variables: not checked from this machine" in result.output


def test_ci_init_passes_args_and_prints_result(monkeypatch):
    calls = {}

    def fake_init(repo, local_only=False):
        calls["repo"], calls["local_only"] = repo, local_only
        return {"repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "skipped"}

    monkeypatch.setattr(ci_mod, "init_ci", fake_init)
    result = CliRunner().invoke(cli_main, ["ci", "init", "deploy-workflows", "--local-only"])
    assert result.exit_code == 0, result.output
    assert calls == {"repo": "deploy-workflows", "local_only": True}
    assert "acme/deploy-workflows" in result.output
    assert "Variables" not in result.output
    assert "Next Step: gapp ci setup --project <project-id>" in result.output


def test_ci_init_prints_the_installed_files_and_the_variables(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_ci", lambda repo, local_only=False: {
        "repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "added", "repo_created": True,
        "files": {".github/workflows/gapp-deploy.yml": "created", ".github/workflows/gapp-register.yml": "created",
                  "gapp/solutions.yaml": "created"},
        "variables": {"GAPP_ALLOWED_OWNERS": False, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": False},
        "variables_checked": True, "variables_note": None,
        "next_step": {"action": "configure variables",
                      "hint": "Repository variable GAPP_ALLOWED_OWNERS is not set in acme/deploy-workflows."},
    })
    result = CliRunner().invoke(cli_main, ["ci", "init", "acme/deploy-workflows"])
    assert result.exit_code == 0, result.output
    assert "repo created" in result.output
    assert ".github/workflows/gapp-register.yml created" in result.output
    assert "GAPP_ALLOWED_OWNERS missing, HOSTING_GITHUB_APP_ID present, HOSTING_GITHUB_APP_PRIVATE_KEY missing" in result.output
    assert "Next Step: configure variables" in result.output


def _setup_result(**overrides):
    base = {
        "solution": "my-app", "solution_repo": "me/my-app", "solution_repo_private": False,
        "project_id": "my-proj", "ci_repo": "acme/deploy-workflows", "domain": None,
        "environment": "my-proj", "environment_record": "exists", "gcloud_used": False,
        "wif_pool": None, "wif_provider": None, "service_account": None, "binding": None,
        "app_credentials": None, "workflow": "gapp-register.yml", "dispatched": True,
        "run_id": "42", "run_url": "https://github.com/acme/deploy-workflows/actions/runs/42",
        "conclusion": "success", "registered": True,
        "messages": ["added 'my-app' -> me/my-app on project 'my-proj'"], "next_step": None,
    }
    base.update(overrides)
    return base


def test_ci_setup_passes_args_and_prints_the_run(monkeypatch):
    calls = {}

    def fake_setup(solution=None, solution_repo=None, domain=None, project=None, watch=True):
        calls.update(solution=solution, solution_repo=solution_repo, domain=domain, project=project, watch=watch)
        return _setup_result(domain=domain)

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, [
        "ci", "setup", "--solution", "my-app", "--solution-repo", "me/my-app",
        "--project", "my-proj", "--domain", "mcp.example.com",
    ])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": "my-app", "solution_repo": "me/my-app", "domain": "mcp.example.com",
                     "project": "my-proj", "watch": True}
    assert "my-app (me/my-app, public) → my-proj via acme/deploy-workflows" in result.output
    assert "Environment my-proj exists; no gcloud call made" in result.output
    assert "Domain mcp.example.com" in result.output
    assert "Register run dispatched: gapp-register.yml" in result.output
    assert "actions/runs/42" in result.output
    assert "Conclusion: success" in result.output
    assert "added 'my-app' -> me/my-app on project 'my-proj'" in result.output
    assert "Next Step: gapp ci trigger" in result.output


def test_ci_setup_first_time_prints_the_gcloud_resources(monkeypatch):
    monkeypatch.setattr(ci_mod, "setup_ci", lambda **kw: _setup_result(
        gcloud_used=True, wif_pool="created", wif_provider="created", service_account="created",
        binding="set", environment_record="created"))
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "my-proj"])
    assert result.exit_code == 0, result.output
    assert "WIF pool created" in result.output
    assert "Environment my-proj created" in result.output


def test_ci_setup_no_watch_maps_the_flag(monkeypatch):
    calls = {}

    def fake_setup(**kw):
        calls.update(kw)
        return _setup_result(conclusion=None, registered=None, messages=[])

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "my-proj", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert calls["watch"] is False
    assert "watch the run" in result.output


def test_ci_setup_stopped_prints_the_next_step_and_exits_nonzero(monkeypatch):
    monkeypatch.setattr(ci_mod, "setup_ci", lambda **kw: _setup_result(
        dispatched=False, run_id=None, run_url=None, conclusion=None, registered=None, messages=[],
        environment_record=None,
        next_step={"action": "pass --project", "hint": "Re-run with --project <project-id>."}))
    result = CliRunner().invoke(cli_main, ["ci", "setup"])
    assert result.exit_code == 1, result.output
    assert "Next Step: pass --project" in result.output
    assert "Register run dispatched" not in result.output


def test_ci_setup_refused_run_prints_the_reason(monkeypatch):
    monkeypatch.setattr(ci_mod, "setup_ci", lambda **kw: _setup_result(
        conclusion="failure", registered=False,
        messages=["refused: owner 'me' is not in GAPP_ALLOWED_OWNERS (other-org)"],
        next_step={"action": "fix and re-run ci setup", "hint": "The register run ended with 'failure'. refused: owner 'me' is not in GAPP_ALLOWED_OWNERS (other-org)"}))
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "my-proj"])
    assert result.exit_code == 1, result.output
    assert "Conclusion: failure" in result.output
    assert "Next Step: fix and re-run ci setup" in result.output


def test_ci_setup_has_no_credential_name_or_out_flags():
    for flag in ("--app-id-var", "--app-key-secret", "--out"):
        result = CliRunner().invoke(cli_main, ["ci", "setup", flag, "X"])
        assert result.exit_code != 0 and "No such option" in result.output
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--help"])
    assert "--project" in result.output and "--no-watch" in result.output


def test_ci_trigger_maps_no_watch_and_prints_run(monkeypatch):
    calls = {}

    def fake_trigger(solution=None, ref="main", watch=True):
        calls.update(solution=solution, ref=ref, watch=watch)
        return {
            "solution": "my-app", "ci_repo": "acme/deploy-workflows", "workflow": "gapp-deploy.yml",
            "legacy_workflow": False, "ref": ref, "run_id": "42",
            "run_url": "https://github.com/acme/deploy-workflows/actions/runs/42",
            "watched": watch, "conclusion": "success" if watch else None, "next_step": None,
        }

    monkeypatch.setattr(ci_mod, "trigger_ci", fake_trigger)
    result = CliRunner().invoke(cli_main, ["ci", "trigger", "--ref", "v1.2.3", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": None, "ref": "v1.2.3", "watch": False}
    assert "Dispatched gapp-deploy.yml in acme/deploy-workflows for my-app (ref v1.2.3)" in result.output
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
