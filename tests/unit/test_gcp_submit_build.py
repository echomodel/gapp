"""Tests for GCPProvider.submit_build_sync's async+poll behavior.

`submit_build_sync` historically called `gcloud builds submit`
without `--async`, which makes gcloud stream build logs back to
the caller. The streaming requires the deploy SA to have read
access to the cloudbuild logs bucket — a permission gapp's
`REQUIRED_ROLES` intentionally doesn't grant. The result was
that every fresh-SHA CI deploy on a gapp-managed project saw
`gcloud builds submit` exit non-zero on the streaming attempt
even though the underlying build succeeded in the background.

The current implementation submits async (returns immediately
with a build ID), then polls `cloudbuild.builds.get` until
terminal status. No streaming = no streaming-permission
dependency = clean exit on success, clean error on failure
without IAM-related noise.

See echomodel/gapp#43 for the original bug.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.gcp import GCPProvider


@pytest.fixture
def provider():
    return GCPProvider()


def test_submit_build_sync_returns_normally_on_success(provider, monkeypatch):
    """Async submit + poll → SUCCESS → return without exception."""
    monkeypatch.setattr(time_module(), "sleep", lambda *_: None)
    with patch.object(provider, "submit_build_async", return_value="build-123") as submit, \
         patch.object(
             provider, "check_build",
             side_effect=[
                 {"status": "QUEUED"},
                 {"status": "WORKING"},
                 {"status": "SUCCESS"},
             ],
         ) as check:
        provider.submit_build_sync(
            "test-project", Path("/tmp/build-dir"),
            "us-central1-docker.pkg.dev/test/repo/img:abc", "module:app",
        )
    submit.assert_called_once()
    assert check.call_count == 3


def test_submit_build_sync_raises_on_failure(provider, monkeypatch):
    """FAILURE status → raise with build ID and log URL surfaced.

    The operator can't tail logs (no streaming), so the URL must
    appear in the error message so they can click through to the
    Cloud Build console for diagnosis.
    """
    monkeypatch.setattr(time_module(), "sleep", lambda *_: None)
    log_url = "https://console.cloud.google.com/cloud-build/builds/build-456"
    with patch.object(provider, "submit_build_async", return_value="build-456"), \
         patch.object(
             provider, "check_build",
             side_effect=[
                 {"status": "WORKING", "logUrl": log_url},
                 {"status": "FAILURE", "logUrl": log_url},
             ],
         ):
        with pytest.raises(RuntimeError) as exc_info:
            provider.submit_build_sync(
                "test-project", Path("/tmp/build-dir"),
                "us-central1-docker.pkg.dev/test/repo/img:abc", "module:app",
            )
    msg = str(exc_info.value)
    assert "build-456" in msg
    assert "FAILURE" in msg
    assert log_url in msg


def test_submit_build_sync_handles_each_terminal_status(provider, monkeypatch):
    """Every Cloud Build terminal status other than SUCCESS raises.

    Important because Cloud Build can finalize in several non-success
    states (TIMEOUT, CANCELLED, EXPIRED, INTERNAL_ERROR, FAILURE) —
    all of them must surface as errors, not silently return.
    """
    monkeypatch.setattr(time_module(), "sleep", lambda *_: None)
    for non_success in ("FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"):
        with patch.object(provider, "submit_build_async", return_value="b"), \
             patch.object(provider, "check_build",
                          return_value={"status": non_success}):
            with pytest.raises(RuntimeError, match=non_success):
                provider.submit_build_sync(
                    "p", Path("/tmp/d"), "img:tag", "m:a",
                )


def test_submit_build_sync_does_not_call_gcloud_with_streaming_flags(provider, monkeypatch):
    """Regression guard: the build submit must use --async.

    Whatever else changes about gcloud's command shape, the absence
    of streaming is the property the SA's permission set depends on.
    Streaming back means failure-on-no-permission; --async means
    success-on-no-permission.
    """
    monkeypatch.setattr(time_module(), "sleep", lambda *_: None)
    captured: list[list[str]] = []

    def fake_run(args, **kwargs):
        captured.append(list(args))
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout='{"id":"b1"}', stderr="",
        )

    monkeypatch.setattr(provider, "_run_gcloud", fake_run)
    monkeypatch.setattr(provider, "check_build", lambda *a, **kw: {"status": "SUCCESS"})
    provider.submit_build_sync(
        "p", Path("/tmp/d"), "img:tag", "m:a",
    )
    # First captured call is the build submit
    submit_args = captured[0]
    assert "builds" in submit_args
    assert "submit" in submit_args
    assert "--async" in submit_args, (
        "submit_build_sync must use --async to avoid the cloudbuild "
        "log streaming permission gap. See echomodel/gapp#43."
    )


def time_module():
    """Module-level access to `time` for monkeypatching `time.sleep`.

    We patch the module's `sleep` rather than importing `sleep`
    directly because the production code calls `time.sleep(5)` via
    attribute access; monkeypatching the imported name in the test
    file wouldn't intercept the production call site.
    """
    from gapp.admin.sdk.cloud import gcp
    return gcp.time
