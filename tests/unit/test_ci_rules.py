"""The rules of the CI system (CONTRIBUTING, "Rules of the CI system"), each
guarded here: only `ci init` sets the fleet repo and every other command
stops with one message without it; ci never reads the working directory;
"missing" is said only when GitHub confirms it, otherwise "couldn't check"
in GitHub's words; the ci group mirrors the root verbs; "CI repo" is gone
from everything the operator reads. Plus the runner-side code the fleet
repo's workflows execute.
"""

import re
import json
import pathlib
import shutil

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import CI_PARITY_VERBS, ci_group, main as cli_main
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import fleet

FLEET = "acme/fleet"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# -- rule 1: only ci init sets the fleet repo --


def test_every_ci_command_without_a_fleet_repo_stops_with_the_one_message(monkeypatch, fake_gh, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh")
    fake_gh.topics["me/fleet"] = {"gapp-ci"}                     # a tagged repo exists; nobody but init may find it
    calls = {
        "setup": lambda: ci_mod.setup_deployment("x", repo="me/x", project="my-proj"),
        "rename": lambda: ci_mod.rename_deployment("x", "y"),
        "deploy": lambda: ci_mod.deploy_deployment("x", watch=False),
        "status": lambda: ci_mod.status_deployment("x"),
        "list": lambda: ci_mod.list_deployments(),
        "sources": lambda: ci_mod.manage_sources("list"),
        "project setup": lambda: ci_mod.setup_ci_project("my-proj"),
    }
    for name, call in calls.items():
        res = call()
        assert res["ok"] is False and res["next_step"]["hint"] == ci_mod.NO_FLEET_REPO_MESSAGE, name
    assert not any(c[:3] == ["gh", "search", "repos"] for c, _ in fake_gh.calls)   # no lookup, no caching
    assert config_mod.get_fleet_repo() == (None, None)
    assert ci_mod.NO_FLEET_REPO_MESSAGE == (
        "No fleet repo is set on this machine. Start here: gapp ci init "
        "(It finds your existing fleet repo, or tells you how to create one.)")


def test_the_fleet_repo_lives_in_gapps_local_config_per_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_mod.get_fleet_repo() == (None, None)
    config_mod.set_fleet_repo("acme/fleet", "gapp ci init acme/fleet")
    assert config_mod.get_fleet_repo() == ("acme/fleet", "gapp ci init acme/fleet")
    assert yaml.safe_load((tmp_path / "xdg" / "gapp" / "config.yaml").read_text())["profiles"]["default"]["fleet_repo"] == "acme/fleet"
    config_mod.set_fleet_repo(None)
    assert config_mod.get_fleet_repo() == (None, None)


# -- rule 2: ci never reads the working directory --


@pytest.fixture
def three_directories(tmp_path):
    """An unrelated directory, another app's checkout, and a directory that is no git repo."""
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    checkout = tmp_path / "other-app"
    (checkout / ".git").mkdir(parents=True)                    # a cwd-reading command would shell out to git here
    (checkout / "gapp.yaml").write_text("name: other-app\nservice:\n  entrypoint: other:app\n")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "gapp.yaml").write_text("name: plain-app\n")
    return [unrelated, checkout, plain]


def test_every_ci_command_gives_the_same_result_from_any_directory_and_only_invokes_gh(
        monkeypatch, fleet_repo, fake_gh, three_directories):
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    fake_gh.private["me/my-app"] = False
    fake_gh.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    fake_gh.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": "p", "GAPP_DEPLOY_SERVICE_ACCOUNT": "s"}
    fake_gh.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    commands = [
        ["ci", "init"],
        ["ci", "init", FLEET],
        ["ci", "sources", "list"],
        ["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"],           # unchanged: no run
        ["ci", "setup", "my-app-2", "--copy-of", "my-app", "--no-watch"],
        ["ci", "rename", "zzz", "yyy"],                                          # refused: no run
        ["ci", "deploy", "my-app", "--no-watch"],
        ["ci", "status", "my-app"],
        ["ci", "status", "my-app", "--live"],
        ["ci", "status", "my-app", "--json"],
        ["ci", "list"],
        ["ci", "list", "--repo", "me/my-app", "--verbose"],
        ["ci", "project", "setup", "my-proj"],                                   # no gcloud here: an outcome
    ]
    outputs = {}
    for directory in three_directories:
        monkeypatch.chdir(directory)
        fake_gh.calls.clear()
        runs_before = dict(fake_gh.runs)
        fake_gh.runs = dict(runs_before)
        fake_gh._next_run = 100
        for argv in commands:
            result = CliRunner().invoke(cli_main, argv)
            # A run is dated when it starts, so the clock is not part of what must match.
            output = re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<time>", result.output)
            outputs.setdefault(tuple(argv), []).append((result.exit_code, output))
        assert all(cmd[0] == "gh" for cmd, _ in fake_gh.calls)
        # the copy made in this directory is undone so the next directory starts equal
        doc = fake_gh.deployments(FLEET)
        doc.pop("my-app-2", None)
        fake_gh.files[(FLEET, fleet.DEPLOYMENTS_FILE)] = fleet.dump(doc)
    for argv, seen in outputs.items():
        assert seen[0] == seen[1] == seen[2], f"{argv} differs by directory:\n{seen}"
    assert "Traceback" not in "".join(o for seen in outputs.values() for _, o in seen)


