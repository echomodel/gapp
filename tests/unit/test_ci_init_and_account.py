"""`gapp ci init` reports the hosting app's fixed variable and secret, and
every gcloud call honours `gapp config account`.

The hosting app's credentials belong to the CI repo, one pair per
operator, under fixed names. `ci init` is where the operator first learns
about them, so it checks and reports, never sets. The configured account
reaches gcloud through CLOUDSDK_CORE_ACCOUNT on every call the provider
and the CI module make, and a configured account without a login fails by
name before any cloud step runs.
"""

import os
import subprocess

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import util
from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.core import GappSDK

CI_REPO = "acme/ci"


@pytest.fixture
def init_env(monkeypatch, fake_gh):
    """init_ci against the in-memory GitHub: the topic is already set."""
    monkeypatch.setattr(ci_mod, "_find_ci_repo_by_topic", lambda: CI_REPO)
    return fake_gh


def test_init_reports_both_credentials_present(init_env):
    init_env.variables.add((CI_REPO, "HOSTING_GITHUB_APP_ID"))
    init_env.secrets.add((CI_REPO, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    res = ci_mod.init_ci(CI_REPO)
    assert res["topic_status"] == "already_set"
    assert res["hosting_app"]["app_id_present"] is True
    assert res["hosting_app"]["private_key_present"] is True
    assert res["next_step"] is None


def test_init_reports_missing_credentials_with_the_two_commands(init_env):
    res = ci_mod.init_ci(CI_REPO)
    app = res["hosting_app"]
    assert app["app_id_var"] == "HOSTING_GITHUB_APP_ID"
    assert app["app_key_secret"] == "HOSTING_GITHUB_APP_PRIVATE_KEY"
    assert app["app_id_present"] is False and app["private_key_present"] is False
    hint = res["next_step"]["hint"]
    assert f"gh variable set HOSTING_GITHUB_APP_ID --repo {CI_REPO} --body <app-id>" in hint
    assert f"gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo {CI_REPO} < key.pem" in hint
    assert "Only private solution repos need them" in hint


def test_init_never_sets_the_credentials_and_rerun_is_a_no_op(init_env):
    first = ci_mod.init_ci(CI_REPO)
    second = ci_mod.init_ci(CI_REPO)
    assert first == second
    assert init_env.variables == set() and init_env.secrets == set()
    assert not any("-X" in cmd for cmd, _ in init_env.calls)   # reads only


def test_init_reports_unchecked_when_the_actions_api_is_unreachable(init_env):
    init_env.actions_api = False
    res = ci_mod.init_ci(CI_REPO)
    assert res["hosting_app"]["checked"] is False
    assert "not checked from this machine" in res["hosting_app"]["note"]
    assert res["next_step"] is None


def test_init_local_only_skips_the_github_check(fake_gh):
    res = ci_mod.init_ci(CI_REPO, local_only=True)
    assert res["topic_status"] == "skipped"
    assert "hosting_app" not in res
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
