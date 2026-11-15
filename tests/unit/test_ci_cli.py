"""How every `gapp ci` command ends: the result's lines and "Next:", or one
sentence on stderr with exit code 2 (refused, nothing changed) or 1
(failed). `--json` prints the result the MCP tool returns, and nothing else."""

import json

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.mcp import server
from gapp.admin.sdk import ci as ci_mod

FLEET = "acme/fleet"


def _run(*argv):
    res = CliRunner().invoke(main, ["ci", *argv])
    return res.exit_code, res.stdout, res.stderr


def test_a_deploy_prints_what_it_builds_then_what_the_run_did_then_the_next_command(ci):
    code, out, err = _run("deploy", "--deployment", "my-app")
    assert code == 0 and err == ""
    assert out.splitlines() == [
        "Deploying my-app: the default branch of me/my-app, to my-proj.",
        "my-app is deployed to my-proj, built from the default branch at abc1234.",
        "URL: https://my-app-abc-uc.a.run.app",
        "Run: https://github.com/acme/fleet/actions/runs/100",
        "Next: gapp ci status --deployment my-app --live",
    ]


def test_deploy_passes_upgrade_and_no_watch(ci, monkeypatch):
    seen = {}

    def deploy(deployment, **kwargs):
        seen.update(deployment=deployment, upgrade=kwargs["upgrade"], watch=kwargs["watch"])
        return {"ok": True, "lines": ["done"], "next_step": None}
    monkeypatch.setattr(ci_mod, "deploy", deploy)
    assert _run("deploy", "-d", "my-app", "--upgrade", "--no-watch")[0] == 0
    assert seen == {"deployment": "my-app", "upgrade": True, "watch": False}


def test_a_refusal_is_one_sentence_on_stderr_and_exit_2(ci):
    ci.define(FLEET, "staging", "me/my-app", "other-proj")
    code, out, err = _run("deploy", "--deployment", "staging")
    assert code == 2 and out == ""
    assert err.strip() == ("Runners in acme/fleet are not connected to other-proj, so nothing was started. "
                           "Connect them, once per project: gapp ci projects connect other-proj")


def test_a_failure_is_one_sentence_on_stderr_and_exit_1(ci):
    ci.deploy_outcome = lambda inputs: ("failure", ["error=Cloud Build failed"])
    code, out, err = _run("deploy", "--deployment", "my-app")
    assert code == 1 and err.strip().startswith("'my-app' did not deploy: Cloud Build failed (")
    assert out.splitlines() == ["Deploying my-app: the default branch of me/my-app, to my-proj."]


@pytest.mark.parametrize("argv, tool", [
    (["deploy", "--deployment", "my-app"], lambda: server.gapp_ci_deploy("my-app")),
    (["status", "--deployment", "my-app"], lambda: server.gapp_ci_status("my-app")),
    (["status", "--deployment", "my-app", "--live"], lambda: server.gapp_ci_status("my-app", live=True)),
    (["init"], server.gapp_ci_init),
    (["projects", "list"], lambda: server.gapp_ci_projects("list")),
    (["sources", "list"], lambda: server.gapp_ci_sources("list")),
    (["sources", "add", "other-org"], lambda: server.gapp_ci_sources("add", "other-org")),
])
def test_json_is_what_the_tool_returns_and_nothing_else(ci, argv, tool):
    code, out, err = _run(*argv, "--json")
    assert code == 0 and err == ""
    printed, returned = json.loads(out), tool()
    for volatile in ("request_id", "run_id", "run_url", "lines", "changed", "last_deploy"):
        printed.pop(volatile, None), returned.pop(volatile, None)
    assert printed == returned


def test_json_carries_a_refusal_with_exit_2(ci):
    code, out, err = _run("status", "--deployment", "nope", "--json")
    result = json.loads(out)
    assert code == 2 and err == "" and result["ok"] is False and result["refused"] is True
    assert "'nope' is not a deployment in your fleet" in result["next_step"]["hint"]


def test_status_and_its_live_form(ci):
    code, out, _ = _run("status", "-d", "my-app")
    assert code == 0 and out.splitlines()[1:] == ["No deploy run yet.", "Next: gapp ci deploy --deployment my-app"]
    code, out, _ = _run("status", "-d", "my-app", "--live")
    assert code == 0 and out.splitlines()[0].startswith("Asking my-proj what is live for my-app.")
    assert "  Healthy  yes" in out


def test_init_projects_and_sources_print_their_lines_and_next_step(ci):
    assert _run("init")[1].splitlines() == ["The workflows in acme/fleet are this gapp's already. Nothing changed.",
                                            "Next: gapp ci projects connect <project>"]
    assert _run("projects", "list")[1].splitlines() == ["Runners in acme/fleet can deploy to:", "  my-proj"]
    assert _run("sources", "list")[1].splitlines() == ["Runners may deploy repos owned by: me"]
    assert _run("sources", "remove", "me")[1].splitlines() == ["Runners in acme/fleet do not deploy repos owned by me."]


def test_projects_connect_takes_the_project_and_is_refused_without_one(ci):
    assert _run("projects", "connect")[0] == 2
    code, _, err = _run("projects", "connect", "P")
    assert code == 2 and "Name the project: gapp ci projects connect <project>" in err


def test_the_projects_tool_refuses_an_action_it_does_not_have():
    res = server.gapp_ci_projects("disconnect")
    assert res["ok"] is False and res["refused"] is True and res["next_step"]["action"] == "choose an action"


def test_no_ci_command_changes_the_fleet(ci):
    from gapp.admin.sdk import entryfile
    before = ci.files[(FLEET, entryfile.FILE_NAME)]
    for argv in (["init"], ["deploy", "-d", "my-app"], ["status", "-d", "my-app", "--live"],
                 ["sources", "add", "other-org"], ["projects", "list"]):
        assert _run(*argv)[0] == 0, argv
    assert ci.files[(FLEET, entryfile.FILE_NAME)] == before
