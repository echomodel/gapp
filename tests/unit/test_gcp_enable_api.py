"""Tests for GCPProvider.enable_api PERMISSION_DENIED tolerance.

CI deploy SAs intentionally do not have
`serviceusage.serviceUsageAdmin`. When the CI workflow re-runs
`gapp setup` (idempotent foundation step), the API-enable call
gets PERMISSION_DENIED — which is fine, because the operator's
local `gapp setup` already enabled the foundation APIs as project
owner. The provider must swallow that specific error and continue.

This tolerance existed in pre-v3 `setup._enable_api` and was
dropped during the v3 GappSDK/cloud-provider consolidation,
causing every CI deploy to fail at the first API-enable call.
Restored to close the regression.
"""

import subprocess
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.gcp import GCPProvider


@pytest.fixture
def provider():
    return GCPProvider()


def _gcloud_ok():
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout="Operation finished successfully.\n", stderr=""
    )


def _gcloud_permission_denied():
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(
            "ERROR: (gcloud.services.enable) PERMISSION_DENIED: "
            "Permission denied to enable service [run.googleapis.com]"
        ),
    )


def _gcloud_other_failure():
    return subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="ERROR: (gcloud.services.enable) NOT_FOUND: Project does not exist.",
    )


def test_enable_api_success_returns_silently(provider):
    """Happy path: the call succeeds, no exception."""
    with patch.object(provider, "_run_gcloud", return_value=_gcloud_ok()):
        provider.enable_api("any-project", "run.googleapis.com")
    # No exception = pass.


def test_enable_api_permission_denied_is_tolerated(provider):
    """CI deploy SAs lack serviceusage.serviceUsageAdmin by design.
    PERMISSION_DENIED here means the operator already enabled the
    API locally; continue silently rather than crashing the deploy."""
    with patch.object(
        provider, "_run_gcloud", return_value=_gcloud_permission_denied()
    ):
        # No raise — the call returns normally.
        provider.enable_api("any-project", "run.googleapis.com")


def test_enable_api_other_failure_raises(provider):
    """Non-permission failures (project doesn't exist, network error,
    etc.) must still raise — silent swallow would mask real bugs."""
    with patch.object(provider, "_run_gcloud", return_value=_gcloud_other_failure()):
        with pytest.raises(RuntimeError) as exc_info:
            provider.enable_api("any-project", "run.googleapis.com")
    # Error message should include the gcloud stderr so the operator
    # can act on it.
    assert "NOT_FOUND" in str(exc_info.value)
    assert "Project does not exist" in str(exc_info.value)


def _done(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_enabled_apis_reads_the_projects_list_and_says_none_when_it_cannot(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done("run.googleapis.com\nstorage.googleapis.com\n")):
        assert provider.enabled_apis("p") == {"run.googleapis.com", "storage.googleapis.com"}
    with patch.object(provider, "_run_gcloud", return_value=_done(returncode=1)):
        assert provider.enabled_apis("p") is None


def test_build_permissions_grant_only_the_roles_the_build_lacks(provider):
    member = "serviceAccount:123-compute" + "@" + "developer.gserviceaccount.com"
    policy = '{"bindings": [{"role": "roles/storage.objectViewer", "members": ["%s"]}]}' % member
    calls = []

    def gcloud(args, **kwargs):
        calls.append(args)
        if args[:2] == ["projects", "describe"]:
            return _done("123\n")
        if args[:2] == ["projects", "get-iam-policy"]:
            return _done(policy)
        return _done()
    with patch.object(provider, "_run_gcloud", side_effect=gcloud):
        assert provider.ensure_build_permissions("p") == ["roles/artifactregistry.writer"]
    grants = [a for a in calls if a[:2] == ["projects", "add-iam-policy-binding"]]
    assert [a[a.index("--role") + 1] for a in grants] == ["roles/artifactregistry.writer"]


def test_build_permissions_grant_every_role_when_the_policy_cannot_be_read(provider):
    calls = []

    def gcloud(args, **kwargs):
        calls.append(args)
        if args[:2] == ["projects", "describe"]:
            return _done("123\n")
        if args[:2] == ["projects", "get-iam-policy"]:
            return _done(returncode=1)
        return _done()
    with patch.object(provider, "_run_gcloud", side_effect=gcloud):
        assert provider.ensure_build_permissions("p") is None
    assert len([a for a in calls if a[:2] == ["projects", "add-iam-policy-binding"]]) == 2
