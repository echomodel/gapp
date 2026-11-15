"""What gapp says when Google Cloud can't be asked or refuses: gcloud is
missing, nobody is signed in, the account gapp is set to has no login, the
login has expired, or the signed-in account can't reach the project. Every
message names the account gapp acted as, and a missing gcloud is a message,
never a traceback."""

import json
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk.cloud.base import GCLOUD_MISSING, CloudAccessError, GcloudNotInstalled
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.cloud.gcp import GCPProvider
from gapp.admin.sdk.util import run_gcloud

ME = "@".join(["me", "example.test"])
OTHER = "@".join(["other", "example.test"])


def _done(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


def _logins(*accounts, active=None):
    return json.dumps([{"account": a, "status": "ACTIVE" if a == active else ""} for a in accounts])


def _gcloud(logins="[]", token=None, refused=None):
    """A stand-in gcloud: `auth list` answers with `logins`, `print-access-token`
    with `token` (or a failure), and every other call with `refused`."""
    def run(args, **kwargs):
        if args[:2] == ["auth", "list"]:
            return _done(0, logins)
        if args[:2] == ["auth", "print-access-token"]:
            return _done(0, token) if token else _done(1, stderr="ERROR: Reauthentication failed.")
        return _done(1, stderr=refused or "ERROR: 403 denied")
    return run


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(GCPProvider, "_gcloud_installed", staticmethod(lambda: True))
    return GCPProvider()


# -- gcloud missing --


def test_a_missing_gcloud_is_one_message_never_a_traceback(monkeypatch):
    def gone(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "gcloud")
    monkeypatch.setattr(subprocess, "run", gone)
    with pytest.raises(GcloudNotInstalled) as exc:
        GCPProvider()._run_gcloud(["config", "get-value", "account"])
    assert str(exc.value) == GCLOUD_MISSING
    with pytest.raises(GcloudNotInstalled):
        run_gcloud(["auth", "list"])
    assert isinstance(exc.value, CloudAccessError)          # every caller that handles a refused call handles this


def test_the_message_for_a_missing_gcloud_says_what_to_install_and_the_way_around_it(monkeypatch):
    monkeypatch.setattr(GCPProvider, "_gcloud_installed", staticmethod(lambda: False))
    assert GCPProvider().identity_hint("my-proj") == GCLOUD_MISSING
    assert "isn't installed" in GCLOUD_MISSING and "`gapp ci deploy`" in GCLOUD_MISSING


def test_a_command_run_without_gcloud_prints_the_message(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PATH", str(tmp_path))                 # nothing on it, gcloud included
    result = CliRunner().invoke(main, ["deployments", "scan"])
    assert result.exit_code == 1 and "Traceback" not in result.output
    assert GCLOUD_MISSING in result.output


# -- who gapp acted as --


def test_nobody_signed_in(provider):
    with patch.object(provider, "_run_gcloud", side_effect=_gcloud()):
        with pytest.raises(CloudAccessError) as exc:
            provider.list_bucket_labels("my-proj")
    assert ("No Google Cloud account is signed in on this machine. Sign in with `gcloud auth login`, "
            "then run this again.") in str(exc.value)


def test_signed_in_as_an_account_that_cannot_reach_the_project_names_it(provider):
    with patch.object(provider, "_run_gcloud", side_effect=_gcloud(_logins(ME, active=ME))):
        with pytest.raises(CloudAccessError) as exc:
            provider.list_bucket_labels("my-proj")
    text = str(exc.value)
    assert "403 denied" in text                               # the cloud's own answer stays
    assert (f"Signed in to Google Cloud as {ME}, which can't reach project 'my-proj'. Sign in as an account "
            f"that can (`gcloud auth login`), or point gapp at one with `gapp config account <email>`.") in text


def test_the_account_gapp_is_set_to_wins_over_gclouds_and_its_missing_login_is_named(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(GCPProvider, "_gcloud_installed", staticmethod(lambda: True))
    provider = GCPProvider(account=OTHER)
    with patch.object(provider, "_run_gcloud", side_effect=_gcloud(_logins(ME, active=ME))):
        hint = provider.identity_hint("my-proj")
    assert hint == (f"gapp is set to use {OTHER} (`gapp config account`), and this machine has no Google Cloud "
                    f"login for it. Sign in with `gcloud auth login {OTHER}`, or point gapp at another account "
                    f"with `gapp config account <email>`, then run this again.")


def test_a_login_that_can_no_longer_get_a_token_is_called_expired(provider):
    with patch.object(provider, "_run_gcloud", side_effect=_gcloud(_logins(ME, active=ME))):
        with pytest.raises(CloudAccessError) as exc:
            provider.get_auth_token()
    assert (f"The Google Cloud login for {ME} can't be used: it has expired or was revoked. "
            f"Sign in again with `gcloud auth login {ME}`, then run this again.") in str(exc.value)


def test_who_is_signed_in_is_asked_once_and_only_after_a_failure(provider):
    calls = []
    run = _gcloud(_logins(ME, active=ME))

    def counted(args, **kwargs):
        calls.append(args[:2])
        return run(args, **kwargs)
    with patch.object(provider, "_run_gcloud", side_effect=counted):
        for _ in range(3):
            provider.identity_hint("my-proj")
    assert calls.count(["auth", "list"]) == 1


def test_on_a_runner_the_hint_is_about_the_deploy_identity(provider, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with patch.object(provider, "_run_gcloud", side_effect=_gcloud(_logins(ME, active=ME))):
        hint = provider.identity_hint("my-proj")
    assert hint.startswith(f"This is a CI run as {ME}") and "gcloud auth login" not in hint


# -- a scan that finds nothing --


def test_a_scan_that_finds_no_project_says_whose_login_searched(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cloud = DummyCloudProvider()
    cloud.account = ME
    monkeypatch.setattr("gapp.admin.sdk.cloud.get_provider", lambda: cloud)
    result = CliRunner().invoke(main, ["deployments", "scan"])
    assert result.output == f"No project that {ME} can see is set up for gapp.\n"


def test_a_list_whose_records_cannot_be_read_explains_why_once(monkeypatch, tmp_path):
    from gapp.admin.sdk import definitions
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cloud = DummyCloudProvider()
    cloud.account = ME
    monkeypatch.setattr("gapp.admin.sdk.cloud.get_provider", lambda: cloud)

    def refused(*args, **kwargs):
        raise CloudAccessError("Could not read the deploy record (gcloud exit 1): ERROR: 403 denied")
    monkeypatch.setattr(cloud, "read_object", refused)
    for name in ("app-a", "app-b"):
        definitions.add(name, project="my-proj", repo=f"acme/{name}")
    out = CliRunner().invoke(main, ["deployments", "list"]).output.splitlines()
    hint = f"Signed in to Google Cloud as {ME}, which Google Cloud refused here."
    assert sum(line.strip() == "Could not read the deploy record (gcloud exit 1): ERROR: 403 denied" for line in out) == 2
    assert out[-1].startswith(hint) and sum(hint in line for line in out) == 1