def test_the_ci_module_has_no_working_directory_or_git_dependency():
    source = (REPO_ROOT / "gapp" / "admin" / "sdk" / "ci.py").read_text()
    for forbidden in ("resolve_solution", "discover_github_repo", "getcwd", "Path.cwd", "load_manifest", "GappSDK", '"git"'):
        assert forbidden not in source, forbidden
    # The one touch of the direct system's code runs on the runner only.
    runner = (REPO_ROOT / "gapp" / "admin" / "sdk" / "ci_runner.py").read_text()
    assert "GappSDK().status(name=solution_id, project=project)" in runner
    source = (REPO_ROOT / "gapp" / "admin" / "sdk" / "fleet.py").read_text()
    assert "import os" not in source and "subprocess" not in source


# -- rule 3: parity with the root verbs --


def test_the_ci_group_mirrors_the_root_verbs():
    for verb in CI_PARITY_VERBS:
        assert verb in cli_main.commands, f"root has no {verb}"
        assert verb in ci_group.commands, f"ci has no {verb}"
        assert f"Mirrors `gapp {verb}`" in (ci_group.commands[verb].help or ""), verb
    assert set(CI_PARITY_VERBS) == {"setup", "deploy", "status", "list"}


# -- rule 4: the from-nothing story is in the help --


def test_ci_help_tells_the_from_nothing_to_first_deploy_story():
    out = CliRunner().invoke(cli_main, ["ci", "--help"]).output
    assert "From nothing to first deploy" in out
    for step in ("gapp ci init", "--create", "gapp ci sources add", "gapp ci project setup", "gapp ci setup", "gapp ci deploy"):
        assert step in out, step


# -- rule 5: missing only when GitHub says so --


def test_an_unreadable_fleet_repo_is_could_not_check_in_githubs_words_never_missing(fleet_repo, fake_gh):
    fake_gh.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    fake_gh.unreadable.add(FLEET)
    expected = ("Couldn't check gapp/deployments.yaml in acme/fleet: GitHub returned 403: GitHub access to this "
                "repository is not enabled for this session. Request: GET repos/acme/fleet/contents/gapp/deployments.yaml")
    for res in (ci_mod.status_deployment("my-app"), ci_mod.deploy_deployment("my-app", watch=False),
                ci_mod.list_deployments(), ci_mod.setup_deployment("my-app", repo="me/my-app", project="my-proj"),
                ci_mod.rename_deployment("my-app", "x")):
        assert res["ok"] is False and res["next_step"]["hint"] == expected
        assert "missing" not in res["next_step"]["hint"] and "not registered" not in res["next_step"]["hint"]
    res = ci_mod.list_deployments(verbose=True)
    assert res["next_step"]["hint"] == expected


def test_a_404_names_both_readings_and_the_login(fleet_repo, fake_gh):
    fake_gh.login = "octocat"
    fake_gh.private.pop(FLEET)                                   # the fleet repo itself answers 404
    res = ci_mod.list_deployments()
    assert res["next_step"]["hint"] == (
        "Couldn't check gapp/deployments.yaml in acme/fleet: GitHub returned 404 Not Found: it doesn't exist, or "
        "your GitHub login (octocat) can't see it. Request: GET repos/acme/fleet/contents/gapp/deployments.yaml")


def test_a_file_404_on_a_visible_repo_is_confirmed_missing(fleet_repo, fake_gh):
    del fake_gh.files[(FLEET, fleet.DEPLOYMENTS_FILE)]
    res = ci_mod.list_deployments()
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"
    assert res["next_step"]["hint"] == f"acme/fleet has no gapp/deployments.yaml, so it holds no deployments yet. Install gapp's files: gapp ci init acme/fleet"


