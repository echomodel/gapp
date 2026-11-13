"""User-facing wording: the `gapp list` summary counts, and the message for
commands that need an app's folder when run outside one."""

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import secrets as sec_mod
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK

NOT_IN_APP = "Not inside an app's folder. Run this in the app's repo, or run gapp init there first."
NOT_IN_APP_SECRETS = ("Not inside an app's folder. Run this in the app's repo, or name a deployment of your fleet "
                      "with --deployment <name>.")


def _summary(monkeypatch, apps, projects):
    monkeypatch.setattr(GappSDK, "list_apps", lambda self, **k: {
        "apps": [], "warnings": [], "messages": [],
        "metadata": {"apps": {"count": apps}, "projects": {"count": projects}, "contract_major": 4}})
    return [l for l in CliRunner().invoke(main, ["list"]).output.splitlines() if l.startswith("Summary:")][0]


def test_list_summary_uses_singular_and_plural(monkeypatch):
    assert _summary(monkeypatch, 1, 1) == "Summary: 1 app across 1 project (this build: v-4)."
    assert _summary(monkeypatch, 2, 3) == "Summary: 2 apps across 3 projects (this build: v-4)."
    assert _summary(monkeypatch, 0, 0) == "Summary: 0 apps across 0 projects (this build: v-4)."


def test_deploy_outside_an_app_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        GappSDK(provider=DummyCloudProvider()).deploy()
    assert str(exc.value) == NOT_IN_APP


@pytest.mark.parametrize("call", [
    lambda: sec_mod.list_secrets(),
    lambda: sec_mod.get_secret("k"),
    lambda: sec_mod.set_secret("k", "v"),
])
def test_secrets_outside_an_app_folder(call, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sec_mod.GappSDK, "resolve_solution_with_project", lambda self, *a, **k: None)
    with pytest.raises(RuntimeError) as exc:
        call()
    assert str(exc.value) == NOT_IN_APP_SECRETS


def test_setup_outside_an_app_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        GappSDK(provider=DummyCloudProvider()).setup("p1")
    assert str(exc.value) == NOT_IN_APP


def test_deploy_dry_run_labels_the_deployment(monkeypatch):
    monkeypatch.setattr(GappSDK, "deploy", lambda self, **k: {
        "name": "my-app", "labels": "gapp-deployment=my-app, gapp-version=4", "project_id": "p1",
        "bucket": "gapp-my-app-p1", "status": "ready", "services": [], "dry_run": True})
    out = CliRunner().invoke(main, ["deploy", "--dry-run"]).output.splitlines()
    assert "    Deployment:  my-app" in out and not [l for l in out if "Solution:" in l]
