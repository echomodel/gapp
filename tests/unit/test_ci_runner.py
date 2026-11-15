"""What a workflow runs on the runner: `gapp ci runner deploy|status
<deployment>`. Each prints one line `gapp ci` finds in the run's log, and
exits 0, 2 or 1 for done, refused or failed."""

import json

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import ci_runner, core, entryfile, fleetstore
from gapp.admin.sdk.definitions import Refused


@pytest.fixture
def seen(monkeypatch):
    calls = []
    monkeypatch.setattr(core.GappSDK, "deploy_definition",
                        lambda self, deployment, upgrade=False: calls.append(("deploy", deployment, upgrade)) or {"commit": "abc"})
    monkeypatch.setattr(core.GappSDK, "status_definition",
                        lambda self, deployment: calls.append(("status", deployment)) or {"healthy": True})
    return calls


def test_deploy_prints_the_result_as_one_line_and_exits_0(seen):
    out = []
    assert ci_runner.runner_deploy("my-app", out=out.append) == 0
    assert out == ['gapp-deploy: result={"commit": "abc"}'] and seen == [("deploy", "my-app", False)]


def test_status_prints_the_result_as_one_line_and_exits_0(seen):
    out = []
    assert ci_runner.runner_status("my-app", out=out.append) == 0
    assert out == ['gapp-status: result={"healthy": true}'] and seen == [("status", "my-app")]


def test_a_refusal_is_one_line_and_exit_2(monkeypatch):
    def refuse(self, deployment, upgrade=False):
        raise Refused("'my-app' has an upgrade pending.\n  Deploy it with: gapp deploy --deployment my-app --upgrade")
    monkeypatch.setattr(core.GappSDK, "deploy_definition", refuse)
    out = []
    assert ci_runner.runner_deploy("my-app", out=out.append) == 2
    assert out == ["gapp-deploy: refused='my-app' has an upgrade pending. Deploy it with: gapp deploy --deployment my-app --upgrade"]


def test_a_failure_is_its_first_line_and_exit_1_never_a_traceback(monkeypatch):
    def fail(self, deployment, upgrade=False):
        raise RuntimeError("Cloud Build failed\nlots of detail")
    monkeypatch.setattr(core.GappSDK, "deploy_definition", fail)
    out = []
    assert ci_runner.runner_deploy("my-app", out=out.append) == 1
    assert out == ["gapp-deploy: error=Cloud Build failed"]


def test_the_commands_take_the_name_and_upgrade_and_exit_with_the_runners_code(seen):
    res = CliRunner().invoke(main, ["ci", "runner", "deploy", "my-app", "--upgrade"])
    assert res.exit_code == 0 and res.output.strip() == 'gapp-deploy: result={"commit": "abc"}'
    assert seen == [("deploy", "my-app", True)]
    res = CliRunner().invoke(main, ["ci", "runner", "status", "my-app"])
    assert res.exit_code == 0 and json.loads(res.output.split("result=", 1)[1]) == {"healthy": True}


def test_the_runner_commands_are_not_listed_for_people():
    listed = CliRunner().invoke(main, ["ci", "--help"]).output.split("Commands:")[1]
    names = [line.split()[0] for line in listed.splitlines() if line.strip()]
    assert names == ["deploy", "init", "projects", "sources", "status"]


def test_on_a_runner_the_fleet_is_the_file_the_workflow_checked_out(monkeypatch, tmp_path):
    from gapp.admin.sdk import config as config_mod
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_mod.set_fleet_repo("acme/fleet")
    checked_out = tmp_path / "fleet" / entryfile.FILE_NAME
    checked_out.parent.mkdir()
    definition = entryfile.new_definition(id="my-app", project="my-proj", repo="me/my-app")
    checked_out.write_text(entryfile.dump({"deployments": {"my-app": definition}}))
    monkeypatch.setenv("GAPP_FLEET_FILE", str(checked_out))
    where = fleetstore.location()
    assert (where.kind, where.path) == ("local", checked_out)       # nothing is fetched, nothing pushed back
    assert fleetstore.read() == {"my-app": definition}
