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
    assert "rename in gapp.yaml" in msg


# -- list_secrets statuses + hints --


def _stub_resolve(name, project_id="proj-x"):
    """Build a fake GappSDK that resolves a fixed solution context."""
    sdk = MagicMock()
    sdk.resolve_solution.return_value = {
        "name": name, "project_id": project_id, "repo_path": "/tmp/fake",
    }
    return sdk


def test_list_secrets_full_scenario(tmp_path, monkeypatch):
    """3 yaml secrets x 3 GCP-labeled-or-not layout — each gets its right status + hint."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: APP_KEY\n"
        "    secret: {name: app-key}\n"
        "  - name: API_TOKEN\n"
        "    secret: {name: api-token}\n"
        "  - name: WEBHOOK_SECRET\n"
        "    secret: {name: webhook-secret}\n"
    )

    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "proj-x", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(
        sec_mod, "list_secrets_by_label",
        lambda pid, sol: [{"id": "my-app-app-key", "labels": {}},
                          {"id": "my-app-old-key", "labels": {}}],
    )

    classify_table = {
        "my-app-api-token": {"kind": "unattached", "owner": None},
        "my-app-webhook-secret": {"kind": "missing", "owner": None},
    }
    monkeypatch.setattr(sec_mod, "_classify_unlabeled",
                        lambda pid, sid: classify_table[sid])

    result = sec_mod.list_secrets()

    by_name = {s["name"]: s for s in result["secrets"]}
    assert by_name["app-key"]["status"] == "ready"
    assert by_name["api-token"]["status"] == "unattached"
    assert by_name["webhook-secret"]["status"] == "missing"

    assert result["orphans"] == ["my-app-old-key"]

    issues = {h["secret_id"]: h["issue"] for h in result["hints"]}
    assert issues == {
        "my-app-api-token": "unattached",
        "my-app-old-key": "orphan",
    }
    unattached = next(h for h in result["hints"] if h["issue"] == "unattached")
    cmds = [opt["command"] for opt in unattached["options"]]
    assert any("gcloud secrets update my-app-api-token" in c for c in cmds)
    assert any("gcloud secrets delete my-app-api-token" in c for c in cmds)


def test_list_secrets_conflict_hint_names_owner(tmp_path, monkeypatch):
    """A conflict status emits a hint that names the offending owner."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: API_TOKEN\n"
        "    secret: {name: api-token}\n"
    )

    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "proj-x", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(sec_mod, "list_secrets_by_label", lambda pid, sol: [])
    monkeypatch.setattr(
        sec_mod, "_classify_unlabeled",
        lambda pid, sid: {"kind": "conflict", "owner": "other-app"},
    )

    result = sec_mod.list_secrets()

    secret = result["secrets"][0]
    assert secret["status"] == "conflict"
    assert len(result["hints"]) == 1
    hint = result["hints"][0]
    assert hint["issue"] == "conflict"
    assert "other-app" in hint["message"]


def test_list_secrets_generate_missing_uses_distinct_status(tmp_path, monkeypatch):
    """generate:true + not present → status `missing-generate`, no hint."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: APP_KEY\n"
        "    secret: {name: app-key, generate: true}\n"
    )

    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "proj-x", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(sec_mod, "list_secrets_by_label", lambda pid, sol: [])
    monkeypatch.setattr(
        sec_mod, "_classify_unlabeled",
        lambda pid, sid: {"kind": "missing", "owner": None},
    )

    result = sec_mod.list_secrets()

    assert result["secrets"][0]["status"] == "missing-generate"
    assert result["hints"] == []  # no remediation needed for auto-generated


def test_list_secrets_no_project_status(tmp_path, monkeypatch):
    """When no GCP project is attached, every declared secret reports `no-project`."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: API_TOKEN\n"
        "    secret: {name: api-token}\n"
    )

    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": None, "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)

    result = sec_mod.list_secrets()

    assert result["project_id"] is None
    assert result["secrets"][0]["status"] == "no-project"
    assert result["hints"] == []


def test_list_secrets_all_ready_hints_empty(tmp_path, monkeypatch):
    """When every declared secret is ready and no orphans exist, hints is empty."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: API_TOKEN\n"
        "    secret: {name: api-token}\n"
        "  - name: APP_KEY\n"
        "    secret: {name: app-key}\n"
    )

    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "proj-x", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(
        sec_mod, "list_secrets_by_label",
        lambda pid, sol: [{"id": "my-app-api-token"}, {"id": "my-app-app-key"}],
    )

    result = sec_mod.list_secrets()

    assert all(s["status"] == "ready" for s in result["secrets"])
    assert result["orphans"] == []
    assert result["hints"] == []


def test_list_secrets_conflict_hint_full_structure(tmp_path, monkeypatch):
    """Conflict hint carries secret_id, issue, message, and two concrete options."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: API_TOKEN\n"
        "    secret: {name: api-token}\n"
    )
    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "my-project", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(sec_mod, "list_secrets_by_label", lambda pid, sol: [])
    monkeypatch.setattr(
        sec_mod, "_classify_unlabeled",
        lambda pid, sid: {"kind": "conflict", "owner": "another-app"},
    )

    hint = sec_mod.list_secrets()["hints"][0]
    assert hint["secret_id"] == "my-app-api-token"
    assert hint["issue"] == "conflict"
    assert "another-app" in hint["message"]
    assert len(hint["options"]) == 2
    rename_opt, relabel_opt = hint["options"]
    assert "rename" in rename_opt["label"].lower()
    assert "edit gapp.yaml" in rename_opt["command"]
    assert "Re-label" in relabel_opt["label"]
    assert "gcloud secrets update my-app-api-token" in relabel_opt["command"]
    assert "--update-labels=gapp-solution=my-app" in relabel_opt["command"]
    assert "--project=my-project" in relabel_opt["command"]


