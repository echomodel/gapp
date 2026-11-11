"""`gapp manifest`: show this repo's settings, and change them one at a time."""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import manifest_edit as m
from gapp.admin.sdk.init import init_solution
from gapp.admin.sdk.schema import Manifest, ManifestValidationError, validate_manifest

ROOT = Path(__file__).resolve().parents[2]


def _repo(tmp_path, monkeypatch, *, origin="https://github.com/acme/demo-app.git", files=None):
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if origin:
        subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", origin], check=True)
    for name, text in (files or {}).items():
        (repo / name).write_text(text)
    monkeypatch.chdir(repo)
    return repo


def _yaml(repo):
    return (repo / "gapp.yaml").read_text()


# -- show --

def test_show_reports_every_setting_with_its_default_when_unset(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "{}\n", "Procfile": "web: demo serve\n"})
    r = m.describe()
    rows = {row["key"]: row for row in r["settings"]}
    assert r["name"] == "demo-app" and r["name_source"] == "repo"
    assert rows["name"]["set"] is False and "not written" in rows["name"]["meaning"]
    assert rows["public"]["set"] is False and rows["public"]["meaning"].startswith("private")
    assert rows["domain"]["meaning"] == "the .run.app URL only"
    assert (rows["memory"]["value"], rows["cpu"]["value"], rows["max-instances"]["value"]) == ("512Mi", "1", 1)
    assert r["env"] == [] and r["start"]["web"] == "demo serve" and r["issues"] == []


def test_show_without_an_origin_names_the_folder(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, origin=None, files={"gapp.yaml": "{}\n"})
    r = m.describe()
    assert r["name"] == "checkout" and r["name_source"] == "folder"


def test_show_explains_each_kind_of_variable(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": (
        "env:\n"
        "  - name: LOG\n    value: INFO\n"
        "  - name: USERS\n    value: \"{{SOLUTION_DATA_PATH}}/users\"\n"
        "  - name: KEY\n    secret:\n      name: api-key\n"
        "  - name: SIGN\n    secret:\n      name: signing-key\n      generate: true\n")})
    env = {e["name"]: e for e in m.describe()["env"]}
    assert env["LOG"]["kind"] == "value"
    assert env["USERS"]["kind"] == "data_path" and "users" in env["USERS"]["meaning"]
    assert "gapp secrets set api-key" in env["KEY"]["meaning"]
    assert "generated on the first deploy" in env["SIGN"]["meaning"]


