"""GCPProvider's deployment-label calls and its hint on a CI runner."""

import json
import subprocess
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.base import CloudAccessError
from gapp.admin.sdk.cloud.gcp import GCPProvider

CI_HINT = (
    "This is a CI run as {who}, so signing in again does not apply: the error above names what it "
    "lacks. `gapp ci projects connect {project}` grants the deploy identity its roles; run it as a "
    "project Owner, then deploy again."
)


def _done(rc, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    return GCPProvider()


def test_bucket_labels_are_listed_by_bucket_name(provider):
    out = json.dumps([{"name": "gapp-a-p", "labels": {"gapp-deployment": "a"}}, {"name": "other"}])
    with patch.object(provider, "_run_gcloud", return_value=_done(0, out)) as run:
        assert provider.list_bucket_labels("p") == {"gapp-a-p": {"gapp-deployment": "a"}, "other": {}}
    assert run.call_args[0][0][:5] == ["storage", "buckets", "list", "--project", "p"]


def test_an_object_is_read_as_text(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(0, "gapp: 4.0.0\n")) as run:
        assert provider.read_object("gapp-p", "deployments/a/deployed.yaml") == "gapp: 4.0.0\n"
    assert run.call_args[0][0] == ["storage", "cat", "gs://gapp-p/deployments/a/deployed.yaml"]


@pytest.mark.parametrize("said", [
    "ERROR: (gcloud.storage.cat) The following URLs matched no objects or files:\ngs://gapp-p/x",
    "ERROR: (gcloud.storage.cat) gs://gapp-p not found: 404.",
])
def test_an_object_or_bucket_that_is_not_there_reads_as_none(provider, said):
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr=said)):
        assert provider.read_object("gapp-p", "x") is None


def test_an_object_that_cannot_be_read_is_not_taken_for_a_missing_one(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError, match="Could not read gs://gapp-p/x"):
            provider.read_object("gapp-p", "x")


def test_an_object_is_written_from_stdin_and_a_refusal_raises(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(0)) as run:
        provider.write_object("gapp-p", "x", "text")
    assert run.call_args[0][0] == ["storage", "cp", "-", "gs://gapp-p/x"] and run.call_args[1]["input"] == "text"
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError, match="Could not write gs://gapp-p/x"):
            provider.write_object("gapp-p", "x", "text")


def test_a_refused_bucket_listing_raises(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError, match="Could not list the buckets of project 'p'.*403"):
            provider.list_bucket_labels("p")


def test_on_a_ci_runner_a_refusal_never_suggests_signing_in(provider, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError) as exc:
            provider.list_bucket_labels("my-proj")
    text = str(exc.value)
    assert CI_HINT.format(who="the deploy identity", project="my-proj") in text
    assert "gcloud auth" not in text


def test_off_a_runner_the_sign_in_hint_is_unchanged(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError) as exc:
            provider.list_bucket_labels("my-proj")
    assert "Check `gcloud auth list`, and run `gcloud auth login`" in str(exc.value)
