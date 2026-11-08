"""CLI surface for `gapp ci` and `gapp status` — thin wrappers over the SDK.

Every SDK outcome (`ok` False with `next_step`) is printed as a plain
statement and the exact next step, and the command exits 1; nothing raises
through to the user.
"""

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import ci as ci_mod

FILES = {
    ".github/workflows/gapp-deploy.yml": {"present": True, "current": True},
    ".github/workflows/gapp-register.yml": {"present": True, "current": True},
    "gapp/solutions.yaml": {"present": True, "current": None},
}
VARS = {"GAPP_ALLOWED_SOURCES": True, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": True}


def _status(**overrides):
    base = {
        "ok": True, "repo": "acme/deploy-workflows", "files": FILES, "variables": VARS, "variables_checked": True,
        "variables_note": None, "solutions": [], "environments": {}, "legacy_workflows": [], "solution": "my-app",
        "registered": False, "solution_repo": None, "domain": None, "gapp_ref": None, "environment": None,
        "environment_record": None,
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
    assert "GAPP_ALLOWED_SOURCES present" in result.output
    assert "my-app (not registered)" in result.output
    assert "Next Step: ci setup" in result.output


def test_ci_status_outcome_exits_nonzero(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        ok=False, repo=None, files=None, variables=None, solution=None,
        next_step={"action": "ci init", "hint": "No CI repo is configured on this machine."}))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 1, result.output
    assert "Next Step: ci init" in result.output and "No CI repo is configured" in result.output


def test_ci_status_lists_solutions_and_is_ready(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        files={**FILES, ".github/workflows/gapp-deploy.yml": {"present": True, "current": False}},
        solutions=[{"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com",
                    "gapp_ref": "v3.9.1", "environment_exists": True}],
        legacy_workflows=["old-app"],
        registered=True, solution_repo="me/my-app", domain="mcp.example.com", environment="my-proj",
        environment_record={"exists": True, "wif_provider_present": True, "service_account_present": True, "checked": True},
        next_step=None,
    ))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "gapp-deploy.yml present, not current" in result.output
    assert "my-app: me/my-app → my-proj (Environment present, domain mcp.example.com, gapp v3.9.1)" in result.output
    assert "Legacy per-solution workflows (register, then delete): old-app" in result.output
    assert "Environment: my-proj (GAPP_WIF_PROVIDER present, GAPP_DEPLOY_SERVICE_ACCOUNT present)" in result.output
    assert "Ready: gapp ci deploy" in result.output


def test_ci_status_reports_unchecked_variables(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: _status(
        variables={k: None for k in VARS}, variables_checked=False, variables_note="not checked from this machine"))
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert "Variables: not checked from this machine" in result.output


def test_ci_init_local_only(monkeypatch):
    calls = {}

    def fake_init(repo, local_only=False):
        calls["repo"], calls["local_only"] = repo, local_only
        return {"ok": True, "repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "skipped", "next_step": None}

    monkeypatch.setattr(ci_mod, "init_ci", fake_init)
    result = CliRunner().invoke(cli_main, ["ci", "init", "deploy-workflows", "--local-only"])
    assert result.exit_code == 0, result.output
    assert calls == {"repo": "deploy-workflows", "local_only": True}
    assert "Variables" not in result.output


def test_ci_init_prints_the_installed_files_and_the_variables(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_ci", lambda repo, local_only=False: {
        "ok": True, "repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "added", "repo_created": True,
        "files": {".github/workflows/gapp-deploy.yml": "created", ".github/workflows/gapp-register.yml": "created",
                  "gapp/solutions.yaml": "created"},
        "variables": {"GAPP_ALLOWED_SOURCES": False, "HOSTING_GITHUB_APP_ID": True, "HOSTING_GITHUB_APP_PRIVATE_KEY": False},
        "variables_checked": True, "variables_note": None,
        "next_step": {"action": "configure variables", "hint": "No source owners are allowed yet in acme/deploy-workflows."},
    })
    result = CliRunner().invoke(cli_main, ["ci", "init", "acme/deploy-workflows"])
    assert result.exit_code == 0, result.output                 # ok with advice: exit 0
    assert "repo created" in result.output
    assert ".github/workflows/gapp-register.yml created" in result.output
    assert "GAPP_ALLOWED_SOURCES missing, HOSTING_GITHUB_APP_ID present, HOSTING_GITHUB_APP_PRIVATE_KEY missing" in result.output
    assert "Next Step: configure variables" in result.output


