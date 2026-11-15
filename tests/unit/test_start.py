"""How an app starts: a Dockerfile, a Procfile with exactly one `web:` line,
or the definition's `start`, in that order. gapp reads the repo's files,
never writes them, and detects nothing."""

import subprocess
from pathlib import Path

import pytest

from gapp.admin.sdk import core as core_mod
from gapp.admin.sdk.start import START_METHOD_HELP, describe, missing, start_method_of


def start_method(root, start=None):
    """The start method of a checkout, read as a deploy reads it."""
    procfile = root / "Procfile"
    return start_method_of(dockerfile=(root / "Dockerfile").is_file(),
                           procfile=procfile.read_text() if procfile.is_file() else None, start=start)


def test_the_guidance_text_is_exact():
    assert START_METHOD_HELP == (
        "Add a Procfile with a web: line that starts the app and serves HTTP on $PORT, for example:\n"
        "  echo 'web: my-app serve' > Procfile\n"
        "  echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile\n"
        "or add a Dockerfile. For a repo you can't change, set the command in its definition instead:\n"
        "  gapp set start 'my-app serve'"
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


def test_the_definitions_start_is_the_fallback_when_there_is_no_dockerfile_or_procfile(tmp_path):
    start = start_method(tmp_path, "my-app serve")
    assert start == {"method": "start", "web": "my-app serve", "ignored": None, "reason": None}
    assert describe(start) == "my-app serve (the definition's start)"


def test_a_procfile_or_dockerfile_wins_over_start_and_start_is_reported_ignored(tmp_path):
    (tmp_path / "Procfile").write_text("web: other serve\n")
    start = start_method(tmp_path, "my-app serve")
    assert start["method"] == "procfile" and start["ignored"] == "the definition's start"
    assert describe(start) == "web: other serve (Procfile) (the definition's start is ignored)"
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    start = start_method(tmp_path, "my-app serve")
    assert start["ignored"] == "Procfile and the definition's start"
    assert describe(start) == "the Dockerfile (the Procfile and the definition's start are ignored)"


def test_start_does_not_stand_in_for_a_broken_procfile(tmp_path):
    (tmp_path / "Procfile").write_text("worker: my-app jobs\n")
    start = start_method(tmp_path, "my-app serve")
    assert start["method"] is None and start["reason"] == "its Procfile has no web: line"


def test_the_build_runs_start_as_the_image_command(tmp_path):
    (tmp_path / "README.md").write_text("an app\n")
    _git_repo(tmp_path)
    d = core_mod._prepare_build_dir(tmp_path, "img", start_method(tmp_path, "my-app serve --port $PORT"))
    assert (Path(d) / "Dockerfile").read_text().endswith("\nCMD exec my-app serve --port $PORT\n")