def test_list_secrets_orphan_hint_full_structure(tmp_path, monkeypatch):
    """Orphan hint carries delete + re-declare options with correct project/id."""
    from gapp.admin.sdk import secrets as sec_mod
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: my-app\n")  # no env declarations
    sdk_stub = MagicMock()
    sdk_stub.resolve_solution.return_value = {
        "name": "my-app", "project_id": "my-project", "repo_path": str(repo),
    }
    monkeypatch.setattr(sec_mod, "GappSDK", lambda: sdk_stub)
    monkeypatch.setattr(
        sec_mod, "list_secrets_by_label",
        lambda pid, sol: [{"id": "my-app-stale-key"}],
    )

    result = sec_mod.list_secrets()
    assert result["orphans"] == ["my-app-stale-key"]
    hint = result["hints"][0]
    assert hint["issue"] == "orphan"
    assert hint["secret_id"] == "my-app-stale-key"
    assert len(hint["options"]) == 2
    delete_opt = hint["options"][0]
    assert "Delete" in delete_opt["label"]
    assert "gcloud secrets delete my-app-stale-key" in delete_opt["command"]
    assert "--project=my-project" in delete_opt["command"]


# -- CLI rendering --


def test_cli_secrets_list_renders_table_orphans_and_footnotes(tmp_path, monkeypatch):
    """`gapp secrets list` text output: status table + orphans block + footnoted hints."""
    from click.testing import CliRunner
    from gapp.admin.cli.main import main as cli_main
    from gapp.admin.sdk import secrets as sec_mod

    fixed = {
        "solution": "my-app",
        "project_id": "my-project",
        "secrets": [
            {"name": "api-token", "env_var": "API_TOKEN",
             "secret_id": "my-app-api-token", "generate": False, "status": "unattached"},
            {"name": "app-key", "env_var": "APP_KEY",
             "secret_id": "my-app-app-key", "generate": False, "status": "ready"},
        ],
        "orphans": ["my-app-stale-key"],
        "hints": [
            {
                "secret_id": "my-app-api-token", "issue": "unattached",
                "message": "Secret 'my-app-api-token' exists but has no label.",
                "options": [
                    {"label": "Adopt", "command": "gcloud secrets update my-app-api-token ..."},
                    {"label": "Delete", "command": "gcloud secrets delete my-app-api-token ..."},
                ],
            },
            {
                "secret_id": "my-app-stale-key", "issue": "orphan",
                "message": "Secret 'my-app-stale-key' is labeled but not declared.",
                "options": [
                    {"label": "Delete", "command": "gcloud secrets delete my-app-stale-key ..."},
                ],
            },
        ],
    }
    monkeypatch.setattr(sec_mod, "list_secrets", lambda solution=None: fixed)

    result = CliRunner().invoke(cli_main, ["secrets", "list"])
    assert result.exit_code == 0
    out = result.output

    assert "App:     my-app" in out
    assert "Project: my-project" in out
    assert "api-token" in out and "API_TOKEN" in out and "unattached" in out
    assert "app-key" in out and "ready" in out
    assert "Orphans" in out and "my-app-stale-key" in out
    assert "Resolution options" in out
    assert "[1] my-app-api-token — unattached" in out
    assert "gcloud secrets update my-app-api-token" in out
    assert "[2] my-app-stale-key — orphan" in out
    assert "gcloud secrets delete my-app-stale-key" in out


def test_cli_secrets_list_no_hints_when_clean(tmp_path, monkeypatch):
    """All-ready solution: no Resolution options block, no Orphans block."""
    from click.testing import CliRunner
    from gapp.admin.cli.main import main as cli_main
    from gapp.admin.sdk import secrets as sec_mod

    fixed = {
        "solution": "my-app",
        "project_id": "my-project",
        "secrets": [
            {"name": "api-token", "env_var": "API_TOKEN",
             "secret_id": "my-app-api-token", "generate": False, "status": "ready"},
        ],
        "orphans": [],
        "hints": [],
    }
    monkeypatch.setattr(sec_mod, "list_secrets", lambda solution=None: fixed)

    result = CliRunner().invoke(cli_main, ["secrets", "list"])
    assert result.exit_code == 0
    assert "Resolution options" not in result.output
    assert "Orphans" not in result.output
    assert "ready" in result.output
