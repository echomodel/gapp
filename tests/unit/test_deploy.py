"""Tests for gapp.sdk.core — deploy and dry-run."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _repo(tmp_path, monkeypatch, contents="name: my-app"):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text(contents)
    monkeypatch.chdir(repo)
    return repo


def test_deploy_dry_run_singular(tmp_path, monkeypatch, sdk):
    """Dry-run resolves a single-match deployment."""
    _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["proj-123"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    res = sdk.deploy(dry_run=True)

    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["label"] == "gapp__my-app"
    assert res["project_id"] == "proj-123"
    assert res["env"] == "prod"


def test_deploy_dry_run_workspace(tmp_path, monkeypatch, sdk):
    """Dry-run unrolls multi-service workspace."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("paths: [services/api, services/worker]")
    api_dir = repo / "services/api"
    api_dir.mkdir(parents=True)
    (api_dir / "gapp.yaml").write_text("name: my-api")
    worker_dir = repo / "services/worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "gapp.yaml").write_text("name: my-worker")
    monkeypatch.chdir(repo)

    sdk.provider.project_labels["proj-ws"] = {"gapp__app": "v-3"}

    res = sdk.deploy(dry_run=True)

    assert res["name"] == "app"
    assert len(res["services"]) == 2


def test_deploy_dry_run_with_owner(tmp_path, monkeypatch, sdk):
    """Dry-run includes owner-scoped label."""
    _repo(tmp_path, monkeypatch)
    sdk.set_owner("owner-a")
    sdk.provider.project_labels["proj-123"] = {
        "gapp_owner-a_my-app": "v-3",
    }

    res = sdk.deploy(dry_run=True)

    assert res["owner"] == "owner-a"
    assert res["label"] == "gapp_owner-a_my-app"
    assert res["env"] is None  # project has no gapp-env binding


def test_deploy_dry_run_no_setup_pending(tmp_path, monkeypatch, sdk):
    """Dry-run with no project resolved still returns a preview."""
    _repo(tmp_path, monkeypatch)
    res = sdk.deploy(dry_run=True)

    assert res["dry_run"] is True
    assert res["status"] == "pending_setup"
    assert res["project_id"] is None


# -- --rebuild flag and --ref threading --


def _setup_real_deploy(tmp_path, monkeypatch, sdk, ref_sha="abc123def456"):
    """Set up a deployable repo + populate provider state for a real apply."""
    repo = _repo(tmp_path, monkeypatch)
    sdk.provider.project_labels["proj-123"] = {
        "gapp__my-app": "v-3",
    }
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123"}

    captured = {}

    def fake_resolve_ref(self, path, ref):
        captured["resolved_ref"] = ref
        return ref_sha

    def fake_prepare_build_dir(path, image, ep, ref="HEAD"):
        captured["build_ref"] = ref
        return str(tmp_path / "build"), ep

    monkeypatch.setattr(GappSDK, "_resolve_ref", fake_resolve_ref)
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(core_mod, "_prepare_build_dir", fake_prepare_build_dir)
    (tmp_path / "build").mkdir()

    builds_called = {"count": 0}
    original_submit = sdk.provider.submit_build_sync

    def counting_submit(*args, **kwargs):
        builds_called["count"] += 1
        return original_submit(*args, **kwargs)

    sdk.provider.submit_build_sync = counting_submit
    return repo, captured, builds_called


def test_deploy_skips_build_when_image_exists(tmp_path, monkeypatch, sdk):
    """Default behavior: existing image short-circuits docker build."""
    _, _, builds_called = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: True

    sdk.deploy()

    assert builds_called["count"] == 0


def test_deploy_rebuild_forces_build_even_when_image_exists(tmp_path, monkeypatch, sdk):
    """--rebuild bypasses the image-exists short-circuit."""
    _, _, builds_called = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: True

    sdk.deploy(rebuild=True)

    assert builds_called["count"] == 1


def test_deploy_ref_is_threaded_into_build(tmp_path, monkeypatch, sdk):
    """--ref reaches both _resolve_ref and _prepare_build_dir (no silent HEAD)."""
    _, captured, _ = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: False

    sdk.deploy(ref="v1.2.3")

    assert captured["resolved_ref"] == "v1.2.3"
    assert captured["build_ref"] == "v1.2.3"


def test_deploy_default_ref_is_head(tmp_path, monkeypatch, sdk):
    """No --ref → resolve and archive HEAD."""
    _, captured, _ = _setup_real_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.image_exists = lambda *a, **kw: False

    sdk.deploy()

    assert captured["resolved_ref"] == "HEAD"
    assert captured["build_ref"] == "HEAD"


# -- env-secret materialization (issue #34) --


