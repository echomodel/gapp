"""`gapp ci init` installs gapp's files in the CI repo and reports the
operator-set variables; every gcloud call honours `gapp config account`.

The CI repo holds one generic deploy workflow, one register workflow and
the solutions data file, all gapp-owned: `ci init` writes them when
missing or outdated and never touches the data file once it exists. The
hosting app's credentials and the owner allowlist belong to the operator,
under fixed names; `ci init` checks and reports, never sets. The
configured account reaches gcloud through CLOUDSDK_CORE_ACCOUNT on every
call the provider and the CI module make, and a configured account
without a login fails by name before any cloud step runs.
"""

import os
import subprocess

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import util
from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.core import GappSDK

CI_REPO = "acme/ci"
FILES = (ci_mod.DEPLOY_WORKFLOW_PATH, ci_mod.REGISTER_WORKFLOW_PATH, ci_mod.SOLUTIONS_FILE)


@pytest.fixture
def init_env(monkeypatch, fake_gh):
    """init_ci against the in-memory GitHub: the topic is already set."""
    monkeypatch.setattr(ci_mod, "_find_ci_repo_by_topic", lambda: CI_REPO)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v3.9.0")
    return fake_gh


def test_init_installs_the_three_files_on_a_fresh_repo(init_env):
    res = ci_mod.init_ci(CI_REPO)
    assert res["topic_status"] == "already_set"
    assert res["files"] == {p: "created" for p in FILES}
    deploy = yaml.safe_load(init_env.files[(CI_REPO, ci_mod.DEPLOY_WORKFLOW_PATH)])
    assert deploy["jobs"]["deploy"]["uses"] == "example-org/gapp/.github/workflows/deploy.yml@v3.9.0"
    assert deploy["jobs"]["deploy"]["with"]["gapp-repo"] == "example-org/gapp"
    register = yaml.safe_load(init_env.files[(CI_REPO, ci_mod.REGISTER_WORKFLOW_PATH)])
    assert set(register[True]["workflow_dispatch"]["inputs"]) == {"solution", "solution-repo", "project", "domain", "gapp-ref"}
    assert yaml.safe_load(init_env.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)]) == {"solutions": []}


def test_init_is_idempotent_and_never_overwrites_the_data_file(init_env):
    ci_mod.init_ci(CI_REPO)
    init_env.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)] = "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: my-proj\n"
    writes_before = len(init_env.puts)
    res = ci_mod.init_ci(CI_REPO)
    assert res["files"] == {p: "unchanged" for p in FILES}
    assert len(init_env.puts) == writes_before
    assert "my-app" in init_env.files[(CI_REPO, ci_mod.SOLUTIONS_FILE)]


def test_init_updates_outdated_workflows_beside_existing_leaf_workflows(init_env, monkeypatch):
    leaf = (CI_REPO, ".github/workflows/old-app.yml")
    init_env.files[leaf] = "name: Deploy old-app\n"
    init_env.install(CI_REPO, gapp_ref="v3.8.0")
    res = ci_mod.init_ci(CI_REPO)
    assert res["files"][ci_mod.DEPLOY_WORKFLOW_PATH] == "updated"       # pins the new gapp ref
    assert res["files"][ci_mod.REGISTER_WORKFLOW_PATH] == "unchanged"   # carries no ref
    assert res["files"][ci_mod.SOLUTIONS_FILE] == "unchanged"
    assert "@v3.9.0" in init_env.files[(CI_REPO, ci_mod.DEPLOY_WORKFLOW_PATH)]
    assert init_env.files[leaf] == "name: Deploy old-app\n"          # untouched


def test_init_reports_the_three_variables_with_commands_when_missing(init_env):
    res = ci_mod.init_ci(CI_REPO)
    assert res["variables"] == {"GAPP_ALLOWED_OWNERS": False, "HOSTING_GITHUB_APP_ID": False,
                                "HOSTING_GITHUB_APP_PRIVATE_KEY": False}
    hint = res["next_step"]["hint"]
    assert res["next_step"]["action"] == "configure variables"
    assert f"gh variable set GAPP_ALLOWED_OWNERS --repo {CI_REPO} --body <owner1,owner2>" in hint
    assert f"gh variable set HOSTING_GITHUB_APP_ID --repo {CI_REPO} --body <app-id>" in hint
    assert f"gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo {CI_REPO} < key.pem" in hint
    assert "Only private solution repos need them" in hint


