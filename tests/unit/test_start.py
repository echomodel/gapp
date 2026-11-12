"""How an app starts: a Dockerfile, or a Procfile with exactly one `web:`
line. gapp reads these files, never writes them, and detects nothing."""

import pytest
from pathlib import Path

from gapp.admin.sdk.start import START_METHOD_HELP, describe, missing, start_method


def test_the_guidance_text_is_exact():
    assert START_METHOD_HELP == (
        "Add a Procfile with a web: line that starts the app and serves HTTP on $PORT, for example:\n"
        "  echo 'web: my-app serve' > Procfile\n"
        "  echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile\n"
        "or add a Dockerfile. For a repo you can't change, set the command in gapp.yaml instead:\n"
        "  gapp manifest set start 'my-app serve'"
    )


def test_a_procfile_web_line_is_the_start_command(tmp_path):
    (tmp_path / "Procfile").write_text(
        "# how the app starts\n\nworker: my-app jobs\nweb:   uvicorn my_app.server:app --host 0.0.0.0 --port $PORT  \n")
    start = start_method(tmp_path)
    assert start == {"method": "procfile", "web": "uvicorn my_app.server:app --host 0.0.0.0 --port $PORT",
                     "ignored": None, "reason": None}
    assert describe(start) == "web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT (Procfile)"


