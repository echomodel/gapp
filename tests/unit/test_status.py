"""What `gapp status` says about a custom domain, and what it says when no
Google Cloud identity on the machine can read the project."""

import pytest

from gapp import __version__
from gapp.admin.sdk import definitions, settings
from gapp.admin.sdk.cloud.base import CloudAccessError
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK

PROJECT = "my-project"
BUCKET = "gapp-my-project"
STATE = "deployments/my-app/terraform/state"


def _conditions(ready, routable="True", cert="True", cert_message=None):
    conds = [
        {"type": "Ready", "status": ready},
        {"type": "DomainRoutable", "status": routable},
        {"type": "CertificateProvisioned", "status": cert},
    ]
    if cert_message:
        conds[2]["message"] = cert_message
        conds[2]["reason"] = "CertificatePending"
    return conds


def test_domain_status_active_when_ready():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    assert _domain_status_from_conditions(_conditions("True")) == ("active", None)


def test_domain_status_pending_dns_when_records_missing():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    msg = "Waiting for certificate provisioning. You must configure your DNS records for certificate issuance to begin."
    status, detail = _domain_status_from_conditions(_conditions("False", cert="False", cert_message=msg))
    assert status == "pending_dns"
    assert detail == msg


def test_domain_status_pending_dns_when_not_routable():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    status, _ = _domain_status_from_conditions(_conditions("False", routable="False", cert="False"))
    assert status == "pending_dns"


def test_domain_status_pending_cert_when_dns_right_and_cert_pending():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    status, detail = _domain_status_from_conditions(
        _conditions("False", cert="False", cert_message="Certificate provisioning in progress."))
    assert status == "pending_cert"
    assert "in progress" in detail


def test_domain_status_error_when_not_ready_for_another_reason():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    conds = _conditions("False")
    conds[0]["message"] = "Mapping failed: domain is mapped to another project."
    status, detail = _domain_status_from_conditions(conds)
    assert status == "error"
    assert "another project" in detail


def test_domain_status_without_conditions_is_pending_dns():
    from gapp.admin.sdk.core import _domain_status_from_conditions
    assert _domain_status_from_conditions([])[0] == "pending_dns"


# -- a deployment's domain, from the live mapping --


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """A local fleet with my-app, deployed with the domain mcp.example.com."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(tmp_path)
    definitions.add("my-app", project=PROJECT, repo="acme/my-app")
    provider = DummyCloudProvider()
    provider.buckets[BUCKET] = {"project": PROJECT, "labels": {}}
    provider.objects[(BUCKET, "deployments/my-app/deployed.yaml")] = f"gapp: {__version__}\n"
    provider.tf_outputs[(BUCKET, STATE)] = {"service_url": "https://my-app.a.run.app",
                                            "custom_domain": "mcp.example.com"}
    return provider


def _status(cloud):
    return GappSDK(provider=cloud).status_definition("my-app")


def test_status_gives_the_record_to_add_while_the_domain_waits_on_dns(cloud):
    cloud.domain_mappings[(PROJECT, "mcp.example.com")] = {"status": {
        "conditions": _conditions("False", cert="False",
                                  cert_message="You must configure your DNS records for certificate issuance to begin."),
        "resourceRecords": [{"name": "mcp", "type": "CNAME", "rrdata": "ghs.googlehosted.com."}],
    }}
    domain = _status(cloud)["domain"]
    assert (domain["name"], domain["status"], domain["cname_target"]) == (
        "mcp.example.com", "pending_dns", "ghs.googlehosted.com")
    assert "DNS records" in domain["detail"]


def test_status_says_when_the_domain_is_active(cloud):
    cloud.domain_mappings[(PROJECT, "mcp.example.com")] = {"status": {"conditions": _conditions("True")}}
    assert _status(cloud)["domain"]["status"] == "active"


def test_a_domain_in_the_state_with_no_mapping_is_an_error_that_names_the_fix(cloud):
    domain = _status(cloud)["domain"]
    assert domain["status"] == "error" and "no mapping" in domain["detail"]


def test_a_deployment_with_no_domain_reports_none(cloud):
    cloud.tf_outputs[(BUCKET, STATE)]["custom_domain"] = ""
    assert _status(cloud)["domain"] is None


# -- a login that does not work is reported as that, never as "not deployed" --


def _no_identity(cloud, monkeypatch, said):
    def refuse(*args, **kwargs):
        raise CloudAccessError(f"Could not read the bucket (gcloud exit 1): {said}")
    monkeypatch.setattr(cloud, "list_bucket_labels", refuse)


def test_status_without_a_usable_login_names_the_sign_in(cloud, monkeypatch):
    _no_identity(cloud, monkeypatch, "UNAUTHENTICATED")
    result = _status(cloud)
    assert result["deployed"] is False
    assert "No Google Cloud identity this machine can use" in result["next_step"]
    assert "gcloud auth login" in result["next_step"] and "UNAUTHENTICATED" not in result["next_step"]
    assert "not deployed yet" not in result["next_step"]


def test_status_on_a_runner_never_suggests_signing_in(cloud, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    _no_identity(cloud, monkeypatch, "PERMISSION_DENIED")
    result = _status(cloud)
    assert result["next_step"].startswith("This is a CI run as ") and "gcloud auth login" not in result["next_step"]
