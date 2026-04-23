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
    
    res = init_solution(repo)
    assert res["name"] == "my-solution"
    assert res["manifest_status"] == "created"
    assert (repo / "gapp.yaml").exists()
    
    manifest = load_manifest(repo)
    assert manifest["name"] == "my-solution"


def test_init_merges_entrypoint(tmp_path):
    """Verify init_solution can set/update the entrypoint."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    
    init_solution(repo, entrypoint="main:app")
    manifest = load_manifest(repo)
    assert manifest["service"]["entrypoint"] == "main:app"
    
    # Update it
    init_solution(repo, entrypoint="api:app")
    manifest = load_manifest(repo)
    assert manifest["service"]["entrypoint"] == "api:app"


def test_init_adds_secrets(tmp_path):
    """Verify init_solution adds prerequisite secrets."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    
    init_solution(repo, secrets={"api-key": "Internal API token"})
    manifest = load_manifest(repo)
    assert "api-key" in manifest["prerequisites"]["secrets"]
    assert manifest["prerequisites"]["secrets"]["api-key"]["description"] == "Internal API token"


def test_init_skips_topic_if_not_github(tmp_path, monkeypatch):
    """Verify topic status is 'skipped' if gh command fails."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    
    # Mock gh failing
    def mock_run(*args, **kwargs):
        class MockProc:
            returncode = 1
            stdout = ""
        return MockProc()
    
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    res = init_solution(repo)
    assert res["topic_status"] == "skipped"
