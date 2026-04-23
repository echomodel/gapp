"""Tests for gapp.sdk.solutions — GCP-based discovery."""

import json
import subprocess
import pytest
from gapp.admin.sdk.solutions import list_solutions


@pytest.fixture
def mock_gcloud_discovery(monkeypatch):
    """Mock gcloud projects list for discovery tests."""
    def _mock_run(args, **kwargs):
        class MockProc:
            returncode = 0
            # Mock structure returned by list_deployments -> _find_gapp_projects
            stdout = json.dumps([
                {
                    "projectId": "proj-123",
                    "labels": {
                        "gapp-api": "default",
                        "gapp-worker": "default",
                        "gapp-owner-a-status": "prod"
                    }
                }
            ])
        return MockProc()
    
    monkeypatch.setattr(subprocess, "run", _mock_run)


def test_list_solutions_from_labels(mock_gcloud_discovery):
    """Verify list_solutions extracts all gapp solutions from GCP labels."""
    results = list_solutions()
    
    # Sort for consistent assertion
    results.sort(key=lambda s: s["name"])
    
    assert len(results) == 3
    assert results[0]["name"] == "api"
    assert results[1]["name"] == "status"
    assert results[2]["name"] == "worker"
    
    # Verify metadata
    status_app = next(s for s in results if s["name"] == "status")
    assert status_app["project_id"] == "proj-123"
    assert status_app["label"] == "gapp-owner-a-status"