def test_show_lists_schema_problems_instead_of_failing(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": "publik: true\n"})
    r = m.describe()
    assert [i["path"] for i in r["issues"]] == ["publik"]


# -- set / unset --

def test_set_writes_the_value_and_only_when_it_changes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    r = m.set_value("public", "yes")
    assert r["changed"] and r["change"] == "public = true"
    assert yaml.safe_load(_yaml(repo)) == {"name": "demo-app", "public": True}
    (repo / "gapp.yaml").write_text("name: demo-app\npublic: true   # kept\n")
    again = m.set_value("public", "true")
    assert again["changed"] is False and "# kept" in _yaml(repo)


@pytest.mark.parametrize("key,value,path,expected", [
    ("domain", "app.example.com", ("domain",), "app.example.com"),
    ("memory", "1Gi", ("service", "memory"), "1Gi"),
    ("cpu", "2", ("service", "cpu"), "2"),
    ("max-instances", "3", ("service", "max_instances"), 3),
])
def test_set_writes_each_setting_where_gapp_reads_it(tmp_path, monkeypatch, key, value, path, expected):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    m.set_value(key, value)
    data = yaml.safe_load(_yaml(repo))
    for part in path:
        data = data[part]
    assert data == expected


@pytest.mark.parametrize("key,value", [
    ("public", "maybe"), ("max-instances", "0"), ("max-instances", "two"),
    ("domain", "https://app.example.com"), ("name", "Bad_Name"), ("memory", " "), ("colour", "red"),
    ("paths", "x"),
])
def test_set_refuses_a_bad_value_and_writes_nothing(tmp_path, monkeypatch, key, value):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    with pytest.raises(m.ManifestChangeRefused):
        m.set_value(key, value)
    assert _yaml(repo) == "name: demo-app\n"


def test_writes_need_a_gapp_yaml(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    with pytest.raises(m.ManifestChangeRefused, match="gapp init"):
        m.set_value("public", "true")
    assert not (repo / "gapp.yaml").exists()


def test_renaming_the_deployment_is_refused_without_new_deployment(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    with pytest.raises(m.ManifestChangeRefused) as e:
        m.set_value("name", "other")
    assert "new, empty deployment" in str(e.value) and "--new-deployment" in str(e.value)
    assert _yaml(repo) == "name: demo-app\n"
    r = m.set_value("name", "other", new_deployment=True)
    assert r["new_deployment"] == {"old_name": "demo-app", "new_name": "other"}
    assert yaml.safe_load(_yaml(repo))["name"] == "other"


def test_writing_the_name_the_deployment_already_has_needs_no_flag(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "public: true\n"})
    r = m.set_value("name", "demo-app")                  # the derived name, now pinned
    assert r["changed"] and "new_deployment" not in r
    m.unset_value("name")                                # back to derived: same name
    assert "name" not in yaml.safe_load(_yaml(repo))


def test_unsetting_a_name_that_differs_from_the_repo_is_guarded(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: custom\n"})
    with pytest.raises(m.ManifestChangeRefused, match="'custom' to 'demo-app'"):
        m.unset_value("name")


def test_unset_takes_only_the_settings_set_takes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\nbogus: true\n"})
    with pytest.raises(m.ManifestChangeRefused, match="Unknown setting"):
        m.unset_value("bogus")
    assert "bogus" in _yaml(repo)


def test_unset_removes_a_service_key_and_an_empty_section(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\nservice:\n  memory: 1Gi\n"})
    m.unset_value("memory")
    assert yaml.safe_load(_yaml(repo)) == {"name": "demo-app"}


def test_env_set_adds_each_kind_and_replaces_by_name(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    m.env_set("LOG", "INFO")
    m.env_set("USERS", "users", data_path=True)
    m.env_set("ROOT", data_path=True)
    m.env_set("KEY", secret="api-key")
    m.env_set("SIGN", secret="signing-key", generate=True)
    m.env_set("LOG", "DEBUG")
    assert yaml.safe_load(_yaml(repo))["env"] == [
        {"name": "LOG", "value": "DEBUG"},
        {"name": "USERS", "value": "{{SOLUTION_DATA_PATH}}/users"},
        {"name": "ROOT", "value": "{{SOLUTION_DATA_PATH}}"},
        {"name": "KEY", "secret": {"name": "api-key"}},
        {"name": "SIGN", "secret": {"name": "signing-key", "generate": True}},
    ]
    assert '"{{SOLUTION_DATA_PATH}}/users"' in _yaml(repo)          # gapp's documented quoting
    validate_manifest(yaml.safe_load(_yaml(repo)))


@pytest.mark.parametrize("kwargs", [
    {"value": None},
    {"value": "x", "secret": "api-key"},
    {"value": None, "generate": True},
    {"value": None, "secret": "api-key", "data_path": True},
    {"value": None, "secret": "Bad_Name"},
])
def test_env_set_refuses_an_ambiguous_variable(tmp_path, monkeypatch, kwargs):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    with pytest.raises(m.ManifestChangeRefused):
        m.env_set("X", **kwargs)
    assert _yaml(repo) == "name: demo-app\n"


def test_env_unset(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\nenv:\n  - name: LOG\n    value: INFO\n"})
    with pytest.raises(m.ManifestChangeRefused):
        m.env_unset("NOPE")
    m.env_unset("LOG")
    assert yaml.safe_load(_yaml(repo)) == {"name": "demo-app"}


# -- the example --

def _schema_paths(model, prefix=""):
    out = set()
    for name, field in model.model_fields.items():
        out.add(prefix + name)
        ann = field.annotation
        for arg in getattr(ann, "__args__", ()) or (ann,):
            inner = getattr(arg, "__args__", None)
            for cand in (inner or (arg,)):
                if isinstance(cand, type) and hasattr(cand, "model_fields"):
                    out |= _schema_paths(cand, prefix + name + ".")
    return out


def test_the_example_is_valid_and_covers_every_key():
    text = m.example()
    validate_manifest(yaml.safe_load(text))
    for path in _schema_paths(Manifest):
        assert re.search(rf"(^|\s|#\s+){re.escape(path.rsplit('.', 1)[-1])}:", text, re.M), path


def test_the_readme_shows_the_example_gapp_prints():
    assert m.example() in (ROOT / "README.md").read_text()


# -- init and the CLI --

def test_init_reports_where_the_name_came_from(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    assert init_solution()["name_source"] == "repo"
    assert init_solution()["name_source"] == "gapp.yaml"


def test_cli_init_says_the_name_and_where_settings_live(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"Procfile": "web: demo serve\n"})
    out = CliRunner().invoke(main, ["init"]).output
    assert "Name: demo-app (the origin repo's name, now written to gapp.yaml)" in out
    assert "makes a new deployment" in out and "gapp manifest" in out


def test_cli_manifest_alone_lists_its_commands(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    out = CliRunner().invoke(main, ["manifest"]).output
    for command in ("show", "set", "unset", "env", "example", "schema"):
        assert re.search(rf"^  {command}\b", out, re.M), command


def test_cli_manifest_shows_and_changes_settings(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    runner = CliRunner()
    shown = runner.invoke(main, ["manifest", "show"])
    assert shown.exit_code == 0 and "the .run.app URL only" in shown.output
    assert "Change: gapp manifest set|unset KEY" in shown.output and "gapp manifest example" in shown.output
    assert f"edit {repo / 'gapp.yaml'} for those" in shown.output.replace("\n  ", " ")      # hand-edited keys: where
    changed = runner.invoke(main, ["manifest", "env", "set", "SIGN", "--secret", "signing-key", "--generate"])
    assert changed.exit_code == 0 and "Changed: SIGN = secret 'signing-key', generated" in changed.output
    refused = runner.invoke(main, ["manifest", "set", "name", "other"])
    assert refused.exit_code == 1 and "Not changed" in refused.output
    assert yaml.safe_load(_yaml(repo))["name"] == "demo-app"
    as_json = runner.invoke(main, ["manifest", "show", "--json"])
    assert json.loads(as_json.output)["env"][0]["name"] == "SIGN"      # nothing but the JSON


def test_cli_schema_and_example_print_only_their_content():
    runner = CliRunner()
    assert runner.invoke(main, ["manifest", "example"]).output == m.example()
    assert json.loads(runner.invoke(main, ["manifest", "schema"]).output) == Manifest.model_json_schema()


# -- a gapp.yaml gapp can't use --

BAD_YAML = "name: demo-app\nenv:\n  - name: A\n   value: b\n"


@pytest.mark.parametrize("args", [["init"], ["manifest", "show"], ["manifest", "set", "public", "true"],
                                  ["secrets", "list"], ["deploy", "--dry-run"]])
def test_unreadable_yaml_gives_one_plain_message_and_changes_nothing(tmp_path, monkeypatch, args):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": BAD_YAML})
    result = _run_entry(args)
    assert result["code"] == 1
    assert "gapp.yaml can't be read as YAML: line 4, column 4" in result["err"]
    assert "gapp init --fresh" in result["err"] and "Traceback" not in result["err"]
    assert _yaml(repo) == BAD_YAML


@pytest.mark.parametrize("args", [["init"], ["secrets", "list"], ["manifest", "set", "public", "true"]])
def test_schema_problems_give_one_plain_message_and_change_nothing(tmp_path, monkeypatch, args):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\npublik: true\n"})
    result = _run_entry(args)
    assert result["code"] == 1
    assert "publik: unknown field" in result["err"] and "gapp init --fresh" in result["err"]
    assert not result["err"].lstrip().startswith("{")                 # not a JSON dump
    assert _yaml(repo) == "name: demo-app\npublik: true\n"


def test_show_names_schema_problems_and_the_way_out(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\npublik: true\n"})
    out = CliRunner().invoke(main, ["manifest", "show"]).output
    assert "publik: unknown field" in out and "gapp init --fresh" in out


def _run_entry(args):
    """Run the installed entry point's code path (cli_entry), capturing its exit and stderr."""
    import contextlib
    import io
    import sys
    from gapp.admin.cli.main import cli_entry
    err, out = io.StringIO(), io.StringIO()
    argv = sys.argv
    sys.argv = ["gapp", *args]
    code = 0
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            cli_entry()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = argv
    return {"code": code, "err": err.getvalue(), "out": out.getvalue()}


def test_init_fresh_keeps_the_old_file_and_its_name(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: custom\npublik: true\n"})
    res = init_solution(fresh=True)
    assert res["fresh_result"] == {"backup": "gapp.yaml.bak", "old_name": "custom", "old_name_unknown": False}
    assert res["manifest_status"] == "replaced" and res["name"] == "custom"
    assert yaml.safe_load(_yaml(repo)) == {"name": "custom"}
    assert (repo / "gapp.yaml.bak").read_text() == "name: custom\npublik: true\n"


def test_init_fresh_reads_the_name_line_of_a_file_that_isnt_yaml(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: custom\n" + BAD_YAML.split("\n", 1)[1]})
    res = init_solution(fresh=True)
    assert res["name"] == "custom" and res["fresh_result"]["old_name_unknown"] is False
    assert (repo / "gapp.yaml.bak").exists()


def test_init_fresh_says_when_the_old_name_is_unknown(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": "env:\n  - name: A\n   value: b\n"})
    out = CliRunner().invoke(main, ["init", "--fresh"]).output
    assert "Kept the old file as gapp.yaml.bak." in out
    assert "the old file's name couldn't be read" in out and "gapp manifest set name <name> --new-deployment" in out


def test_init_fresh_numbers_backups_and_never_deletes(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n", "gapp.yaml.bak": "older\n"})
    assert init_solution(fresh=True)["fresh_result"]["backup"] == "gapp.yaml.bak.1"
    assert init_solution(fresh=True)["fresh_result"]["backup"] == "gapp.yaml.bak.2"
    assert (repo / "gapp.yaml.bak").read_text() == "older\n"


def test_init_fresh_without_a_file_is_a_plain_init(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    res = init_solution(fresh=True)
    assert res["fresh_result"] is None and res["manifest_status"] == "created"


def test_init_fresh_asks_nothing(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, files={"gapp.yaml": BAD_YAML})
    result = CliRunner().invoke(main, ["init", "--fresh"], input="")
    assert result.exit_code == 0 and "[y/N]" not in result.output


def test_mcp_tools_report_a_broken_file_as_data(tmp_path, monkeypatch):
    from gapp.admin.mcp import server
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": BAD_YAML})
    assert server.gapp_manifest_show()["error"] == "manifest_unreadable"
    assert server.gapp_init()["error"] == "manifest_unreadable"
    assert server.gapp_init(fresh=True)["fresh_result"]["backup"] == "gapp.yaml.bak"
    assert (repo / "gapp.yaml.bak").read_text() == BAD_YAML


def test_the_ci_overlay_step_reports_a_broken_repo_file(tmp_path):
    from gapp.admin.sdk.ci import runner_overlay
    from gapp.admin.sdk.schema import ManifestUnreadable
    path = tmp_path / "gapp.yaml"
    path.write_text(BAD_YAML)
    with pytest.raises(ManifestUnreadable, match="line 4, column 4"):
        runner_overlay(str(path), "{}")


def _cli_help(cmd, ctx=None):
    import click
    ctx = click.Context(cmd, info_name=cmd.name, parent=ctx)
    yield cmd.get_help(ctx)
    for sub in getattr(cmd, "commands", {}).values():
        yield from _cli_help(sub, ctx)


def _message_strings():
    """Every string the CLI, MCP tools and SDK can show: help text, tool
    descriptions, and string literals that are not docstrings."""
    import ast
    from gapp.admin.mcp import server
    yield from _cli_help(main)
    yield from (fn.__doc__ or "" for name, fn in vars(server).items() if name.startswith("gapp_") and callable(fn))
    for path in (ROOT / "gapp").rglob("*.py"):
        tree = ast.parse(path.read_text())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                yield node.value


def test_no_message_sends_the_reader_to_files_the_cli_does_not_install():
    """Someone with only the installed CLI must not be pointed at the repo's docs."""
    hits = [t for t in _message_strings() if re.search(r"README|docs/|CONTRIBUTING\.md", t)]
    assert hits == []


# -- --file: a deployment's fleet-side manifest --

def test_file_target_is_created_on_the_first_change_and_has_no_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                                  # no git repo needed
    f = tmp_path / "staging.yaml"
    r = m.set_value("public", "true", file=f)
    assert r["changed"] and yaml.safe_load(f.read_text()) == {"public": True}
    m.env_set("KEY", secret="api-key", file=f)
    m.set_value("start", "my-app serve", file=f)
    assert yaml.safe_load(f.read_text()) == {"public": True, "env": [{"name": "KEY", "secret": {"name": "api-key"}}],
                                             "start": "my-app serve"}
    with pytest.raises(m.ManifestChangeRefused, match="has no name"):
        m.set_value("name", "x", file=f)
    m.env_unset("KEY", file=f)
    m.unset_value("start", file=f)
    assert yaml.safe_load(f.read_text()) == {"public": True}


def test_file_show_says_unset_keys_are_the_repos_to_decide(tmp_path):
    f = tmp_path / "staging.yaml"
    f.write_text("start: my-app serve\n")
    r = m.describe(file=f)
    rows = {row["key"]: row for row in r["settings"]}
    assert "name" not in rows and r["start"] is None
    assert rows["start"]["meaning"] == "starts the app when its repo has no Dockerfile or Procfile"
    assert rows["memory"]["value"] is None and "the app repo's gapp.yaml decides" in rows["memory"]["meaning"]


def test_file_problems_name_the_file_not_gapp_yaml(tmp_path):
    f = tmp_path / "staging.yaml"
    f.write_text(BAD_YAML)
    with pytest.raises(m.ManifestChangeRefused, match=r"staging.yaml can't be read as YAML: line 4, column 4"):
        m.set_value("public", "true", file=f)
    f.write_text("publik: true\n")
    with pytest.raises(m.ManifestChangeRefused, match=r"staging.yaml has settings gapp doesn't accept: publik"):
        m.set_value("public", "true", file=f)
    assert f.read_text() == "publik: true\n"


def test_cli_manifest_file_option_and_upload_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    res = runner.invoke(main, ["manifest", "--file", "s.yaml", "set", "memory", "1Gi"])
    assert res.exit_code == 0 and "Changed: memory = 1Gi" in res.output
    out = runner.invoke(main, ["manifest", "--file", "s.yaml", "show"]).output
    assert "--manifest s.yaml --update" in out and "name " not in out


def test_start_can_be_set_in_the_repo_and_shows_whether_it_is_used(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, files={"gapp.yaml": "name: demo-app\n"})
    m.set_value("start", "demo serve")
    rows = {row["key"]: row for row in m.describe()["settings"]}
    assert rows["start"]["meaning"] == "starts the app"
    (repo / "Procfile").write_text("web: demo web\n")
    rows = {row["key"]: row for row in m.describe()["settings"]}
    assert rows["start"]["meaning"] == "ignored: the Procfile starts the app"
