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
    sdk.provider.host("proj-123", "my-app")

    res = sdk.deploy(dry_run=True)

    assert res["dry_run"] is True
    assert res["name"] == "my-app"
    assert res["labels"] == "gapp-deployment=my-app, gapp-version=4"
    assert "owner" not in res
    assert res["project_id"] == "proj-123"
    assert "env" not in res


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

    sdk.provider.host("proj-ws", "app")

    res = sdk.deploy(dry_run=True)

    assert res["name"] == "app"
    assert len(res["services"]) == 2


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
    sdk.provider.host("proj-123", "my-app", service=False)

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
        "  - name: APP_KEY\n"
        "    secret:\n"
        "      name: app-key\n"
        "      generate: true\n"
        "  - name: API_TOKEN\n"
        "    secret:\n"
        "      name: api-token\n"
    )

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 5, "env": {}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, {}, repo, False, "",
        "gapp-my-app-proj-123",
        solution_name="my-app",
    )

    assert tfvars["secrets"] == {
        "APP_KEY": "my-app-app-key",
        "API_TOKEN": "my-app-api-token",
    }


def test_build_tfvars_merges_env_secrets_with_prerequisites_secrets(tmp_path):
    """Env-secret declarations and prerequisites.secrets both reach the secrets tfvar."""
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-app\n"
        "env:\n"
        "  - name: APP_KEY\n"
        "    secret: {name: app-key, generate: true}\n"
    )

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 5, "env": {}}
    prerequisites = {"legacy-secret": {"description": "from old prerequisites block"}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, prerequisites, repo, False, "",
        "gapp-my-app-proj-123",
        solution_name="my-app",
    )

    assert tfvars["secrets"]["APP_KEY"] == "my-app-app-key"
    assert "LEGACY_SECRET" in tfvars["secrets"]
    assert len(tfvars["secrets"]) == 2


def test_build_tfvars_uses_solution_name_for_workspace_services(tmp_path):
    """Service in a workspace pulls secret IDs from parent solution name, not service name."""
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text(
        "name: my-svc\n"
        "env:\n"
        "  - name: APP_KEY\n"
        "    secret: {name: app-key, generate: true}\n"
    )

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 5, "env": {}}
    tfvars = _build_tfvars(
        "my-svc", "proj-123", "img:tag", cfg, {}, repo, False, "",
        "gapp-parent-app-proj-123",
        solution_name="parent-app",
    )

    assert tfvars["secrets"] == {"APP_KEY": "parent-app-app-key"}


# -- data_bucket wiring (issue #35) --


def test_build_tfvars_emits_data_bucket(tmp_path):
    """data_bucket reaches the tfvars dict verbatim from the caller."""
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: my-app\n")

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 1, "env": {}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, {}, repo, False, "",
        "gapp-my-app-proj-123",
        solution_name="my-app",
    )

    assert tfvars["data_bucket"] == "gapp-my-app-proj-123"


def test_build_tfvars_data_bucket_matches_get_bucket_name_convention(tmp_path):
    """The data_bucket emitted matches whatever GappSDK.get_bucket_name returns
    for the same (solution, project) — same naming convention end to end."""
    from gapp.admin.sdk.core import _build_tfvars, GappSDK
    from gapp.admin.sdk.cloud.dummy import DummyCloudProvider

    sdk = GappSDK(provider=DummyCloudProvider())
    expected = sdk.get_bucket_name("my-app", "proj-123")

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: my-app\n")
    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 1, "env": {}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, {}, repo, False, "",
        expected,
        solution_name="my-app",
    )

    assert tfvars["data_bucket"] == expected


REQUIRED_TFVAR_KEYS = (
    "project_id",
    "service_name",
    "image",
    "memory",
    "cpu",
    "max_instances",
    "env",
    "secrets",
    "public",
    "custom_domain",
    "data_bucket",
)


@pytest.mark.parametrize("key", REQUIRED_TFVAR_KEYS)
def test_build_tfvars_emits_every_required_key(tmp_path, key):
    """Every key the cloud-run-service module declares as a required tfvar must
    be present in the dict _build_tfvars returns. The next refactor that drops
    one fails this test instead of silently breaking a live deploy.

    See issue #35: a prior consolidation lost `data_bucket` from this dict and
    the conditional in main.tf turned that into a silent volume strip across
    every gapp-deployed solution that uses {{SOLUTION_DATA_PATH}}.
    """
    from gapp.admin.sdk.core import _build_tfvars

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "gapp.yaml").write_text("name: my-app\n")

    cfg = {"memory": "512Mi", "cpu": "1", "max_instances": 1, "env": {}}
    tfvars = _build_tfvars(
        "my-app", "proj-123", "img:tag", cfg, {}, repo, False, "",
        "gapp-my-app-proj-123",
        solution_name="my-app",
    )

    assert key in tfvars, f"_build_tfvars dropped required tfvar '{key}'"


