"""Tests for gapp.sdk.status — infrastructure health check."""

import json
import subprocess
import pytest
from pathlib import Path
from gapp.admin.sdk.status import get_status


@pytest.fixture
def mock_gcloud_status(monkeypatch):
    """Mock gcloud for status discovery."""
    def _mock_run(args, **kwargs):
        class MockProc:
            returncode = 0
            if "describe" in args:
                stdout = json.dumps({"projectId": "proj-123", "labels": {"gapp-status": "default"}})
            elif "list" in args:
                stdout = json.dumps([{"projectId": "proj-123", "labels": {"gapp-status": "default"}}])
            else:
                stdout = "test-token"
            
        m = MockProc()
        m.stdout = stdout
        return m
    
    # We need to mock gapp.admin.sdk.context.run_gcloud since status.py uses it indirectly
    from gapp.admin.sdk import context
    monkeypatch.setattr(context, "run_gcloud", _mock_run)


def test_status_uninitialized(tmp_path, monkeypatch):
    """Verify get_status returns init step if no gapp.yaml found."""
    monkeypatch.chdir(tmp_path)
    res = get_status()
    assert res.initialized is False
    assert res.next_step.action == "init"


def test_status_pending_setup(tmp_path, monkeypatch):
    """Verify get_status returns setup step if no project attached."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    # Mock discovery finding nothing
    from gapp.admin.sdk import context
    def mock_run_empty(args, **kwargs):
        class MockProc:
            returncode = 0
            stdout = "[]"
        return MockProc()
    monkeypatch.setattr(context, "run_gcloud", mock_run_empty)
    
    res = get_status()
    assert res.initialized is True
    assert res.next_step.action == "setup"
    assert "No GCP project attached" in res.next_step.hint
