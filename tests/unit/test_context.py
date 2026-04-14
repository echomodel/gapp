"""Tests for gapp.sdk.context — solution context resolution."""

import subprocess
from pathlib import Path

from gapp.admin.sdk.config import save_solutions
from gapp.admin.sdk.context import get_git_root, resolve_solution


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True)
    return path


def test_get_git_root(tmp_path):
    repo = _make_git_repo(tmp_path / "repo")
    result = get_git_root(repo)
    assert result == repo


def test_get_git_root_not_a_repo(tmp_path):
    result = get_git_root(tmp_path)
    assert result is None


def test_resolve_by_name():
    save_solutions({"my-app": {"project_id": "proj-123", "repo_path": "/tmp/my-app"}})
    ctx = resolve_solution("my-app")
    assert ctx["name"] == "my-app"
    assert ctx["project_id"] == "proj-123"


def test_resolve_by_name_unknown():
    ctx = resolve_solution("nonexistent")
    assert ctx["name"] == "nonexistent"
    assert ctx["project_id"] is None


def test_resolve_from_cwd(tmp_path, monkeypatch):
    repo = _make_git_repo(tmp_path / "my-app")
    (repo / "gapp.yaml").write_text("name: my-app\n")
    monkeypatch.chdir(repo)

    ctx = resolve_solution()
    assert ctx["name"] == "my-app"


def test_resolve_none_outside_solution(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = resolve_solution()
    assert ctx is None
