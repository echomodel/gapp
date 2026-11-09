"""Root commands take the deployment name as an argument, like `gapp ci status <name>`.

`--solution`/`-s` is gone (a clean break). `gapp status [NAME]`,
`gapp setup [NAME] --project P` and `gapp secrets list [NAME]` take it;
`gapp deploy`, `gapp secrets get` and `gapp secrets set` always use the
checkout. Without a name, every command reads it from the checkout's
gapp.yaml as before. The MCP tools mirror the CLI.
"""

import inspect

import click
import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.mcp import server
from gapp.admin.sdk import secrets as sec_mod
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


def _run(*args):
    return CliRunner().invoke(cli_main, list(args))


def _root_commands(group=cli_main, path=()):
    for name, cmd in group.commands.items():
        if path == () and name == "ci":
            continue
        if isinstance(cmd, click.Group):
            yield from _root_commands(cmd, path + (name,))
        else:
            yield path + (name,), cmd


def test_no_root_command_takes_solution():
    for path, cmd in _root_commands():
        opts = {o for p in cmd.params for o in p.opts}
        assert not opts & {"--solution", "-s"}, path
        assert "solution" not in {p.name for p in cmd.params}, path


def test_status_takes_the_name_as_an_argument(monkeypatch):
    seen = {}

    def fake_status(self, name=None, project=None):
        seen.update(name=name, project=project)
        raise RuntimeError("stop")
    monkeypatch.setattr(GappSDK, "status", fake_status)
    _run("status", "my-app", "--project", "p1")
    assert seen == {"name": "my-app", "project": "p1"}
    _run("status")
    assert seen == {"name": None, "project": None}
    assert _run("status", "-s", "my-app").exit_code == 2


def test_setup_takes_the_name_as_an_argument_and_the_project_as_an_option(monkeypatch):
    seen = {}

    def fake_setup(self, project_id=None, name=None):
        seen.update(project_id=project_id, name=name)
        raise RuntimeError("stop")
    monkeypatch.setattr(GappSDK, "setup", fake_setup)
    _run("setup", "my-app", "--project", "p1")
    assert seen == {"project_id": "p1", "name": "my-app"}
    _run("setup", "--project", "p1")
    assert seen == {"project_id": "p1", "name": None}
    assert _run("setup", "--solution", "my-app").exit_code == 2


def test_deploy_and_secrets_get_set_take_no_deployment_name():
    assert _run("deploy", "my-app").exit_code == 2
    for args in (["deploy", "--solution", "x"], ["secrets", "get", "k", "-s", "x"],
                 ["secrets", "set", "k", "v", "-s", "x"]):
        assert _run(*args).exit_code == 2, args


def test_secrets_list_takes_the_name_as_an_argument(monkeypatch):
    seen = {}

    def fake_list(name=None):
        seen["name"] = name
        raise RuntimeError("stop")
    monkeypatch.setattr(sec_mod, "list_secrets", fake_list)
    _run("secrets", "list", "my-app")
    assert seen == {"name": "my-app"}


def test_the_mcp_tools_mirror_the_cli():
    def params(fn):
        return list(inspect.signature(fn).parameters)
    assert params(server.gapp_status) == ["name", "project"]
    assert params(server.gapp_setup) == ["name", "project_id"]
    assert params(server.gapp_secret_list) == ["name"]
    assert params(server.gapp_secret_get) == ["name", "plaintext"]
    assert params(server.gapp_secret_set) == ["name", "value"]
    assert "solution" not in params(server.gapp_deploy)


def test_status_outside_a_checkout_points_at_the_name_argument(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = GappSDK(provider=DummyCloudProvider()).status()
    assert res.next_step.hint == (
        "No gapp.yaml here (or not inside a git repository). Run `gapp init` in the "
        "app's repo, or run `gapp status <name>` to check a deployment.")


def test_setup_with_a_name_works_without_a_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sdk = GappSDK(provider=DummyCloudProvider())
    res = sdk.setup("p1", name="my-app")
    assert res["name"] == "my-app" and res["bucket"] == "gapp-my-app-p1"


def test_the_sdk_deploy_takes_no_name():
    assert "solution" not in inspect.signature(GappSDK.deploy).parameters
    assert "name" not in inspect.signature(GappSDK.deploy).parameters
