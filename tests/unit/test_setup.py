"""Tests for gapp.sdk.setup — GCP foundation setup."""

import subprocess
from pathlib import Path
from unittest.mock import patch, call

import pytest

from gapp.sdk.config import load_solutions, save_solutions
from gapp.sdk.setup import setup_solution


def _make_solution(tmp_path, monkeypatch, name="my-app"):
    """Create a minimal git repo with deploy/manifest.yaml and register it."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True)

    deploy = repo / "deploy"
    deploy.mkdir()
    (deploy / "manifest.yaml").write_text(
        "prerequisites:\n"
        "  apis:\n"
        "    - run.googleapis.com\n"
        "    - secretmanager.googleapis.com\n"
    )

    save_solutions({name: {"repo_path": str(repo)}})
    monkeypatch.chdir(repo)
    return repo


@patch("gapp.sdk.setup._label_project", return_value="added")
@patch("gapp.sdk.setup._create_bucket", return_value="created")
@patch("gapp.sdk.setup._enable_api")
def test_setup_with_explicit_project(mock_api, mock_bucket, mock_label, tmp_path, monkeypatch):
    _make_solution(tmp_path, monkeypatch)

    result = setup_solution("my-project")

    assert result["name"] == "my-app"
    assert result["project_id"] == "my-project"
    assert result["apis"] == ["run.googleapis.com", "secretmanager.googleapis.com"]
    assert result["bucket"] == "gapp-my-app-my-project"
    assert result["bucket_status"] == "created"
    assert result["label_status"] == "added"

    mock_api.assert_any_call("my-project", "run.googleapis.com")
    mock_api.assert_any_call("my-project", "secretmanager.googleapis.com")
    mock_bucket.assert_called_once_with("my-project", "gapp-my-app-my-project")
    mock_label.assert_called_once_with("my-project", "my-app")


@patch("gapp.sdk.setup._label_project", return_value="added")
@patch("gapp.sdk.setup._create_bucket", return_value="created")
@patch("gapp.sdk.setup._enable_api")
def test_setup_uses_cached_project(mock_api, mock_bucket, mock_label, tmp_path, monkeypatch):
    _make_solution(tmp_path, monkeypatch)
    solutions = load_solutions()
    solutions["my-app"]["project_id"] = "cached-proj"
    save_solutions(solutions)

    result = setup_solution()

    assert result["project_id"] == "cached-proj"


@patch("gapp.sdk.setup._label_project", return_value="added")
@patch("gapp.sdk.setup._create_bucket", return_value="created")
@patch("gapp.sdk.setup._enable_api")
def test_setup_saves_project_to_cache(mock_api, mock_bucket, mock_label, tmp_path, monkeypatch):
    _make_solution(tmp_path, monkeypatch)

    setup_solution("new-proj")

    solutions = load_solutions()
    assert solutions["my-app"]["project_id"] == "new-proj"


@patch("gapp.sdk.setup._label_project", return_value="exists")
@patch("gapp.sdk.setup._create_bucket", return_value="exists")
@patch("gapp.sdk.setup._enable_api")
def test_setup_idempotent(mock_api, mock_bucket, mock_label, tmp_path, monkeypatch):
    _make_solution(tmp_path, monkeypatch)

    result1 = setup_solution("proj")
    result2 = setup_solution("proj")

    assert result2["bucket_status"] == "exists"
    assert result2["label_status"] == "exists"


def test_setup_fails_outside_solution(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(RuntimeError, match="Not inside a gapp solution"):
        setup_solution("proj")


@patch("gapp.sdk.setup._discover_project_from_label", return_value=None)
def test_setup_fails_no_project(mock_discover, tmp_path, monkeypatch):
    _make_solution(tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="No GCP project specified"):
        setup_solution()