def test_an_unreadable_environment_is_not_reported_as_untrusted(fleet_repo, fake_gh):
    fake_gh.private["me/my-app"] = False
    fake_gh.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    fake_gh.environments[(FLEET, "my-proj")] = {}
    fake_gh.fail_with["environments/my-proj"] = "gh: HTTP 500: Internal Server Error"
    res = ci_mod.setup_deployment(None, repo="me/my-app", project="my-proj", watch=False)
    assert res["dispatched"] is True                             # the run decides; nothing was called untrusted
    fake_gh.deploy(FLEET, "my-app", "me/my-app", "my-proj")
    res = ci_mod.status_deployment("my-app")
    assert res["ok"] is True and res["next_step"] is None and "environment_record" not in res


def test_gh_failures_are_passed_through_trimmed():
    assert ci_mod._gh_failure("gh: HTTP 403: request blocked: no rule allows host (https://api.github.com/repos/x)") == (403, "request blocked: no rule allows host")
    assert ci_mod._gh_failure("gh: Not Found (HTTP 404)") == (404, "Not Found")
    assert ci_mod._gh_failure("HTTP 401: Bad credentials") == (401, "Bad credentials")
    assert ci_mod._gh_failure("dial tcp: connection refused") == (None, "dial tcp: connection refused")


# -- the term --


def test_nothing_the_operator_reads_says_ci_repo_or_solutions_yaml():
    paths = [
        REPO_ROOT / "gapp" / "admin" / "sdk" / "ci.py", REPO_ROOT / "gapp" / "admin" / "sdk" / "fleet.py",
        REPO_ROOT / "gapp" / "admin" / "cli" / "main.py", REPO_ROOT / "gapp" / "admin" / "mcp" / "server.py",
        REPO_ROOT / "gapp" / "templates" / "ci" / "gapp-deploy.yml", REPO_ROOT / "gapp" / "templates" / "ci" / "gapp-register.yml",
        REPO_ROOT / ".github" / "workflows" / "deploy.yml", REPO_ROOT / "README.md", REPO_ROOT / "docs" / "CI.md",
        *sorted((REPO_ROOT / "skills" / "gapp").rglob("*.md")),
    ]
    for path in paths:
        text = path.read_text()
        assert "CI repo" not in text and "CI Repo" not in text, path
        if path.name not in ("ci.py", "main.py", "server.py", "CI.md", "CONTRIBUTING.md"):
            assert "solutions.yaml" not in text, path                # only the 3.9 migration note may name it


# -- what the runner executes --


@pytest.fixture
def runner(fleet_repo, fake_gh):
    fake_gh.private["me/my-app"] = False
    fake_gh.environments[(FLEET, "my-proj")] = {}
    return fake_gh


def _env(**over):
    env = {"FLEET_REPO": FLEET, "OPERATION": "set", "NAME": "my-app", "REPO": "me/my-app", "PROJECT": "my-proj",
           "CHANGES": "{}", "SOURCE": "", "ALLOWED_SOURCES": "me,other-org", "APP_ID": "", "APP_KEY_SET": "false"}
    env.update(over)
    return env


def _run(env):
    lines = []
    rc = ci_mod.runner_register(env, out=lines.append)
    return rc, lines