def test_a_dockerfile_wins_and_the_procfile_is_reported_ignored(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    assert describe(start_method(tmp_path)) == "the Dockerfile"
    (tmp_path / "Procfile").write_text("web: my-app serve\n")
    start = start_method(tmp_path)
    assert start["method"] == "dockerfile" and start["ignored"] == "Procfile"
    assert describe(start) == "the Dockerfile (the Procfile is ignored)"


@pytest.mark.parametrize("text, reason", [
    ("worker: my-app jobs\n", "its Procfile has no web: line"),
    ("web:\n", "its Procfile has no web: line"),
    ("web: one\nweb: two\n", "its Procfile has more than one web: line"),
])
def test_a_procfile_without_exactly_one_web_command_is_no_start_method(tmp_path, text, reason):
    (tmp_path / "Procfile").write_text(text)
    start = start_method(tmp_path)
    assert start["method"] is None and start["reason"] == reason
    assert missing("my-app", start) == f"my-app has no start method: {reason}.\n{START_METHOD_HELP}"


def test_nothing_at_all_is_no_start_method(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")      # no detection from code
    start = start_method(tmp_path)
    assert start == {"method": None, "web": None, "ignored": None, "reason": None}
    assert missing("my-app", start, consequence=", so nothing was built or deployed") == (
        f"my-app has no start method, so nothing was built or deployed.\n{START_METHOD_HELP}")
    assert missing("my-app", start, service="api").startswith("my-app service 'api' has no start method.")


# -- the one check, used by deploy, status, setup and init --

import subprocess

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import core as core_mod
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _app(tmp_path, monkeypatch, procfile=None, gapp_yaml="name: my-app\n"):
    repo = tmp_path / "my-app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text(gapp_yaml)
    if procfile is not None:
        (repo / "Procfile").write_text(procfile)
    monkeypatch.chdir(repo)
    return repo


def test_deploy_without_a_start_method_refuses_before_any_cloud_call(tmp_path, monkeypatch, sdk):
    _app(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(sdk, "resolve_project_for_solution", lambda *a, **k: calls.append("resolve"))
    monkeypatch.setattr(sdk.provider, "bucket_exists", lambda *a, **k: calls.append("bucket"))
    monkeypatch.setattr(core_mod, "_prepare_build_dir", lambda *a, **k: calls.append("build"))
    with pytest.raises(RuntimeError) as exc:
        sdk.deploy(project_id="proj-1")
    assert str(exc.value) == f"my-app has no start method, so nothing was built or deployed.\n{START_METHOD_HELP}"
    assert calls == []


def test_deploy_names_the_service_without_a_start_method(tmp_path, monkeypatch, sdk):
    repo = _app(tmp_path, monkeypatch, gapp_yaml="name: my-app\npaths: [services/api, services/worker]\n")
    for p, procfile in (("services/api", "web: api serve\n"), ("services/worker", None)):
        (repo / p).mkdir(parents=True)
        (repo / p / "gapp.yaml").write_text(f"name: {p.split('/')[-1]}\n")
        if procfile:
            (repo / p / "Procfile").write_text(procfile)
    with pytest.raises(RuntimeError, match=r"^my-app service 'services/worker' has no start method, so nothing"):
        sdk.deploy(project_id="proj-1")


def test_dry_run_reports_the_problem_instead_of_refusing(tmp_path, monkeypatch, sdk):
    _app(tmp_path, monkeypatch)
    res = sdk.deploy(dry_run=True, project_id="proj-1")
    assert res["start_problem"].startswith("my-app has no start method, so nothing was built or deployed.")


def test_a_gapp_yaml_start_setting_is_refused_as_an_unknown_key(tmp_path, monkeypatch, sdk):
    from gapp.admin.sdk.schema import ManifestValidationError
    _app(tmp_path, monkeypatch, procfile="web: my-app serve\n", gapp_yaml="name: my-app\nservice:\n  cmd: my-app serve\n")
    with pytest.raises(ManifestValidationError, match=r"service\.cmd"):
        sdk.deploy(project_id="proj-1")


def test_status_names_the_start_method_as_the_next_step(tmp_path, monkeypatch, sdk):
    _app(tmp_path, monkeypatch)
    res = sdk.status()
    assert res.next_step.action == "add a start method"
    assert res.next_step.hint == f"my-app has no start method, so it can't be deployed.\n{START_METHOD_HELP}"


def test_setup_completes_and_names_the_start_method_next(tmp_path, monkeypatch, sdk):
    _app(tmp_path, monkeypatch)
    res = sdk.setup(project_id="proj-1")
    assert res["bucket"] and res["start_problem"] == (
        f"my-app has no start method, so it can't be deployed yet.\n{START_METHOD_HELP}")
    (tmp_path / "my-app" / "Procfile").write_text("web: my-app serve\n")
    assert sdk.setup(project_id="proj-1")["start_problem"] is None


def _git_repo(path):
    """A repo with one commit, made with plumbing so no commit hook runs."""
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    tree = subprocess.run(git + ["write-tree"], check=True, capture_output=True, text=True).stdout.strip()
    commit = subprocess.run(git + ["commit-tree", tree, "-m", "x"], check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(git + ["update-ref", "HEAD", commit], check=True)


def test_the_procfile_command_becomes_the_image_cmd_run_by_the_shell(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "Procfile").write_text("web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT\n")
    _git_repo(app)
    build = core_mod._prepare_build_dir(app, "img", start_method(app))
    dockerfile = (Path(build) / "Dockerfile").read_text()
    assert dockerfile.rstrip().endswith("CMD exec uvicorn my_app.server:app --host 0.0.0.0 --port $PORT")
    assert dockerfile.count("CMD ") == 1
    assert "ENTRYPOINT" not in (Path(build) / "cloudbuild.yaml").read_text()


def test_an_app_dockerfile_is_built_as_is(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "Dockerfile").write_text("FROM scratch\nCMD [\"own\"]\n")
    (app / "Procfile").write_text("web: ignored\n")
    _git_repo(app)
    build = core_mod._prepare_build_dir(app, "img", start_method(app))
    assert (Path(build) / "Dockerfile").read_text() == "FROM scratch\nCMD [\"own\"]\n"


def test_gapps_template_names_no_framework_and_supplies_no_server():
    template = (Path(core_mod.__file__).resolve().parent.parent.parent / "templates" / "Dockerfile").read_text()
    for word in ("mcp", "uvicorn", "ENTRYPOINT", "__cmd__", "CMD ["):
        assert word not in template


def test_cli_init_reports_the_start_method_or_says_not_deployable(tmp_path, monkeypatch):
    repo = _app(tmp_path, monkeypatch)
    monkeypatch.setattr(subprocess, "run", _no_gh(subprocess.run))
    result = CliRunner().invoke(cli_main, ["init"])
    assert result.exit_code == 0
    assert result.output.splitlines()[-6:] == [
        "  Not deployable yet: my-app has no start method.",
        "  Add a Procfile with a web: line that starts the app and serves HTTP on $PORT, for example:",
        "    echo 'web: my-app serve' > Procfile",
        "    echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile",
        "  or add a Dockerfile. For a repo you can't change, set the command in gapp.yaml instead:",
        "    gapp manifest set start 'my-app serve'",
    ]
    assert "Starts with" not in result.output and "Next: gapp setup" not in result.output
    (repo / "Procfile").write_text("web: my-app serve\n")
    out = CliRunner().invoke(cli_main, ["init"]).output
    assert "    Starts with: web: my-app serve (Procfile)\n" in out
    assert "  Next: gapp setup --project <project-id>" in out


def test_cli_init_says_already_set_in_words(tmp_path, monkeypatch):
    _app(tmp_path, monkeypatch, procfile="web: my-app serve\n")
    import json
    real = subprocess.run

    def gh(args, **kw):
        if args[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"repositoryTopics": [{"name": "gapp-solution"}]}), "")
        return real(args, **kw)
    monkeypatch.setattr(subprocess, "run", gh)
    out = CliRunner().invoke(cli_main, ["init"]).output
    assert "    GitHub topic 'gapp-solution' already set ✓" in out and "already_set" not in out


def _no_gh(real):
    def run(args, **kw):
        if args and args[0] == "gh":
            return subprocess.CompletedProcess(args, 1, "", "")
        return real(args, **kw)
    return run


def test_mcp_init_takes_no_start_setting():
    import inspect
    from gapp.admin.mcp.server import gapp_init
    fn = getattr(gapp_init, "fn", gapp_init)
    assert not {"entrypoint", "cmd"} & set(inspect.signature(fn).parameters)


def test_gapp_yaml_start_is_the_fallback_when_there_is_no_dockerfile_or_procfile(tmp_path):
    (tmp_path / "gapp.yaml").write_text("start: my-app serve\n")
    start = start_method(tmp_path)
    assert start == {"method": "start", "web": "my-app serve", "ignored": None, "reason": None}
    assert describe(start) == "my-app serve (gapp.yaml's start)"


def test_a_procfile_or_dockerfile_wins_over_start_and_start_is_reported_ignored(tmp_path):
    (tmp_path / "gapp.yaml").write_text("start: my-app serve\n")
    (tmp_path / "Procfile").write_text("web: other serve\n")
    start = start_method(tmp_path)
    assert start["method"] == "procfile" and start["ignored"] == "gapp.yaml's start"
    assert describe(start) == "web: other serve (Procfile) (gapp.yaml's start is ignored)"
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    start = start_method(tmp_path)
    assert start["ignored"] == "Procfile and gapp.yaml's start"
    assert describe(start) == "the Dockerfile (the Procfile and gapp.yaml's start are ignored)"


def test_start_does_not_stand_in_for_a_broken_procfile(tmp_path):
    (tmp_path / "gapp.yaml").write_text("start: my-app serve\n")
    (tmp_path / "Procfile").write_text("worker: my-app jobs\n")
    start = start_method(tmp_path)
    assert start["method"] is None and start["reason"] == "its Procfile has no web: line"


def test_the_build_runs_start_as_the_image_command(tmp_path):
    (tmp_path / "gapp.yaml").write_text("start: my-app serve --port $PORT\n")
    _git_repo(tmp_path)
    d = core_mod._prepare_build_dir(tmp_path, "img", start_method(tmp_path))
    assert (Path(d) / "Dockerfile").read_text().endswith("\nCMD exec my-app serve --port $PORT\n")
