"""CLI surface for `gapp ci` — thin wrappers over gapp.admin.sdk.ci."""

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import ci as ci_mod


def test_ci_status_reports_next_step(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: {"repo": "acme/deploy-workflows", "workflow": False})
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "acme/deploy-workflows" in result.output
    assert "missing" in result.output
    assert "Next Step: ci setup" in result.output


def test_ci_status_without_repo_points_at_init(monkeypatch):
    monkeypatch.setattr(ci_mod, "get_ci_status", lambda solution=None: {"repo": None, "workflow": False})
    result = CliRunner().invoke(cli_main, ["ci", "status"])
    assert result.exit_code == 0, result.output
    assert "Next Step: ci init" in result.output


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
    assert "Next Step: gapp ci setup" in result.output


def test_ci_setup_passes_args_and_prints_resources(monkeypatch):
    calls = {}

    def fake_setup(solution=None, solution_repo=None):
        calls["solution"], calls["solution_repo"] = solution, solution_repo
        return {
            "solution": "my-app", "solution_repo": "me/my-app", "project_id": "my-proj",
            "ci_repo": "acme/deploy-workflows", "wif_pool": "exists", "wif_provider": "created",
            "service_account": "exists", "binding": "exists", "workflow": "pushed",
        }

    monkeypatch.setattr(ci_mod, "setup_ci", fake_setup)
    result = CliRunner().invoke(cli_main, ["ci", "setup", "--solution", "my-app", "--solution-repo", "me/my-app"])
    assert result.exit_code == 0, result.output
    assert calls == {"solution": "my-app", "solution_repo": "me/my-app"}
    assert "my-app (me/my-app) → my-proj via acme/deploy-workflows" in result.output
    assert "Workflow pushed" in result.output


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
