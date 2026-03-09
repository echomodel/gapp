"""Tests for gapp.sdk.init — solution initialization."""

from pathlib import Path
from unittest.mock import patch

from gapp.sdk.config import load_solutions
from gapp.sdk.init import init_solution


def _make_git_repo(path: Path) -> Path:
    """Create a minimal git repo at path. Returns the repo root."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        capture_output=True,
        cwd=path,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        capture_output=True,
        cwd=path,
    )
    return path


@patch("gapp.sdk.init._add_github_topic", return_value="skipped")
def test_init_creates_manifest_and_dockerfile(mock_topic, tmp_path):
    repo = _make_git_repo(tmp_path / "my-app")
    result = init_solution(repo_path=repo)

    assert result["name"] == "my-app"
    assert result["manifest_status"] == "created"
    assert result["dockerfile_status"] == "created"
    assert (repo / "gapp.yaml").exists()
    assert (repo / "Dockerfile").exists()
    dockerfile = (repo / "Dockerfile").read_text()
    assert "ARG ENTRYPOINT" in dockerfile
    assert "uvicorn" in dockerfile


@patch("gapp.sdk.init._add_github_topic", return_value="skipped")
def test_init_existing_manifest_and_dockerfile(mock_topic, tmp_path):
    repo = _make_git_repo(tmp_path / "my-app")
    (repo / "gapp.yaml").write_text("solution:\n  name: custom\n")
    (repo / "Dockerfile").write_text("FROM custom:image\n")

    result = init_solution(repo_path=repo)

    assert result["name"] == "custom"
    assert result["manifest_status"] == "exists"
    assert result["dockerfile_status"] == "exists"
    # Should not overwrite existing Dockerfile
    assert (repo / "Dockerfile").read_text() == "FROM custom:image\n"


@patch("gapp.sdk.init._add_github_topic", return_value="skipped")
def test_init_registers_in_solutions(mock_topic, tmp_path):
    repo = _make_git_repo(tmp_path / "my-app")
    init_solution(repo_path=repo)

    solutions = load_solutions()
    assert "my-app" in solutions
    assert solutions["my-app"]["repo_path"] == str(repo)


@patch("gapp.sdk.init._add_github_topic", return_value="skipped")
def test_init_idempotent(mock_topic, tmp_path):
    repo = _make_git_repo(tmp_path / "my-app")

    result1 = init_solution(repo_path=repo)
    assert result1["manifest_status"] == "created"

    result2 = init_solution(repo_path=repo)
    assert result2["manifest_status"] == "exists"
    assert result2["name"] == "my-app"


def test_init_fails_outside_git_repo(tmp_path):
    import pytest

    with pytest.raises(RuntimeError, match="Not inside a git repository"):
        init_solution(repo_path=tmp_path)
