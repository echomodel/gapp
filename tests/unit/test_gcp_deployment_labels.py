"""GCPProvider's deployment-label calls and its hint on a CI runner."""

import json
import subprocess
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.base import CloudAccessError
from gapp.admin.sdk.cloud.gcp import GCPProvider

CI_HINT = (
    "This is a CI run as {who}, so signing in again does not apply: the error above names what it "
    "lacks. `gapp ci project setup {project}` grants the deploy identity its roles; run it as a "
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


def test_bucket_labels_are_updated_not_replaced(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(0)) as run:
        provider.set_bucket_labels("p", "gapp-a-p", {"gapp-version": "4", "gapp-deployment": "a"})
    assert run.call_args[0][0] == ["storage", "buckets", "update", "gs://gapp-a-p",
                                   "--update-labels=gapp-deployment=a,gapp-version=4"]


def test_a_refused_bucket_listing_raises(provider):
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: 403 denied")):
        with pytest.raises(CloudAccessError, match="Could not list the buckets of project 'p'.*403"):
            provider.list_bucket_labels("p")


def test_service_labels_come_from_the_service_and_a_missing_service_is_none(provider):
    out = json.dumps({"metadata": {"labels": {"gapp-version": "4"}}})
    with patch.object(provider, "_run_gcloud", return_value=_done(0, out)):
        assert provider.get_service_labels("p", "us-central1", "a") == {"gapp-version": "4"}
    # gcloud has worded a missing service several ways; every one is "no service", not an error.
    for missing in (
        "ERROR: (gcloud.run.services.describe) Cannot find service [a]",
        "ERROR: (gcloud.run.services.describe) Cannot find service [a]: it could not be found.",
        "ERROR: (gcloud.run.services.describe) Service [a] not found",
        "ERROR: (gcloud.run.services.describe) NOT_FOUND: Resource 'a' of kind 'SERVICE' does not exist",
    ):
        with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr=missing)):
            assert provider.get_service_labels("p", "us-central1", "a") is None, missing
    with patch.object(provider, "_run_gcloud", return_value=_done(1, stderr="ERROR: PERMISSION_DENIED")):
        with pytest.raises(CloudAccessError, match="PERMISSION_DENIED"):
            provider.get_service_labels("p", "us-central1", "a")


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
