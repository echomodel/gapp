"""Environments are removed: a deployment (its name, project and config) is
the unit. No `gapp projects` group, no --env option or MCP argument, no env
in any result, and no gapp code reads or writes the gapp-env project label.
A name on more than one hosted project is refused with one message, after
the search stops at the second match."""

import inspect
import pathlib
import re

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.mcp import server
from gapp.admin.sdk.core import AmbiguousDeployment, GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

ROOT = pathlib.Path(__file__).resolve().parents[2]
AMBIGUOUS = "'my-app' exists on more than one project. Pass --project to choose one; `gapp list` shows where each is."


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)


def _options(command):
    return {o for p in cli_main.commands[command].params for o in p.opts}


def test_there_is_no_projects_group_and_no_env_option():
    assert "projects" not in cli_main.commands
    for command in ("setup", "deploy", "status"):
        assert "--env" not in _options(command), command
        assert CliRunner().invoke(cli_main, [command, "--env", "prod"]).exit_code != 0


def test_the_mcp_tools_take_no_env_and_the_projects_tools_are_gone():
    assert not [n for n in dir(server) if n.startswith("gapp_projects")]
    for tool in (server.gapp_setup, server.gapp_deploy, server.gapp_status):
        assert "env" not in inspect.signature(tool).parameters, tool.__name__


def test_deploy_refuses_a_name_on_two_projects_even_on_a_dry_run(tmp_path, monkeypatch, sdk):
    _repo(tmp_path, monkeypatch)
    sdk.provider.host("p1", "my-app")
    sdk.provider.host("p2", "my-app")
    for dry_run in (True, False):
        with pytest.raises(AmbiguousDeployment) as exc:
            sdk.deploy(dry_run=dry_run)
        assert str(exc.value) == AMBIGUOUS


def test_the_cli_prints_the_ambiguity_message_for_deploy_and_status(monkeypatch):
    def ambiguous(*a, **k):
        raise AmbiguousDeployment(AMBIGUOUS)
    monkeypatch.setattr(GappSDK, "deploy", ambiguous)
    monkeypatch.setattr(GappSDK, "status", ambiguous)
    for args in (["deploy", "--dry-run"], ["status", "my-app"]):
        res = CliRunner().invoke(cli_main, args)
        assert AMBIGUOUS in res.output, (args, res.output)


def test_list_with_no_deployments_says_none_have_been_set_up(monkeypatch):
    monkeypatch.setattr(GappSDK, "list_apps", lambda self, **k: {
        "apps": [], "warnings": [], "messages": [],
        "metadata": {"projects": {"count": 0, "limit": 50}}})
    out = CliRunner().invoke(cli_main, ["list"]).output
    assert "  No apps found: no deployments have been set up on any project yet." in out
    assert "  No apps found.\n" not in out


def test_a_ci_run_with_an_explicit_project_reads_no_project_labels(tmp_path, monkeypatch, sdk):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _repo(tmp_path, monkeypatch)
    for name in ("get_project_labels", "list_projects"):
        monkeypatch.setattr(sdk.provider, name, lambda *a, **k: pytest.fail("no project labels on CI"))
    res = sdk.setup(project_id="p1")
    assert res["label_status"] == "skipped"
    assert sdk.deploy(dry_run=True, project_id="p1")["project_id"] == "p1"


def test_no_code_reads_or_writes_the_gapp_env_label():
    offenders = [f"{p.relative_to(ROOT)}:{i}" for p in (ROOT / "gapp").rglob("*.py")
                 for i, line in enumerate(p.read_text().splitlines(), 1)
                 if re.search(r"gapp-env|PROJECT_ENV_LABEL", line)]
    assert offenders == []
