"""Tests for gapp.sdk.status — infrastructure health check."""

import shutil
import subprocess
from pathlib import Path

import pytest

from gapp.admin.sdk.config import save_solutions
from gapp.admin.sdk.status import get_status, TerraformNotFoundError, GcloudNotFoundError


def _make_solution(tmp_path, monkeypatch, name="my-app", project_id=None):
    """Create a minimal git repo with gapp.yaml and register it."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], capture_output=True)

    (repo / "gapp.yaml").write_text(
        "service:\n"
        "  entrypoint: my_app.mcp.server:mcp_app\n"
    )

    entry = {"repo_path": str(repo)}
    if project_id:
        entry["project_id"] = project_id
    save_solutions({name: entry})
    monkeypatch.chdir(repo)
    return repo


def _path_without(command: str) -> str:
    """Return PATH with the directory containing `command` removed."""
    import os
    cmd_path = shutil.which(command)
    if not cmd_path:
        return os.environ["PATH"]
    cmd_dir = str(Path(cmd_path).parent)
    return ":".join(
        p for p in os.environ["PATH"].split(":")
        if p != cmd_dir
    )


def test_not_initialized_outside_solution(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    result = get_status()
    assert result.initialized is False
    assert result.next_step.action == "init"
    assert result.name is None
    assert result.deployment is None


def test_initialized_no_project(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _make_solution(tmp_path, monkeypatch)

    result = get_status()
    assert result.initialized is True
    assert result.name == "my-app"
    assert result.deployment.project is None
    assert result.deployment.pending is True
    assert result.next_step.action == "setup"


def test_terraform_not_installed_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    _make_solution(tmp_path, monkeypatch, project_id="my-project")

    # Remove terraform from PATH but keep git and gcloud
    monkeypatch.setenv("PATH", _path_without("terraform"))

    with pytest.raises(TerraformNotFoundError, match="terraform CLI is not installed"):
        get_status()
