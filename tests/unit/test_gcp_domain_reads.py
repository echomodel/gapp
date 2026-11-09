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
