"""Root commands take the deployment name as an argument, like `gapp ci status <name>`.

`--solution`/`-s` is gone (a clean break). `gapp status [NAME]` and
`gapp secrets list [NAME]` take it; `gapp setup --project P`,
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


def test_setup_takes_only_the_project_as_an_option(monkeypatch):
    seen = {}

    def fake_setup(self, project_id=None):
        seen["project_id"] = project_id
        raise RuntimeError("stop")
    monkeypatch.setattr(GappSDK, "setup", fake_setup)
    _run("setup", "--project", "p1")
    assert seen == {"project_id": "p1"}
    for args in (["setup", "my-app", "--project", "p1"], ["setup", "p1"], ["setup", "--solution", "my-app"]):
        assert _run(*args).exit_code == 2, args


def test_setup_deploy_and_secrets_get_set_take_no_deployment_name():
    assert _run("deploy", "my-app").exit_code == 2
    for args in (["deploy", "--solution", "x"], ["secrets", "get", "k", "-s", "x"],
                 ["secrets", "set", "k", "v", "-s", "x"]):
        assert _run(*args).exit_code == 2, args


def test_secrets_list_takes_the_name_as_an_argument(monkeypatch):
    seen = {}

    def fake_list(name=None, deployment=None):
        seen["name"] = name
        raise RuntimeError("stop")
    monkeypatch.setattr(sec_mod, "list_secrets", fake_list)
    _run("secrets", "list", "my-app")
    assert seen == {"name": "my-app"}


def test_the_mcp_tools_mirror_the_cli():
    def params(fn):
        return list(inspect.signature(fn).parameters)
    assert params(server.gapp_status) == ["name", "project"]
    assert params(server.gapp_setup) == ["project_id"]
    assert params(server.gapp_secret_list) == ["name", "deployment"]
    assert params(server.gapp_secret_get) == ["name", "plaintext", "deployment"]
    assert params(server.gapp_secret_set) == ["name", "value", "deployment"]
    assert "solution" not in params(server.gapp_deploy)


def test_status_outside_a_checkout_points_at_the_name_argument(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = GappSDK(provider=DummyCloudProvider()).status()
    assert res.next_step.hint == (
        "No gapp.yaml here (or not inside a git repository). Run `gapp init` in the "
        "app's repo, or run `gapp status <name>` to check a deployment.")


def test_setup_outside_a_checkout_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """Without the checkout's gapp.yaml, setup could not enable the app's required APIs."""
    monkeypatch.chdir(tmp_path)
    sdk = GappSDK(provider=DummyCloudProvider())
    with pytest.raises(RuntimeError, match="Not inside an app's folder."):
        sdk.setup("p1")
    assert not sdk.provider.apis_enabled and not sdk.provider.buckets


def test_the_sdk_setup_and_deploy_take_no_name():
    for fn in (GappSDK.setup, GappSDK.deploy):
        params = inspect.signature(fn).parameters
        assert "solution" not in params and "name" not in params, fn.__name__
