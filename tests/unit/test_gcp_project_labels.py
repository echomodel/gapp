"""GCPProvider reads of project labels, the project list, and the access
token surface the real failure instead of returning empty.

An expired login or a wrong account used to come back as "no labels",
which gapp then reported as "solution is not deployed". Every non-success
now raises CloudAccessError (ProjectNotFoundError for a 404) carrying the
API's own message, so the user sees "re-authenticate", not "not deployed".
"""

import json
import subprocess

import pytest

from gapp.admin.sdk.cloud.base import CloudAccessError, ProjectNotFoundError
from gapp.admin.sdk.cloud.gcp import GCPProvider


def _proc(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _api_error(status, message):
    return json.dumps({"error": {"code": 0, "message": message, "status": status}})


@pytest.fixture
def provider(monkeypatch):
    p = GCPProvider(account="deployer@example.com")
    monkeypatch.setattr(p, "get_auth_token", lambda: "tok")
    return p


def _curl_returning(monkeypatch, code, body):
    def run(cmd, **kwargs):
        assert cmd[0] == "curl" and "-f" not in cmd and "-sf" not in cmd
        return _proc(0, f"{body}\n{code}")
    monkeypatch.setattr(subprocess, "run", run)


def test_labels_are_returned_on_success(provider, monkeypatch):
    _curl_returning(monkeypatch, 200, json.dumps({"labels": {"gapp__my-app": "v-3"}}))
    assert provider.get_project_labels("my-proj") == {"gapp__my-app": "v-3"}


def test_project_without_labels_returns_empty_dict(provider, monkeypatch):
    _curl_returning(monkeypatch, 200, json.dumps({"name": "projects/1"}))
    assert provider.get_project_labels("my-proj") == {}


def test_unauthenticated_read_raises_with_the_api_message(provider, monkeypatch):
    _curl_returning(monkeypatch, 401, _api_error(
        "UNAUTHENTICATED", "Request had invalid authentication credentials."))
    with pytest.raises(CloudAccessError) as exc:
        provider.get_project_labels("my-proj")
    text = str(exc.value)
    assert "HTTP 401" in text
    assert "invalid authentication credentials" in text
    assert "deployer@example.com" in text
    assert "gcloud auth login" in text


def test_forbidden_read_raises(provider, monkeypatch):
    _curl_returning(monkeypatch, 403, _api_error("PERMISSION_DENIED", "The caller does not have permission"))
    with pytest.raises(CloudAccessError, match="does not have permission"):
        provider.get_project_labels("my-proj")


def test_missing_project_is_a_distinct_error(provider, monkeypatch):
    _curl_returning(monkeypatch, 404, _api_error("NOT_FOUND", "Requested entity was not found."))
    with pytest.raises(ProjectNotFoundError, match="my-proj"):
        provider.get_project_labels("my-proj")


def test_curl_failure_raises(provider, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _proc(6, "", "Could not resolve host"))
    with pytest.raises(CloudAccessError, match="Could not resolve host"):
        provider.get_project_labels("my-proj")


def test_label_write_failure_raises(provider, monkeypatch):
    _curl_returning(monkeypatch, 403, _api_error("PERMISSION_DENIED", "resourcemanager.projects.update"))
    with pytest.raises(CloudAccessError, match="write labels"):
        provider.set_project_labels("my-proj", {"gapp__my-app": "v-3"})


def test_project_listing_failure_raises_with_gcloud_stderr(provider, monkeypatch):
    monkeypatch.setattr(provider, "_run_gcloud", lambda args, **kw: _proc(
        1, "", "ERROR: (gcloud.projects.list) There was a problem refreshing your current auth tokens: "
               "Reauthentication failed. Please run `gcloud auth login`."))
    with pytest.raises(CloudAccessError, match="Reauthentication failed"):
        provider.list_projects(filter_query="labels:gapp__my-app")


def test_project_listing_success_returns_projects(provider, monkeypatch):
    monkeypatch.setattr(provider, "_run_gcloud", lambda args, **kw: _proc(
        0, json.dumps([{"projectId": "my-proj", "labels": {"gapp__my-app": "v-3"}}])))
    assert provider.list_projects()[0]["projectId"] == "my-proj"


def test_token_failure_raises_with_gcloud_stderr(monkeypatch):
    p = GCPProvider(account="deployer@example.com")
    monkeypatch.setattr(p, "_run_gcloud", lambda args, **kw: _proc(
        1, "", "ERROR: (gcloud.auth.print-access-token) You do not currently have an active account selected."))
    with pytest.raises(CloudAccessError, match="active account"):
        p.get_auth_token()


# -- the two domain reads --


def test_verified_domains_are_normalised(provider, monkeypatch):
    monkeypatch.setattr(provider, "_run_gcloud", lambda args, **kw: _proc(
        0, json.dumps([{"id": "Example.com."}, {"id": "apps.example.net"}])))
    assert provider.list_verified_domains() == ["example.com", "apps.example.net"]


def test_verified_domains_failure_raises(provider, monkeypatch):
    monkeypatch.setattr(provider, "_run_gcloud", lambda args, **kw: _proc(1, "", "ERROR: reauth required"))
    with pytest.raises(CloudAccessError, match="reauth required"):
        provider.list_verified_domains()


def test_missing_domain_mapping_is_none(provider, monkeypatch):
    monkeypatch.setattr(provider, "_run_gcloud", lambda args, **kw: _proc(
        1, "", "ERROR: (gcloud.beta.run.domain-mappings.describe) Domain mapping [mcp.example.com] not found."))
    assert provider.get_domain_mapping("my-proj", "us-central1", "mcp.example.com") is None


def test_domain_mapping_is_returned(provider, monkeypatch):
    captured = {}

    def run(args, **kw):
        captured["args"] = args
        return _proc(0, json.dumps({"status": {"conditions": [{"type": "Ready", "status": "True"}]}}))

    monkeypatch.setattr(provider, "_run_gcloud", run)
    mapping = provider.get_domain_mapping("my-proj", "us-central1", "mcp.example.com")
    assert mapping["status"]["conditions"][0]["type"] == "Ready"
    assert captured["args"][:4] == ["beta", "run", "domain-mappings", "describe"]
    assert "mcp.example.com" in captured["args"] and "my-proj" in captured["args"]
