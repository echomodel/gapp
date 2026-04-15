"""Tests for gapp.admin.sdk.secrets — label-based ownership (#27)."""

from unittest.mock import patch, MagicMock

import pytest

from gapp.admin.sdk.secrets import (
    GAPP_SOLUTION_LABEL,
    _ensure_secret,
    list_secrets_by_label,
    validate_declared_secrets,
)


def _run_mock(returncode=0, stdout="", stderr=""):
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


def test_label_constant():
    assert GAPP_SOLUTION_LABEL == "gapp-solution"


def test_list_secrets_by_label_single_call():
    """One gcloud call, filtered by label, parses secret IDs from stdout."""
    with patch("gapp.admin.sdk.secrets.subprocess.run") as run:
        run.return_value = _run_mock(stdout="my-app-signing-key\nmy-app-api-token\n")
        result = list_secrets_by_label("proj", "my-app")

    assert run.call_count == 1
    args = run.call_args.args[0]
    assert "list" in args
    assert "--filter" in args
    assert f"labels.{GAPP_SOLUTION_LABEL}=my-app" in args
    assert [s["id"] for s in result] == ["my-app-signing-key", "my-app-api-token"]


def test_list_secrets_by_label_api_failure_degrades():
    """API failure returns [] and does not raise — the caller decides what's load-bearing."""
    with patch("gapp.admin.sdk.secrets.subprocess.run") as run:
        run.return_value = _run_mock(returncode=1, stderr="boom")
        assert list_secrets_by_label("proj", "my-app") == []


def test_ensure_secret_stamps_label_on_create():
    """When the secret doesn't exist, create with --labels."""
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if "describe" in args:
            return _run_mock(returncode=1)
        return _run_mock(returncode=0)

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        status = _ensure_secret("proj", "my-app-signing-key", "my-app")

    assert status == "created"
    create_call = next(c for c in calls if "create" in c)
    assert "--labels" in create_call
    assert "gapp-solution=my-app" in create_call


def test_ensure_secret_reuses_when_already_owned():
    """Existing secret already labeled for this solution → reuse, no mutation."""
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _run_mock(returncode=0, stdout="my-app\n")

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        status = _ensure_secret("proj", "my-app-signing-key", "my-app")

    assert status == "exists"
    assert len(calls) == 1  # describe only — no create, no update
    assert "describe" in calls[0]


def test_ensure_secret_refuses_unlabeled_preexisting():
    """Secret with the target ID exists but has no gapp-solution label → raise."""
    def fake_run(args, **kw):
        return _run_mock(returncode=0, stdout="")  # describe ok, label empty

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as exc:
            _ensure_secret("proj", "my-app-signing-key", "my-app")
    msg = str(exc.value)
    assert "my-app-signing-key" in msg
    assert "no gapp-solution label" in msg
    assert "gcloud secrets describe my-app-signing-key" in msg
    assert "gcloud secrets delete my-app-signing-key" in msg


def test_ensure_secret_refuses_differently_owned_preexisting():
    """Secret labeled for a different solution → raise, name the owner."""
    def fake_run(args, **kw):
        return _run_mock(returncode=0, stdout="other-app\n")

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as exc:
            _ensure_secret("proj", "my-app-signing-key", "my-app")
    assert "owned by solution 'other-app'" in str(exc.value)


def test_validate_declared_secrets_passes_when_present():
    manifest = {
        "env": [
            {"name": "API_TOKEN", "secret": {"name": "api-token"}},
        ]
    }
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label",
               return_value=[{"id": "my-app-api-token", "labels": {}}]):
        validate_declared_secrets("proj", "my-app", manifest)  # no raise


def test_validate_declared_secrets_fast_fails_on_missing_non_generate():
    manifest = {
        "env": [
            {"name": "API_TOKEN", "secret": {"name": "api-token"}},
        ]
    }
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            validate_declared_secrets("proj", "my-app", manifest)
    msg = str(exc.value)
    assert "my-app-api-token" in msg
    assert "gapp secrets set api-token" in msg


def test_validate_declared_secrets_skips_generate():
    """Secrets with generate: true are not checked — gapp creates them on deploy."""
    manifest = {
        "env": [
            {"name": "SIGNING_KEY", "secret": {"name": "signing-key", "generate": True}},
        ]
    }
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]):
        validate_declared_secrets("proj", "my-app", manifest)  # no raise


def test_validate_declared_secrets_reports_all_missing():
    """When multiple non-generate secrets are missing, the error names each one."""
    manifest = {
        "env": [
            {"name": "API_TOKEN", "secret": {"name": "api-token"}},
            {"name": "DB_URL", "secret": {"name": "db-url"}},
            {"name": "SIGNING_KEY", "secret": {"name": "signing-key", "generate": True}},
        ]
    }
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            validate_declared_secrets("proj", "my-app", manifest)
    msg = str(exc.value)
    assert "my-app-api-token" in msg
    assert "my-app-db-url" in msg
    # generate-true secret is not required pre-deploy
    assert "my-app-signing-key" not in msg


def test_list_secrets_by_label_filter_value_is_solution_name():
    """The label-filter query must use labels.gapp-solution=<solution> verbatim."""
    from gapp.admin.sdk.secrets import list_secrets_by_label, GAPP_SOLUTION_LABEL
    captured = []
    def fake_run(args, **kw):
        captured.append(args)
        return _run_mock(stdout="")
    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        list_secrets_by_label("proj", "food-agent")
    assert len(captured) == 1
    filter_idx = captured[0].index("--filter")
    assert captured[0][filter_idx + 1] == f"labels.{GAPP_SOLUTION_LABEL}=food-agent"
