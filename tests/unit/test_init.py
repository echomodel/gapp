"""Tests for gapp.sdk.init — local project initialization."""

import json
import subprocess
from pathlib import Path
import pytest
from gapp.admin.sdk.init import init_solution
from gapp.admin.sdk.manifest import load_manifest


def test_init_creates_manifest(tmp_path, monkeypatch):
    """Verify init_solution creates a gapp.yaml with name derived from dir."""
    repo = tmp_path / "my-solution"
    repo.mkdir()
    (repo / ".git").mkdir()
    
    # We must be IN the repo for init to find the name from directory
    monkeypatch.chdir(repo)
    
    res = init_solution(repo)
    assert res["name"] == "my-solution"
    assert res["manifest_status"] == "created"
    assert (repo / "gapp.yaml").exists()
    
    manifest = load_manifest(repo)
    assert manifest["name"] == "my-solution"


def _repo(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    return repo


def test_init_reports_the_procfile_web_command_and_writes_no_start_setting(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    (repo / "Procfile").write_text("web: my-app serve\n")
    res = init_solution(repo)
    assert res["start"] == {"method": "procfile", "web": "my-app serve", "ignored": None, "reason": None}
    assert res["next_step"] is None
    assert "service" not in load_manifest(repo)
    assert (repo / "Procfile").read_text() == "web: my-app serve\n"          # read, never written


def test_init_reports_the_dockerfile_and_an_ignored_procfile(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    (repo / "Dockerfile").write_text("FROM scratch\n")
    (repo / "Procfile").write_text("web: my-app serve\n")
    res = init_solution(repo)
    assert res["start"]["method"] == "dockerfile" and res["start"]["ignored"] == "Procfile"
    assert res["next_step"] is None


def test_init_without_a_start_method_succeeds_and_says_not_deployable(tmp_path, monkeypatch):
    from gapp.admin.sdk.start import START_METHOD_HELP
    repo = _repo(tmp_path, monkeypatch)
    res = init_solution(repo)
    assert res["manifest_status"] == "created" and res["start"]["method"] is None
    assert res["next_step"] == {"action": "add a start method",
                                "hint": f"app has no start method.\n{START_METHOD_HELP}"}
    assert not (repo / "Procfile").exists()                                  # nothing detected or written


def test_init_takes_no_start_setting():
    import inspect
    assert "entrypoint" not in inspect.signature(init_solution).parameters
    assert "cmd" not in inspect.signature(init_solution).parameters


def test_init_adds_secrets(tmp_path, monkeypatch):
    """Verify init_solution adds prerequisite secrets."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    
    init_solution(repo, secrets={"api-key": "Internal API token"})
    manifest = load_manifest(repo)
    assert "api-key" in manifest["prerequisites"]["secrets"]
    assert manifest["prerequisites"]["secrets"]["api-key"]["description"] == "Internal API token"


def test_init_skips_topic_if_not_github(tmp_path, monkeypatch):
    """Verify topic status is 'skipped' if gh command fails."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.chdir(repo)
    
    # Mock gh failing without breaking mock_git
    orig_run = subprocess.run
    def _mock_run(args, **kwargs):
        if "gh" in args:
            class MockProc:
                returncode = 1
                stdout = ""
            return MockProc()
        return orig_run(args, **kwargs)
    
    monkeypatch.setattr(subprocess, "run", _mock_run)
    
    res = init_solution(repo)
    assert res["topic_status"] == "skipped"
