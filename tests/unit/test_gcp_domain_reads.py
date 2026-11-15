"""GCPProvider's domain and state reads: a read that fails is never taken
for "nothing there" where that could let a deploy take a live domain down.
"""

import subprocess
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.base import CloudAccessError
from gapp.admin.sdk.cloud.gcp import GCPProvider


def _done(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture
def provider(monkeypatch):
    p = GCPProvider()
    monkeypatch.setattr(p, "_identity_hint", lambda *a: "(sign-in hint)")
    return p


def test_domain_mapping_other_failures_raise_and_never_read_as_no_mapping(provider):
    err = "ERROR: (gcloud.beta.run.domain-mappings.describe) PERMISSION_DENIED: denied"
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr=err)):
        with pytest.raises(CloudAccessError, match="PERMISSION_DENIED"):
            provider.get_domain_mapping("proj", "us-central1", "mcp.example.com")


def test_state_read_failure_raises_when_strict_and_is_none_otherwise(provider, monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "get_auth_token", lambda: "t")
    monkeypatch.setattr("gapp.admin.sdk.cloud.gcp._stage_terraform", lambda d: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(1, stderr="Error: Failed to get existing workspaces: 403"))
    assert provider.get_infrastructure_outputs(tmp_path, "b", "terraform/state") is None
    with pytest.raises(CloudAccessError, match="terraform init against gs://b/terraform/state failed: .*403"):
        provider.get_infrastructure_outputs(tmp_path, "b", "terraform/state", strict=True)


def test_an_empty_state_reads_as_no_outputs_even_when_strict(provider, monkeypatch, tmp_path):
    monkeypatch.setattr(provider, "get_auth_token", lambda: "t")
    monkeypatch.setattr("gapp.admin.sdk.cloud.gcp._stage_terraform", lambda d: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(0, stdout="{}"))
    assert provider.get_infrastructure_outputs(tmp_path, "b", "terraform/state", strict=True) == {}


def test_terraform_always_starts_from_the_state_location_gapp_names(provider, monkeypatch, tmp_path):
    """A staging folder that remembers another state location (an earlier
    gapp's, or the same id in another project) must not stop a deploy or a
    status read, and state is never moved: init always gets -reconfigure."""
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return _done(0, "{}")

    monkeypatch.setattr(provider, "get_auth_token", lambda: "token")
    monkeypatch.setattr(subprocess, "run", run)
    provider.get_infrastructure_outputs(tmp_path / "staging", "gapp-p", "deployments/a/terraform/state")
    provider.apply_infrastructure(tmp_path / "staging", "gapp-p", "deployments/a/terraform/state", True, {})
    inits = [c for c in calls if c[:2] == ["terraform", "init"]]
    assert len(inits) == 2 and all("-reconfigure" in c and "-migrate-state" not in c for c in inits)


def test_the_active_account_is_read_from_gcloud_and_unset_reads_as_none(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(0, "deployer@example.test\n")):
        assert provider.active_account() == "deployer@example.test"
    with patch.object(provider, "_run_gcloud", return_value=_done(0, "(unset)\n")):
        assert provider.active_account() is None


# -- finding gapp's projects and their deploy records --


def test_the_project_search_asks_for_the_label_and_follows_pages(provider, monkeypatch):
    import json
    monkeypatch.setattr(provider, "get_auth_token", lambda: "t")
    pages = iter([
        json.dumps({"projects": [{"projectId": "b-proj", "state": "ACTIVE"},
                                 {"projectId": "gone", "state": "DELETE_REQUESTED"}], "nextPageToken": "p2"}) + "\n200",
        json.dumps({"projects": [{"projectId": "a-proj", "state": "ACTIVE"}]}) + "\n200",
    ])
    urls = []

    def run(cmd, **kwargs):
        urls.append(cmd[-1])
        return _done(0, stdout=next(pages))
    monkeypatch.setattr(subprocess, "run", run)
    assert provider.search_projects_by_label("gapp", "hosted") == ["a-proj", "b-proj"]
    assert urls[0].endswith("/v3/projects:search?query=labels.gapp%3Ahosted")
    assert urls[1].endswith("&pageToken=p2")


def test_a_failed_project_search_raises_and_is_never_no_projects(provider, monkeypatch):
    monkeypatch.setattr(provider, "get_auth_token", lambda: "t")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _done(0, stdout='{"error": {"message": "denied"}}\n403'))
    with pytest.raises(CloudAccessError, match="HTTP 403"):
        provider.search_projects_by_label("gapp", "hosted")


def test_object_listing_returns_paths_and_nothing_for_no_match(provider):
    listed = "gs://gapp-p/deployments/a/deployed.yaml\ngs://gapp-p/deployments/b/deployed.yaml\n"
    with patch.object(provider, "_run_gcloud", return_value=_done(0, stdout=listed)):
        assert provider.list_object_paths("gapp-p", "deployments/*/deployed.yaml") == [
            "deployments/a/deployed.yaml", "deployments/b/deployed.yaml"]
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: One or more URLs matched no objects.")):
        assert provider.list_object_paths("gapp-p", "deployments/*/deployed.yaml") == []
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError):
            provider.list_object_paths("gapp-p", "deployments/*/deployed.yaml")