def test_ci_init_refused_write_exits_nonzero(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_ci", lambda repo, local_only=False: {
        "ok": False, "repo": "acme/deploy-workflows", "config_status": "saved", "topic_status": "already_set", "files": None,
        "variables": None, "next_step": {"action": "install operator files", "hint": "gapp's files could not be written."}})
    result = CliRunner().invoke(cli_main, ["ci", "init", "acme/deploy-workflows"])
    assert result.exit_code == 1, result.output
    assert "Next Step: install operator files" in result.output


def test_ci_init_help_states_the_api_write_requirement():
    result = CliRunner().invoke(cli_main, ["ci", "init", "--help"])
    assert "GitHub API" in result.output and "write" in result.output


def test_ci_project_setup_prints_resources_and_next_step(monkeypatch):
    calls = {}

    def fake(project):
        calls["project"] = project
        return {"ok": True, "project_id": project, "ci_repo": "acme/deploy-workflows", "wif_pool": "created",
                "wif_provider": "created", "service_account": "exists", "binding": "set", "environment": project,
                "environment_record": "created", "next_step": None}

    monkeypatch.setattr(ci_mod, "setup_ci_project", fake)
    result = CliRunner().invoke(cli_main, ["ci", "project", "setup", "my-proj"])
    assert result.exit_code == 0, result.output
    assert calls == {"project": "my-proj"}
    assert "WIF pool created" in result.output and "Environment my-proj created" in result.output
    assert "Next Step: gapp ci setup --project my-proj" in result.output


def test_ci_project_setup_outcome_exits_nonzero_with_the_requirement(monkeypatch):
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: {
        "ok": True, "project_id": project, "ci_repo": "acme/deploy-workflows", "wif_pool": None, "wif_provider": None,
        "service_account": None, "binding": None, "environment": project, "environment_record": None,
    } | ci_mod._outcome("install gcloud", "gcloud is not installed on this machine."))
    result = CliRunner().invoke(cli_main, ["ci", "project", "setup", "my-proj"])
    assert result.exit_code == 1, result.output
    assert "Next Step: install gcloud" in result.output
    help_text = CliRunner().invoke(cli_main, ["ci", "project", "setup", "--help"]).output
    assert "Owner" in help_text and "admin of the CI repo" in help_text


def test_ci_sources_commands(monkeypatch):
    calls = []

    def fake(action, owner=None):
        calls.append((action, owner))
        return {"ok": True, "action": action, "ci_repo": "acme/deploy-workflows", "owner": owner,
                "owners": ["me", "other-org"], "changed": action != "list", "next_step": None,
                "note": "me may now deploy through acme/deploy-workflows." if action == "add" else None}

    monkeypatch.setattr(ci_mod, "manage_sources", fake)
    runner = CliRunner()
    assert runner.invoke(cli_main, ["ci", "sources", "add", "me"]).exit_code == 0
    out = runner.invoke(cli_main, ["ci", "sources", "list"]).output
    assert "Allowed sources: me, other-org" in out
    assert runner.invoke(cli_main, ["ci", "sources", "remove", "other-org"]).exit_code == 0
    assert calls == [("add", "me"), ("list", None), ("remove", "other-org")]


