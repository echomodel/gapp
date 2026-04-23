"""Tests for gapp.sdk.setup — GCP foundation provisioning."""

import subprocess
import pytest
from pathlib import Path
from gapp.admin.sdk.setup import setup_solution
from gapp.admin.sdk.context import set_owner


@pytest.fixture
def mock_gcloud(monkeypatch):
    """Mock subprocess.run for gcloud calls."""
    calls = []
    
    def _mock_run(args, **kwargs):
        calls.append(args)
        class MockProc:
            returncode = 0
            stdout = "31628365056" if "describe" in args else "" # project number mock
        return MockProc()
    
    monkeypatch.setattr(subprocess, "run", _mock_run)
    return calls


def test_setup_enables_apis_and_creates_bucket(tmp_path, monkeypatch, mock_gcloud):
    """Verify setup_solution enables foundation APIs and creates the deterministic bucket."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = setup_solution(project_id="test-proj-123")
    
    assert res["name"] == "my-app"
    assert res["project_id"] == "test-proj-123"
    assert res["bucket"] == "gapp-my-app-test-proj-123"
    
    # Verify core APIs were enabled
    enabled_apis = [c[2] for c in mock_gcloud if c[1] == "services" and c[2] == "enable"]
    assert "run.googleapis.com" in enabled_apis
    assert "cloudbuild.googleapis.com" in enabled_apis
    
    # Verify bucket creation call
    assert any("storage" in c and "create" in c for c in mock_gcloud)


def test_setup_with_owner_namespace(tmp_path, monkeypatch, mock_gcloud):
    """Verify setup_solution uses the owner namespace in the bucket name."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    set_owner("owner-a")
    res = setup_solution(project_id="test-proj-123")
    
    assert res["bucket"] == "gapp-owner-a-my-app-test-proj-123"
    assert res["label_status"] == "added"


def test_setup_with_env_scoping(tmp_path, monkeypatch, mock_gcloud):
    """Verify setup_solution supports environment names in bucket and labels."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = setup_solution(project_id="test-proj-123", env="prod")
    
    # env != 'default', so it should appear in the bucket name
    assert res["bucket"] == "gapp-my-app-test-proj-123-prod"
    assert res["env"] == "prod"
