"""On a runner the workflows run gapp's own commands, `gapp deploy --json`
and `gapp status --json`, with the fleet named by GAPP_FLEET. There is no
command of gapp's that only a workflow runs."""

import os

from click.testing import CliRunner

from gapp.admin.cli.main import _json_only_on_stdout, main
from gapp.admin.sdk import entryfile, fleetstore


def test_there_is_no_runner_only_command():
    listed = CliRunner().invoke(main, ["ci", "--help"]).output.split("Commands:")[1]
    names = [line.split()[0] for line in listed.splitlines() if line.strip()]
    assert names == ["deploy", "init", "projects", "status"]
    assert "runner" not in main.commands["ci"].commands


def test_on_a_runner_the_fleet_is_the_file_the_workflow_checked_out(monkeypatch, tmp_path):
    from gapp.admin.sdk import config as config_mod
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_mod.set_fleet(repo="acme/fleet")
    checked_out = tmp_path / "fleet" / entryfile.FILE_NAME
    checked_out.parent.mkdir()
    definition = entryfile.new_definition(id="my-app", project="my-proj", repo="me/my-app")
    checked_out.write_text(entryfile.dump({"deployments": {"my-app": definition}}))
    monkeypatch.setenv("GAPP_FLEET", str(checked_out))
    where = fleetstore.location()
    assert (where.kind, where.path) == ("local", checked_out)       # nothing is fetched, nothing pushed back
    assert fleetstore.read() == {"my-app": definition}


def test_with_json_what_other_tools_print_goes_to_stderr(capfd):
    with _json_only_on_stdout(True):
        os.write(1, b"terraform says things\n")
    os.write(1, b"the result\n")
    out, err = capfd.readouterr()
    assert out == "the result\n" and err == "terraform says things\n"
