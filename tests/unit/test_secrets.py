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
        run.return_value = _run_mock(stdout="my-app-app-key\nmy-app-api-token\n")
        result = list_secrets_by_label("proj", "my-app")

    assert run.call_count == 1
    args = run.call_args.args[0]
    assert "list" in args
    assert "--filter" in args
    assert f"labels.{GAPP_SOLUTION_LABEL}=my-app" in args
    assert [s["id"] for s in result] == ["my-app-app-key", "my-app-api-token"]


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
        status = _ensure_secret("proj", "my-app-app-key", "my-app")

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
        status = _ensure_secret("proj", "my-app-app-key", "my-app")

    assert status == "exists"
    assert len(calls) == 1  # describe only — no create, no update
    assert "describe" in calls[0]


def test_ensure_secret_refuses_unlabeled_preexisting():
    """Secret with the target ID exists but has no gapp-solution label → raise."""
    def fake_run(args, **kw):
        return _run_mock(returncode=0, stdout="")  # describe ok, label empty

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as exc:
            _ensure_secret("proj", "my-app-app-key", "my-app")
    msg = str(exc.value)
    assert "my-app-app-key" in msg
    assert "no gapp-solution label" in msg
    assert "gcloud secrets describe my-app-app-key" in msg
    assert "gcloud secrets delete my-app-app-key" in msg


def test_ensure_secret_refuses_differently_owned_preexisting():
    """Secret labeled for a different solution → raise, name the owner."""
    def fake_run(args, **kw):
        return _run_mock(returncode=0, stdout="other-app\n")

    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError) as exc:
            _ensure_secret("proj", "my-app-app-key", "my-app")
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
            {"name": "APP_KEY", "secret": {"name": "app-key", "generate": True}},
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
            {"name": "APP_KEY", "secret": {"name": "app-key", "generate": True}},
        ]
    }
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]):
        with pytest.raises(RuntimeError) as exc:
            validate_declared_secrets("proj", "my-app", manifest)
    msg = str(exc.value)
    assert "my-app-api-token" in msg
    assert "my-app-db-url" in msg
    # generate-true secret is not required pre-deploy
    assert "my-app-app-key" not in msg


def test_list_secrets_by_label_filter_value_is_solution_name():
    """The label-filter query must use labels.gapp-solution=<solution> verbatim."""
    from gapp.admin.sdk.secrets import list_secrets_by_label, GAPP_SOLUTION_LABEL
    captured = []
    def fake_run(args, **kw):
        captured.append(args)
        return _run_mock(stdout="")
    with patch("gapp.admin.sdk.secrets.subprocess.run", side_effect=fake_run):
        list_secrets_by_label("proj", "my-svc")
    assert len(captured) == 1
    filter_idx = captured[0].index("--filter")
    assert captured[0][filter_idx + 1] == f"labels.{GAPP_SOLUTION_LABEL}=my-svc"


# -- _classify_unlabeled --


def test_classify_unlabeled_missing():
    """describe returncode != 0 → secret doesn't exist."""
    from gapp.admin.sdk.secrets import _classify_unlabeled
    with patch("gapp.admin.sdk.secrets.subprocess.run",
               return_value=_run_mock(returncode=1)):
        assert _classify_unlabeled("proj", "my-app-foo") == {"kind": "missing", "owner": None}


def test_classify_unlabeled_unattached():
    """describe ok, label empty → secret exists with no gapp-solution label."""
    from gapp.admin.sdk.secrets import _classify_unlabeled
    with patch("gapp.admin.sdk.secrets.subprocess.run",
               return_value=_run_mock(returncode=0, stdout="\n")):
        assert _classify_unlabeled("proj", "my-app-foo") == {"kind": "unattached", "owner": None}


def test_classify_unlabeled_conflict():
    """describe ok, label points at another solution → conflict, owner returned."""
    from gapp.admin.sdk.secrets import _classify_unlabeled
    with patch("gapp.admin.sdk.secrets.subprocess.run",
               return_value=_run_mock(returncode=0, stdout="other-app\n")):
        assert _classify_unlabeled("proj", "my-app-foo") == {"kind": "conflict", "owner": "other-app"}


# -- validate_declared_secrets with unattached/conflict distinction --