def test_deploy_threads_data_bucket_into_apply_tfvars(tmp_path, monkeypatch, sdk):
    """End-to-end: deploy() reaches apply_infrastructure with the right data_bucket.

    Catches regressions where bucket_name is computed but not threaded into the
    tfvars call — the exact shape of issue #35.
    """
    _repo(tmp_path, monkeypatch)
    sdk.provider.host("proj-123", "my-app", service=False)

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True
    _patch_secret_calls(monkeypatch, present_ids=())

    sdk.deploy()

    assert sdk.provider.last_tfvars["data_bucket"] == "gapp-my-app-proj-123"


def test_deploy_workspace_threads_parent_data_bucket_into_each_service(
    tmp_path, monkeypatch, sdk
):
    """Workspace services share the parent solution's data bucket, not their own."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("paths: [services/api]")
    api_dir = repo / "services/api"
    api_dir.mkdir(parents=True)
    (api_dir / "gapp.yaml").write_text("name: my-api\n")
    monkeypatch.chdir(repo)

    sdk.provider.host("proj-ws", "app", service=False)

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True
    _patch_secret_calls(monkeypatch, present_ids=())

    sdk.deploy()

    # Service was deployed under the parent solution's bucket, not "gapp-my-api-…".
    assert sdk.provider.last_tfvars["data_bucket"] == "gapp-app-proj-ws"
    # Each service carries its parent deployment's labels.
    assert sdk.provider.last_tfvars["labels"] == {"gapp-deployment": "app", "gapp-version": "4"}
    assert sdk.provider.services[("proj-ws", "my-api")]["gapp-deployment"] == "app"


def test_deploy_materializes_generated_secrets(tmp_path, monkeypatch, sdk):
    """generate: true → ensure_secret + add_secret_version called with 32-char value."""
    repo = _repo(
        tmp_path,
        monkeypatch,
        contents=(
            "name: my-app\n"
            "env:\n"
            "  - name: APP_KEY\n"
            "    secret: {name: app-key, generate: true}\n"
        ),
    )
    sdk.provider.host("proj-123", "my-app", service=False)

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

    assert calls["ensure_secret"] == ["my-app-app-key"]
    assert len(calls["add_secret_version"]) == 1
    assert calls["add_secret_version"][0] == ("my-app-app-key", 32)
    assert sdk.provider.last_tfvars["secrets"] == {"APP_KEY": "my-app-app-key"}


def test_deploy_skips_existing_generated_secret(tmp_path, monkeypatch, sdk):
    """Idempotent: redeploys never overwrite an already-materialized generated secret."""
    _repo(
        tmp_path, monkeypatch,
        contents=(
            "name: my-app\n"
            "env:\n"
            "  - name: APP_KEY\n"
            "    secret: {name: app-key, generate: true}\n"
        ),
    )
    sdk.provider.host("proj-123", "my-app", service=False)

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir()
    sdk.provider.image_exists = lambda *a, **kw: True

    calls = _patch_secret_calls(monkeypatch, present_ids=("my-app-app-key",))

    sdk.deploy()

    assert calls["ensure_secret"] == []
    assert calls["add_secret_version"] == []
    assert sdk.provider.last_tfvars["secrets"] == {"APP_KEY": "my-app-app-key"}


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
    sdk.provider.host("proj-123", "my-app", service=False)

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
            {"name": "APP_KEY", "secret": {"name": "app-key", "generate": True}},
        ],
    }

    calls = _patch_secret_calls(monkeypatch, present_ids=("my-app-app-key",))
    results = materialize_generated_secrets("proj-123", "my-app", manifest)

    assert results == [{"name": "app-key", "secret_id": "my-app-app-key", "status": "exists"}]
    assert calls["ensure_secret"] == []
    assert calls["add_secret_version"] == []


# -- custom domain safety --
#
# Terraform replaces a changed domain mapping by destroying the old one
# first, so a typo, a placeholder, or an omitted `domain:` would take the
# live domain down. deploy() refuses those before any build or apply unless
# replace_domain=True, and refuses an unverified domain before terraform.


def _domain_deploy(tmp_path, monkeypatch, sdk, manifest, live_domain=None, verified=()):
    """A deployable repo with `manifest`, a recorded live mapping, and a verified-domain list."""
    _repo(tmp_path, monkeypatch, contents=manifest)
    sdk.provider.host("proj-123", "my-app", service=False)
    if live_domain is not None:
        sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state")] = {
            "service_url": "https://my-app.a.run.app", "custom_domain": live_domain,
        }
    sdk.provider.verified_domains.update(verified)

    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(
        core_mod, "_prepare_build_dir",
        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep),
    )
    (tmp_path / "build").mkdir(exist_ok=True)
    _patch_secret_calls(monkeypatch, present_ids=())

    builds = {"count": 0}
    original = sdk.provider.submit_build_sync

    def counting(*a, **kw):
        builds["count"] += 1
        return original(*a, **kw)

    sdk.provider.submit_build_sync = counting
    sdk.provider.image_exists = lambda *a, **kw: False
    return builds


def test_deploy_refuses_to_replace_live_domain_without_flag(tmp_path, monkeypatch, sdk):
    builds = _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\ndomain: mcp.example.com\n",
        live_domain="mcp.example.net", verified={"example.com"},
    )
    with pytest.raises(RuntimeError, match="replace the live domain mapping") as exc:
        sdk.deploy()
    assert "mcp.example.net" in str(exc.value)          # names the domain that would go
    assert "mcp.example.com" in str(exc.value)
    assert "--replace-domain" in str(exc.value)
    assert builds["count"] == 0                        # refused before the build
    assert sdk.provider.last_tfvars is None            # and before terraform


def test_deploy_refuses_to_remove_live_domain_without_flag(tmp_path, monkeypatch, sdk):
    builds = _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\n", live_domain="mcp.example.com",
    )
    with pytest.raises(RuntimeError, match="remove the live domain mapping mcp.example.com"):
        sdk.deploy()
    assert builds["count"] == 0
    assert sdk.provider.last_tfvars is None


def test_deploy_replace_domain_flag_allows_the_change(tmp_path, monkeypatch, sdk):
    _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\ndomain: mcp.example.com\n",
        live_domain="mcp.example.net", verified={"example.com"},
    )
    sdk.deploy(replace_domain=True)
    assert sdk.provider.last_tfvars["custom_domain"] == "mcp.example.com"


def test_deploy_replace_domain_flag_allows_removal(tmp_path, monkeypatch, sdk):
    _domain_deploy(tmp_path, monkeypatch, sdk, "name: my-app\n", live_domain="mcp.example.com")
    sdk.deploy(replace_domain=True)
    assert sdk.provider.last_tfvars["custom_domain"] == ""


def test_deploy_refuses_unverified_domain_before_build(tmp_path, monkeypatch, sdk):
    builds = _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\ndomain: mcp.example.com\n",
        live_domain=None, verified={"example.net"},
    )
    with pytest.raises(RuntimeError, match="not verified ownership of mcp.example.com") as exc:
        sdk.deploy()
    assert "gcloud domains verify example.com" in str(exc.value)
    assert "gapp-deploy@" in str(exc.value)          # names the CI deploy identity
    assert "verified owner" in str(exc.value)        # and the Search Console step
    assert builds["count"] == 0
    assert sdk.provider.last_tfvars is None


def test_deploy_accepts_domain_verified_at_a_parent(tmp_path, monkeypatch, sdk):
    _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\ndomain: mcp.app.example.com\n",
        live_domain="", verified={"example.com"},
    )
    sdk.deploy()
    assert sdk.provider.last_tfvars["custom_domain"] == "mcp.app.example.com"


def test_redeploy_with_same_domain_needs_no_verification_or_flag(tmp_path, monkeypatch, sdk):
    """The mapping is untouched, so an identity that never verified the domain
    (a CI service account, say) can still redeploy."""
    _domain_deploy(
        tmp_path, monkeypatch, sdk, "name: my-app\ndomain: mcp.example.com\n",
        live_domain="mcp.example.com", verified=set(),
    )
    sdk.deploy()
    assert sdk.provider.last_tfvars["custom_domain"] == "mcp.example.com"


def test_first_deploy_without_domain_runs_unguarded(tmp_path, monkeypatch, sdk):
    _domain_deploy(tmp_path, monkeypatch, sdk, "name: my-app\n", live_domain=None)
    sdk.deploy()
    assert sdk.provider.last_tfvars["custom_domain"] == ""


def test_domain_is_verified_matches_exact_and_parent_only():
    from gapp.admin.sdk.core import _domain_is_verified
    assert _domain_is_verified("mcp.example.com", ["example.com"])
    assert _domain_is_verified("mcp.example.com", ["MCP.example.com."])
    assert _domain_is_verified("a.b.example.com", ["b.example.com"])
    assert not _domain_is_verified("mcp.example.com", ["ample.com"])      # not a label boundary
    assert not _domain_is_verified("mcp.example.com", ["mcp.example.com.au"])
    assert not _domain_is_verified("mcp.example.com", [])


def test_deploy_fails_closed_when_the_terraform_state_cannot_be_read(tmp_path, monkeypatch, sdk):
    """Unreadable state is not "no live mapping": the deploy refuses before any build or apply."""
    builds = _domain_deploy(tmp_path, monkeypatch, sdk, "name: my-app\n", live_domain="mcp.example.com")
    sdk.provider.unreadable_state.add(("gapp-my-app-proj-123", "terraform/state"))
    with pytest.raises(RuntimeError, match="Refusing to deploy 'my-app': its Terraform state could not be read") as exc:
        sdk.deploy(replace_domain=True)                    # not even the flag gets past an unknown state
    assert "Nothing was built or changed" in str(exc.value) and "gapp-my-app-proj-123" in str(exc.value)
    assert builds["count"] == 0 and sdk.provider.last_tfvars is None


# -- deployment labels, the version guard, CI runs, and the migration --


def _plain_deploy(tmp_path, monkeypatch, sdk):
    """A deployable single-service repo; nothing in GCP yet."""
    _repo(tmp_path, monkeypatch)
    monkeypatch.setattr(GappSDK, "_resolve_ref", lambda self, p, r: "abc123")
    import gapp.admin.sdk.core as core_mod
    monkeypatch.setattr(core_mod, "_prepare_build_dir",
                        lambda path, image, ep, ref="HEAD": (str(tmp_path / "build"), ep))
    (tmp_path / "build").mkdir(exist_ok=True)
    sdk.provider.image_exists = lambda *a, **kw: True
    _patch_secret_calls(monkeypatch, present_ids=())


def test_every_deploy_labels_the_bucket_and_the_service(tmp_path, monkeypatch, sdk):
    _plain_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.host("proj-123", "my-app", service=False, version="3")
    sdk.deploy()
    assert sdk.provider.last_tfvars["labels"] == {"gapp-deployment": "my-app", "gapp-version": "4"}
    assert sdk.provider.services[("proj-123", "my-app")] == {"gapp-deployment": "my-app", "gapp-version": "4"}
    assert sdk.provider.buckets["gapp-my-app-proj-123"]["labels"]["gapp-version"] == "4"
    sdk.deploy()                                                    # a redeploy labels again, cleanly
    assert sdk.provider.services[("proj-123", "my-app")]["gapp-version"] == "4"


def test_a_deploy_never_writes_project_labels(tmp_path, monkeypatch, sdk):
    _plain_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.host("proj-123", "my-app", service=False)
    sdk.deploy()
    assert not [w for w in sdk.provider.label_writes if w[0] == "project"]


def test_deploy_refuses_a_deployment_a_newer_gapp_deployed(tmp_path, monkeypatch, sdk):
    _plain_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.host("proj-123", "my-app", version="5")
    with pytest.raises(RuntimeError) as exc:
        sdk.deploy()
    assert str(exc.value) == (
        "Deployment 'my-app' on project 'proj-123' was deployed by gapp 5.x (service label "
        f"gapp-version=5); this gapp is v{__import__('gapp').__version__}. Upgrade gapp to deploy it."
    )
    assert sdk.provider.last_tfvars is None


def test_deploy_without_a_bucket_is_foundation_missing(tmp_path, monkeypatch, sdk):
    _plain_deploy(tmp_path, monkeypatch, sdk)
    with pytest.raises(RuntimeError, match="Foundation missing. Run 'gapp setup'."):
        sdk.deploy(project_id="proj-123")


def test_ci_deploy_with_project_needs_no_project_labels_at_all(tmp_path, monkeypatch, sdk):
    """The CI path: setup and deploy with --project as the deploy identity, which may not
    write project labels. Everything but the project mark is done, and the deploy succeeds."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _plain_deploy(tmp_path, monkeypatch, sdk)

    def refuse(project_id, labels):
        raise AssertionError("a CI run must not write project labels")

    sdk.provider.set_project_labels = refuse
    monkeypatch.setattr(sdk.provider, "list_projects", lambda *a, **k: pytest.fail("no search with --project"))
    res = sdk.setup(project_id="proj-123")
    assert res["label_status"] == "skipped" and res["bucket_status"] == "created"
    sdk.deploy(project_id="proj-123")
    assert sdk.provider.services[("proj-123", "my-app")]["gapp-version"] == "4"


def test_migration_a_3x_deployment_is_found_by_name_after_one_redeploy_with_project(tmp_path, monkeypatch, sdk):
    """As 3.x left it: a v-3 project label, a bucket and service without gapp labels.
    By name it is not found; after `gapp setup` (marks the project) and a redeploy with
    --project, it is found by name. The old label is never read."""
    _plain_deploy(tmp_path, monkeypatch, sdk)
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.buckets["gapp-my-app-proj-123"] = {"project": "proj-123", "labels": {}}
    with pytest.raises(RuntimeError, match="No deployment 'my-app' found in any project marked gapp=hosted"):
        sdk.deploy()
    sdk.setup(project_id="proj-123")
    sdk.deploy(project_id="proj-123")
    assert sdk.resolve_project_for_solution("my-app")["project_id"] == "proj-123"
    assert sdk.provider.project_labels["proj-123"]["gapp__my-app"] == "v-3"      # untouched