def test_ci_sources_outcome_exits_nonzero(monkeypatch):
    monkeypatch.setattr(ci_mod, "manage_sources", lambda action, owner=None: {
        "ok": False, "action": action, "ci_repo": "acme/deploy-workflows", "owner": owner, "owners": None, "changed": False,
        "next_step": {"action": "sign in to gh with write access", "hint": "Could not write GAPP_ALLOWED_SOURCES."}})
    result = CliRunner().invoke(cli_main, ["ci", "sources", "add", "me"])
    assert result.exit_code == 1 and "Next Step: sign in to gh with write access" in result.output


def _setup_result(**overrides):
    base = {
        "ok": True, "solution": "my-app", "solution_repo": "me/my-app", "project_id": "my-proj",
        "ci_repo": "acme/deploy-workflows", "domain": None, "environment": "my-proj",
        "workflow": "gapp-register.yml", "dispatched": True, "run_id": "42",
        "run_url": "https://github.com/acme/deploy-workflows/actions/runs/42", "conclusion": "success",
        "registered": True, "result_code": "added",
        "messages": ["result=added", "added 'my-app' -> me/my-app on project 'my-proj'."], "next_step": None,
    }
    base.update(overrides)
    return base


def test_ci_setup_passes_args_including_the_exact_command_line(monkeypatch):
    calls = {}

    def fake_setup(solution=None, solution_repo=None, project=None, domain=None, watch=True, command=None):
        calls.update(solution=solution, solution_repo=solution_repo, project=project, domain=domain, watch=watch, command=command)
        return _setup_result(domain=domain)

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    monkeypatch.setattr("sys.argv", ["gapp", "ci", "setup", "-s", "my-app", "--solution-repo", "me/my-app",
                                     "--project", "my-proj", "--domain", "mcp.example.com"])
    result = CliRunner().invoke(cli_main, [
        "ci", "setup", "-s", "my-app", "--solution-repo", "me/my-app", "--project", "my-proj", "--domain", "mcp.example.com",
    ])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": "my-app", "solution_repo": "me/my-app", "domain": "mcp.example.com",
                     "project": "my-proj", "watch": True,
                     "command": "gapp ci setup -s my-app --solution-repo me/my-app --project my-proj --domain mcp.example.com"}
    assert "my-app (me/my-app) → my-proj via acme/deploy-workflows" in result.output
    assert "Domain mcp.example.com" in result.output
    assert "Register run dispatched: gapp-register.yml" in result.output
    assert "actions/runs/42" in result.output and "Conclusion: success" in result.output
    assert "added 'my-app' -> me/my-app on project 'my-proj'." in result.output
    assert "result=added" not in result.output
    assert "Next Step: gapp ci deploy" in result.output


def test_ci_setup_requires_project():
    result = CliRunner().invoke(cli_main, ["ci", "setup"])
    assert result.exit_code != 0 and "--project" in result.output


def test_ci_setup_prints_the_one_time_message_and_exits_nonzero(monkeypatch):
    message = ci_mod.PROJECT_NOT_TRUSTED_MESSAGE.format(ci_repo="acme/deploy-workflows", project="my-proj",
                                                         command="gapp ci setup --project my-proj")
    monkeypatch.setattr(ci_mod, "setup_ci", lambda **kw: _setup_result(
        dispatched=False, run_id=None, run_url=None, conclusion=None, registered=None, result_code=None, messages=[],
    ) | ci_mod._outcome("ci project setup", message))
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "my-proj"])
    assert result.exit_code == 1, result.output
    assert message in result.output
    assert "Register run dispatched" not in result.output


def test_ci_setup_no_watch_maps_the_flag(monkeypatch):
    calls = {}

    def fake_setup(**kw):
        calls.update(kw)
        return _setup_result(conclusion=None, registered=None, result_code=None, messages=[])

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "my-proj", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert calls["watch"] is False and "watch the run" in result.output


def test_ci_setup_has_no_gcloud_era_flags():
    for flag in ("--app-id-var", "--app-key-secret", "--out"):
        result = CliRunner().invoke(cli_main, ["ci", "setup", "--project", "p", flag, "X"])
        assert result.exit_code != 0 and "No such option" in result.output