def test_validate_unattached_secret_includes_adopt_command():
    """Existing unlabeled secret → error explains and offers re-label command."""
    manifest = {"env": [{"name": "API_TOKEN", "secret": {"name": "api-token"}}]}
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]), \
         patch("gapp.admin.sdk.secrets._classify_unlabeled",
               return_value={"kind": "unattached", "owner": None}):
        with pytest.raises(RuntimeError) as exc:
            validate_declared_secrets("proj-x", "my-app", manifest)
    msg = str(exc.value)
    assert "exists in GCP but has no" in msg
    assert "gcloud secrets update my-app-api-token" in msg
    assert f"--update-labels={GAPP_SOLUTION_LABEL}=my-app" in msg
    assert "--project=proj-x" in msg


def test_validate_conflict_secret_names_other_owner():
    """Secret labeled for a different solution → error names that owner."""
    manifest = {"env": [{"name": "API_TOKEN", "secret": {"name": "api-token"}}]}
    with patch("gapp.admin.sdk.secrets.list_secrets_by_label", return_value=[]), \
         patch("gapp.admin.sdk.secrets._classify_unlabeled",
               return_value={"kind": "conflict", "owner": "other-app"}):
        with pytest.raises(RuntimeError) as exc:
            validate_declared_secrets("proj-x", "my-app", manifest)
    msg = str(exc.value)
    assert "labeled for solution 'other-app'" in msg
    assert "give the secret another name (gapp manifest env set <ENV_VAR> --secret <new-name>)" in msg


# -- list_secrets statuses + hints --


def _stub_resolve(name, project_id="proj-x"):
    """Build a fake GappSDK that resolves a fixed solution context."""
    sdk = MagicMock()
    sdk.resolve_solution_with_project.return_value = {
        "name": name, "project_id": project_id, "repo_path": "/tmp/fake",
    }
    return sdk


# -- CLI rendering --


# -- project resolution chain (issue #39) --
#
# The bug: every entry point in this module called `GappSDK.resolve_solution()`
# alone, which always returns project_id=None. Result: `gapp secrets list`
# reported every declared secret as `no-project` and `gapp secrets get/set`
# raised "No GCP project attached" — even when the same shell could run
# `gapp deploy` against a fully-resolved project. The fix routes every call
# site through `resolve_solution_with_project(...)` so the chained
# `resolve_project_for_solution` discovery actually runs.


def _solution_repo(tmp_path, monkeypatch, contents="name: my-app\n"):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(contents)
    monkeypatch.chdir(repo)
    return repo


def test_resolve_solution_with_project_populates_project_id_via_label_discovery(
    tmp_path, monkeypatch
):
    """Chained resolver finds the project by solution label."""
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

    _solution_repo(tmp_path, monkeypatch)
    sdk = GappSDK(provider=DummyCloudProvider())
    sdk.provider.host("proj-found", "my-app")

    ctx = sdk.resolve_solution_with_project()

    assert ctx is not None
    assert ctx["name"] == "my-app"
    assert ctx["project_id"] == "proj-found"


def test_resolve_solution_with_project_returns_none_project_when_undeployed(
    tmp_path, monkeypatch
):
    """Solution exists locally but no project hosts it → project_id=None,
    no exception. Lets list_secrets degrade gracefully to `no-project`."""
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

    _solution_repo(tmp_path, monkeypatch)
    sdk = GappSDK(provider=DummyCloudProvider())  # no projects at all

    ctx = sdk.resolve_solution_with_project()

    assert ctx is not None
    assert ctx["name"] == "my-app"
    assert ctx["project_id"] is None


def test_resolve_solution_with_project_returns_none_outside_solution(
    tmp_path, monkeypatch
):
    """Not in a solution repo → returns None entirely (caller raises)."""
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

    monkeypatch.chdir(tmp_path)  # no gapp.yaml here
    sdk = GappSDK(provider=DummyCloudProvider())

    assert sdk.resolve_solution_with_project() is None


def test_list_secrets_status_does_not_call_resolve_solution_alone(monkeypatch):
    """Defense in depth: confirm `secrets.list_secrets` does NOT use the
    bare `resolve_solution` shape. Anyone refactoring this code path back to
    the broken pattern hits this test rather than a silent prod regression."""
    import inspect
    from gapp.admin.sdk import secrets as sec_mod

    src = inspect.getsource(sec_mod)
    # Allow `resolve_solution_with_project` and the docstring; flag any bare
    # `.resolve_solution(` call that isn't the with_project variant.
    bare_calls = [
        line for line in src.splitlines()
        if ".resolve_solution(" in line
        and ".resolve_solution_with_project(" not in line
    ]
    assert not bare_calls, (
        "secrets.py reintroduced bare .resolve_solution() — that returns "
        f"project_id=None. See issue #39. Offending lines:\n  "
        + "\n  ".join(bare_calls)
    )