def test_runner_adds_an_entry_with_a_sha_checked_write_and_reports_name_id_project(runner):
    rc, lines = _run(_env(CHANGES=json.dumps({"domain": "mcp.example.com"})))
    assert rc == 0
    assert lines[:4] == ["gapp-register: result=added", "gapp-register: name=my-app", "gapp-register: id=my-app",
                         "gapp-register: project=my-proj"]
    assert lines[4] == "gapp-register: added 'my-app' -> me/my-app on project 'my-proj'."
    assert runner.deployments(FLEET) == {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com"}}
    put = [c for c, k in runner.calls if "-X" in c and "PUT" in c]
    assert len(put) == 1
    assert json.loads([k for c, k in runner.calls if "-X" in c and "PUT" in c][0]["input"])["sha"].startswith("sha-")


def test_runner_reports_unchanged_without_writing_and_refuses_each_case_with_its_code(runner):
    _run(_env())
    writes = len(runner.puts)
    rc, lines = _run(_env())
    assert rc == 0 and lines[0] == "gapp-register: result=unchanged" and len(runner.puts) == writes
    assert _run(_env(ALLOWED_SOURCES="other-org"))[1][0] == "gapp-register: result=source-owner-not-allowed"
    assert _run(_env(NAME="x", REPO="me/x", PROJECT="other-proj"))[1][0] == "gapp-register: result=project-not-trusted"
    rc, lines = _run(_env(NAME="my-app-2", REPO="me/my-app"))
    assert lines[0] == "gapp-register: result=repo-deployed"
    rc, lines = _run(_env(NAME="my-app", REPO="me/other"))
    assert lines[0] == "gapp-register: result=name-taken"
    runner.private.pop("me/my-app")
    assert _run(_env(NAME="y", REPO="me/my-app"))[1][0] in ("gapp-register: result=repo-deployed",)
    runner.private["me/other"] = True                            # private, no hosting app
    assert _run(_env(NAME="other", REPO="me/other"))[1][0] == "gapp-register: result=repo-unreachable"
    assert _run(_env(NAME="bad name"))[1][0] == "gapp-register: result=invalid-input"
    assert _run(_env(OPERATION="frob"))[1][0] == "gapp-register: result=invalid-input"
    assert _run(_env(CHANGES="[1]"))[1][0] == "gapp-register: result=invalid-input"


def test_runner_copies_and_renames(runner):
    _run(_env(CHANGES=json.dumps({"env": [{"name": "A", "value": "1"}]})))
    rc, lines = _run(_env(OPERATION="copy", NAME="my-app-2", REPO="", PROJECT="", SOURCE="my-app"))
    assert rc == 0 and lines[0] == "gapp-register: result=added" and "copy of 'my-app'" in lines[4]
    doc = runner.deployments(FLEET)
    assert doc["my-app-2"]["id"] == "my-app-2" and doc["my-app-2"]["env"] == doc["my-app"]["env"]
    rc, lines = _run(_env(OPERATION="rename", NAME="renamed", REPO="", PROJECT="", SOURCE="my-app"))
    assert rc == 0 and lines[0] == "gapp-register: result=renamed" and "id 'my-app'" in lines[4]
    assert runner.deployments(FLEET)["renamed"]["id"] == "my-app"


def test_runner_fails_cleanly_when_the_file_changed_during_the_run(runner, monkeypatch):
    original = runner._put

    def stale(endpoint, payload):
        payload = {**payload, "sha": "stale"}
        return original(endpoint, payload)

    runner._put = stale
    rc, lines = _run(_env())
    assert rc == 1 and lines[0] == "gapp-register: result=write-rejected"
    assert "GitHub returned 409" in lines[1] and "dispatch again" in lines[1]


def test_runner_names_a_missing_deployments_file(runner):
    del runner.files[(FLEET, fleet.DEPLOYMENTS_FILE)]
    rc, lines = _run(_env())
    assert rc == 1 and lines[0] == "gapp-register: result=data-file-missing"


def test_runner_overlay_merges_over_the_repo_file_or_writes_it_whole(tmp_path):
    path = tmp_path / "gapp.yaml"
    path.write_text(yaml.safe_dump({"name": "from-repo", "service": {"entrypoint": "a:app"},
                                    "env": [{"name": "KEY", "secret": {"name": "key"}}]}))
    merged = ci_mod.runner_overlay(str(path), json.dumps({"name": "my-app-2", "domain": "d.example.com",
                                                          "env": [{"name": "LOG", "value": "x"}]}))
    assert merged["name"] == "my-app-2"                           # the deployment governs over the repo's name
    assert merged["service"] == {"entrypoint": "a:app"} and merged["domain"] == "d.example.com"
    assert [e["name"] for e in merged["env"]] == ["KEY", "LOG"]
    assert yaml.safe_load(path.read_text()) == merged
    fresh = tmp_path / "none" / "gapp.yaml"
    fresh.parent.mkdir()
    merged = ci_mod.runner_overlay(str(fresh), json.dumps({"name": "x", "service": {"entrypoint": "b:app"}}), domain="e.example.com")
    assert merged == {"name": "x", "service": {"entrypoint": "b:app"}, "domain": "e.example.com"}
    assert yaml.safe_load(fresh.read_text()) == merged


# -- REST only: no gh subcommand that reaches for GraphQL --


def test_the_ci_module_calls_github_only_through_gh_api():
    import re as _re
    source = (REPO_ROOT / "gapp" / "admin" / "sdk" / "ci.py").read_text()
    code = _re.sub(r'"""[\s\S]*?"""', "", source)                # docstrings may name the subcommands
    for sub in ('"workflow", "run"', '"run", "view"', '"run", "watch"', '"repo", "create"', '"repo", "edit"',
                '"repo", "view"', '"search", "repos"', '"pr",', "graphql"):
        assert sub not in code, sub
    assert not _re.search(r'_gh\(\["(?!api")', code)


def test_runner_refuses_a_key_gapp_yaml_does_not_have(runner):
    rc, lines = _run(_env(CHANGES=json.dumps({"memroy": "1Gi"})))
    assert rc != 0 and FLEET not in [k[0] for k in runner.puts]
    assert any("'memroy' is not a gapp.yaml key (check for typos); `gapp manifest schema` lists them." in l for l in lines)