def test_ci_deploy_maps_no_watch_and_prints_run(monkeypatch):
    calls = {}

    def fake_deploy(solution=None, ref="main", watch=True):
        calls.update(solution=solution, ref=ref, watch=watch)
        return {"ok": True, "solution": "my-app", "ci_repo": "acme/deploy-workflows", "workflow": "gapp-deploy.yml",
                "ref": ref, "run_id": "42", "run_url": "https://github.com/acme/deploy-workflows/actions/runs/42",
                "watched": watch, "conclusion": "success" if watch else None, "next_step": None}

    monkeypatch.setattr(ci_mod, "deploy_ci", fake_deploy)
    result = CliRunner().invoke(cli_main, ["ci", "deploy", "--ref", "v1.2.3", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": None, "ref": "v1.2.3", "watch": False}
    assert "Dispatched gapp-deploy.yml in acme/deploy-workflows for my-app (ref v1.2.3)" in result.output
    assert "Conclusion" not in result.output


def test_ci_deploy_outcome_exits_nonzero(monkeypatch):
    monkeypatch.setattr(ci_mod, "deploy_ci", lambda **kw: {
        "ok": False, "solution": "my-app", "ci_repo": "acme/deploy-workflows", "workflow": "gapp-deploy.yml", "ref": "main",
        "run_id": None, "run_url": None, "watched": False, "conclusion": None,
        "next_step": {"action": "ci setup", "hint": "'my-app' is not registered in acme/deploy-workflows."}})
    result = CliRunner().invoke(cli_main, ["ci", "deploy"])
    assert result.exit_code == 1 and "Next Step: ci setup" in result.output


def test_ci_trigger_is_a_hidden_alias_that_names_the_new_command(monkeypatch):
    calls = {}

    def fake_deploy(solution=None, ref="main", watch=True):
        calls.update(ref=ref)
        return {"ok": True, "solution": "my-app", "ci_repo": "acme/ci", "workflow": "gapp-deploy.yml", "ref": ref,
                "run_id": "7", "run_url": "https://github.com/acme/ci/actions/runs/7", "watched": False,
                "conclusion": None, "next_step": None}

    monkeypatch.setattr(ci_mod, "deploy_ci", fake_deploy)
    result = CliRunner().invoke(cli_main, ["ci", "trigger", "--ref", "v1", "--no-watch"])
    assert result.exit_code == 0, result.output
    assert "is now `gapp ci deploy`" in result.output and calls == {"ref": "v1"}
    assert "trigger" not in CliRunner().invoke(cli_main, ["ci", "--help"]).output


# -- gapp status never crashes --


def test_status_without_identity_prints_the_sign_in_step(monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.models import DeploymentInfo, NextStep, StatusResult

    monkeypatch.setattr(GappSDK, "status", lambda self, name=None, env=None: StatusResult(
        initialized=True, name="my-app", deployment=DeploymentInfo(project=None, pending=True),
        next_step=NextStep(action="sign in", hint="No Google Cloud identity this machine can use. Sign in with `gcloud auth login`.")))
    result = CliRunner().invoke(cli_main, ["status"])
    assert result.exit_code == 0, result.output
    assert "App:      my-app" in result.output and "Status:   PENDING" in result.output
    assert "Next Step: sign in" in result.output and "gcloud auth login" in result.output
    assert "NoneType" not in result.output and "Error" not in result.output


def test_status_uninitialized_prints_the_init_step(monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.models import DeploymentInfo, NextStep, StatusResult

    monkeypatch.setattr(GappSDK, "status", lambda self, name=None, env=None: StatusResult(
        initialized=False, deployment=DeploymentInfo(), next_step=NextStep(action="init", hint="No gapp.yaml here.")))
    result = CliRunner().invoke(cli_main, ["status"])
    assert result.exit_code == 0, result.output
    assert "not initialized here" in result.output and "Next Step: init" in result.output
    assert "NoneType" not in result.output


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