def _patch_secret_calls(monkeypatch, present_ids=()):
    """Stub the gcloud-backed secret helpers so deploy tests don't shell out.

    Returns a `calls` dict that records every materialize side-effect.
    """
    import gapp.admin.sdk.secrets as secrets_mod

    calls = {
        "list_secrets_by_label": [],
        "ensure_secret": [],
        "add_secret_version": [],
    }

    def fake_list(project_id, solution_name):
        calls["list_secrets_by_label"].append((project_id, solution_name))
        return [{"id": sid} for sid in present_ids]

    def fake_ensure(project_id, secret_id, solution_name):
        calls["ensure_secret"].append(secret_id)
        return "created"

    def fake_add_version(project_id, secret_id, value):
        calls["add_secret_version"].append((secret_id, len(value)))

    monkeypatch.setattr(secrets_mod, "list_secrets_by_label", fake_list)
    monkeypatch.setattr(secrets_mod, "_ensure_secret", fake_ensure)
    monkeypatch.setattr(secrets_mod, "_add_secret_version", fake_add_version)
    return calls


def test_build_tfvars_emits_env_secret_declarations(tmp_path):
    """env: [{secret: {name, generate}}] entries reach the secrets tfvar."""
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: SIGNING_KEY\n"
        "    secret:\n"
        "      name: signing-key\n"
        "      generate: true\n"
        "  - name: API_TOKEN\n"
        "    secret:\n"
        "      name: api-token\n"
    )

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 5, "env": {}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, {}, repo, False, "",
        solution_name="my-app",
    )

    assert tfvars["secrets"] == {
        "SIGNING_KEY": "my-app-signing-key",
        "API_TOKEN": "my-app-api-token",
    }


def test_build_tfvars_uses_solution_name_for_workspace_services(tmp_path):
    """Service in a workspace pulls secret IDs from parent solution name, not service name."""
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-svc\n"
        "env:\n"
        "  - name: SIGNING_KEY\n"
        "    secret: {name: signing-key, generate: true}\n"
    )

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 5, "env": {}}
    tfvars = _build_tfvars(
        "my-svc", "proj-123", "img:tag", cfg, {}, repo, False, "",
        solution_name="parent-app",
    )

    assert tfvars["secrets"] == {"SIGNING_KEY": "parent-app-signing-key"}


def test_deploy_materializes_generated_secrets(tmp_path, monkeypatch, sdk):
    """generate: true → ensure_secret + add_secret_version called with 32-char value."""
    repo = _repo(
        tmp_path,
        monkeypatch,
        contents=(
            "name: my-app\n"
            "env:\n"
            "  - name: SIGNING_KEY\n"
            "    secret: {name: signing-key, generate: true}\n"
        ),
    )
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123"}

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True

    calls = _patch_secret_calls(monkeypatch, present_ids=())

    sdk.deploy()

    assert calls["ensure_secret"] == ["my-app-signing-key"]
    assert len(calls["add_secret_version"]) == 1
    assert calls["add_secret_version"][0] == ("my-app-signing-key", 32)
    assert sdk.provider.last_tfvars["secrets"] == {"SIGNING_KEY": "my-app-signing-key"}


def test_deploy_skips_existing_generated_secret(tmp_path, monkeypatch, sdk):
    """Idempotent: redeploys never overwrite an already-materialized generated secret."""
    _repo(
        tmp_path, monkeypatch,
        contents=(
            "name: my-app\n"
            "env:\n"
            "  - name: SIGNING_KEY\n"
            "    secret: {name: signing-key, generate: true}\n"
        ),
    )
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123"}

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True

    calls = _patch_secret_calls(monkeypatch, present_ids=("my-app-signing-key",))

    sdk.deploy()

    assert calls["ensure_secret"] == []
    assert calls["add_secret_version"] == []
    assert sdk.provider.last_tfvars["secrets"] == {"SIGNING_KEY": "my-app-signing-key"}


def test_deploy_fails_fast_on_missing_non_generate_secret(tmp_path, monkeypatch, sdk):
    """Non-generate secret missing in GCP → deploy raises before build/apply."""
    _repo(
        tmp_path, monkeypatch,
        contents=(
            "name: my-app\n"
            "env:\n"
            "  - name: API_TOKEN\n"
            "    secret: {name: api-token}\n"
        ),
    )
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123"}

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True

    _patch_secret_calls(monkeypatch, present_ids=())

    with pytest.raises(RuntimeError, match="api-token"):
        sdk.deploy()


def test_materialize_generated_secrets_idempotent(monkeypatch):
    """Second call with the secret already present does not write a new version."""
    from gapp.admin.sdk.secrets import materialize_generated_secrets

    manifest = {
        "name": "my-app",
        "env": [
            {"name": "SIGNING_KEY", "secret": {"name": "signing-key", "generate": True}},
        ],
    }

    calls = _patch_secret_calls(monkeypatch, present_ids=("my-app-signing-key",))
    results = materialize_generated_secrets("proj-123", "my-app", manifest)

    assert results == [{"name": "signing-key", "secret_id": "my-app-signing-key", "status": "exists"}]
    assert calls["ensure_secret"] == []
    assert calls["add_secret_version"] == []
