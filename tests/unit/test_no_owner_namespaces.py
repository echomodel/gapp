"""Owner namespaces are removed: no command, option, tool argument, output
line or config key for them, and an `owner:` key in the user config is an
error the CLI prints plainly."""

import inspect
import subprocess
import sys

import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.mcp import server
from gapp.admin.sdk.config import get_config_file


def _run(*args):
    return CliRunner().invoke(cli_main, list(args))


def test_the_owner_command_and_options_are_gone():
    assert "owner" not in cli_main.commands["config"].commands
    assert "--all" not in {o for p in cli_main.commands["list"].params for o in p.opts}
    assert "--force" not in {o for p in cli_main.commands["setup"].params for o in p.opts}
    assert _run("list", "--all").exit_code != 0
    assert _run("setup", "--force").exit_code != 0


def test_the_mcp_tools_take_no_owner_arguments():
    assert list(inspect.signature(server.gapp_user).parameters) == ["account"]
    assert list(inspect.signature(server.gapp_list).parameters) == ["project_limit"]
    assert "force" not in inspect.signature(server.gapp_setup).parameters


def test_config_shows_no_owner_line():
    out = _run("config").output
    assert "app owner" not in out and "gcloud account:" in out


def test_an_owner_key_stops_any_command_with_a_plain_message(tmp_path):
    path = get_config_file()
    with open(path, "w") as f:
        yaml.dump({"active": "default", "profiles": {"default": {"discovery": "on", "owner": "alice"}}}, f)
    env = {"XDG_CONFIG_HOME": str(path.parent.parent), "PATH": "/usr/bin:/bin"}
    res = subprocess.run([sys.executable, "-c", "from gapp.admin.cli.main import cli_entry; cli_entry()", "list"],
                         capture_output=True, text=True, env=env)
    assert res.returncode == 1 and "Traceback" not in res.stderr
    assert f"Your gapp config {path} has an `owner:` key in profile 'default'." in res.stderr
    assert "delete that line, then run the command again." in res.stderr