def test_init_with_everything_set_has_no_next_step(init_env):
    init_env.variables[(CI_REPO, "GAPP_ALLOWED_OWNERS")] = "me"
    init_env.variables[(CI_REPO, "HOSTING_GITHUB_APP_ID")] = "1"
    init_env.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    res = ci_mod.init_ci(CI_REPO)
    assert all(res["variables"].values())
    assert res["next_step"] is None


def test_init_never_sets_the_variables(init_env):
    ci_mod.init_ci(CI_REPO)
    assert init_env.variables == {} and init_env.secrets == set()
    assert all("/actions/" not in cmd[cmd.index("-X") + 2] for cmd, _ in init_env.calls if "-X" in cmd)


def test_init_reports_unchecked_when_the_actions_api_is_unreachable(init_env):
    init_env.actions_api = False
    res = ci_mod.init_ci(CI_REPO)
    assert res["variables_checked"] is False
    assert "not checked from this machine" in res["variables_note"]
    assert res["next_step"] is None


def test_init_where_api_writes_are_refused_names_the_files_and_the_machine(init_env):
    init_env.api_writes = False
    res = ci_mod.init_ci(CI_REPO)
    assert res["files"] is None
    assert res["next_step"]["action"] == "install operator files"
    for path in FILES:
        assert path in res["next_step"]["hint"]
    assert f"gapp ci init {CI_REPO}" in res["next_step"]["hint"]


def test_init_local_only_skips_github(fake_gh):
    res = ci_mod.init_ci(CI_REPO, local_only=True)
    assert res["topic_status"] == "skipped"
    assert "files" not in res and "variables" not in res
    assert fake_gh.calls == []


# -- account honesty --


def _capture_runs(monkeypatch):
    seen = []

    def run(cmd, **kwargs):
        seen.append((cmd, kwargs.get("env")))
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_provider_is_bound_to_the_configured_account(monkeypatch):
    GappSDK().set_account("deployer@example.com")
    provider = get_provider()
    assert provider.account == "deployer@example.com"
    seen = _capture_runs(monkeypatch)
    provider.list_projects()
    cmd, env = seen[-1]
    assert cmd[:3] == ["gcloud", "projects", "list"]
    assert env["CLOUDSDK_CORE_ACCOUNT"] == "deployer@example.com"


def test_provider_without_configured_account_uses_gcloud_default(monkeypatch):
    GappSDK().set_account(None)
    provider = get_provider()
    assert provider.account is None
    seen = _capture_runs(monkeypatch)
    provider.list_projects()
    _, env = seen[-1]
    assert "CLOUDSDK_CORE_ACCOUNT" not in (env or os.environ)


def test_ci_module_gcloud_calls_carry_the_configured_account(monkeypatch):
    GappSDK().set_account("deployer@example.com")
    seen = _capture_runs(monkeypatch)
    ci_mod._get_project_number("my-proj")
    ci_mod._ensure_wif_pool("my-proj")
    assert len(seen) >= 2
    for cmd, env in seen:
        assert cmd[0] == "gcloud"
        assert env["CLOUDSDK_CORE_ACCOUNT"] == "deployer@example.com"


def test_require_gcloud_account_fails_by_name_when_not_credentialed(monkeypatch):
    GappSDK().set_account("deployer@example.com")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    with pytest.raises(RuntimeError) as exc:
        util.require_gcloud_account()
    assert "deployer@example.com" in str(exc.value)
    assert "gcloud auth login deployer@example.com" in str(exc.value)


def test_require_gcloud_account_passes_when_credentialed(monkeypatch):
    GappSDK().set_account("deployer@example.com")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, stdout="deployer@example.com\n", stderr=""))
    assert util.require_gcloud_account() == "deployer@example.com"


def test_require_gcloud_account_is_a_no_op_without_configured_account(monkeypatch):
    GappSDK().set_account(None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: pytest.fail("gcloud must not run"))
    assert util.require_gcloud_account() is None
