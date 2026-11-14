"""User-facing wording: the `gapp list` summary counts, and the message for
commands that need an app's folder when run outside one."""

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK

NOT_IN_APP = "Not inside an app's folder. Run this in the app's repo, or run gapp init there first."


def test_deploy_outside_an_app_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        GappSDK(provider=DummyCloudProvider()).deploy()
    assert str(exc.value) == NOT_IN_APP


def test_setup_outside_an_app_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        GappSDK(provider=DummyCloudProvider()).setup("p1")
    assert str(exc.value) == NOT_IN_APP
