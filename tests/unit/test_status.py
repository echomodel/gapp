"""Tests for gapp.sdk.core — infrastructure health check."""

import pytest
from pathlib import Path
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider


@pytest.fixture
def sdk():
    """Return a fresh GappSDK instance with a dummy provider."""
    return GappSDK(provider=DummyCloudProvider())


def test_status_uninitialized(tmp_path, monkeypatch, sdk):
    """Verify status returns init step if no gapp.yaml found."""
    monkeypatch.chdir(tmp_path)
    res = sdk.status()
    assert res.initialized is False
    assert res.next_step.action == "init"


def test_status_pending_setup(tmp_path, monkeypatch, sdk):
    """Verify status returns setup step if no project attached."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)
    
    res = sdk.status()
    assert res.initialized is True
    assert res.next_step.action == "setup"


def test_status_ready(tmp_path, monkeypatch, sdk):
    """Verify status returns ready if services are found."""
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)

    # Register project (env-blind label, optional gapp-env)
    sdk.provider.project_labels["proj-123"] = {
        "gapp-env": "prod",
        "gapp__my-app": "v-3",
    }

    # Bucket name: gapp-{solution}-{project_id} — owner-blind, env-blind
    sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state")] = {"service_url": "https://my-app.run.app"}

    res = sdk.status()
    assert res.initialized is True
    assert res.deployment.project == "proj-123"
    assert len(res.deployment.services) == 1
    assert res.deployment.services[0].name == "my-app"


def test_status_with_explicit_solution_skips_local_discovery(tmp_path, monkeypatch, sdk):
    """status(name=...) skips git-root/gapp.yaml discovery entirely.

    Caller is in a directory with no gapp.yaml; passing a solution name
    must still resolve cleanly via cloud labels and TF outputs.
    """
    monkeypatch.chdir(tmp_path)  # no gapp.yaml here
    sdk.provider.project_labels["proj-123"] = {"gapp__remote-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-remote-app-proj-123", "terraform/state")] = {
        "service_url": "https://remote-app.run.app",
    }

    res = sdk.status(name="remote-app")
    assert res.initialized is True
    assert res.name == "remote-app"
    assert res.deployment.project == "proj-123"
    assert len(res.deployment.services) == 1
    assert res.deployment.services[0].url == "https://remote-app.run.app"


def test_status_tolerates_invalid_local_manifest(tmp_path, monkeypatch, sdk):
    """status doesn't fail validation on a stale or unsupported gapp.yaml.

    A repo with a manifest that wouldn't pass strict schema validation
    (e.g., legacy fields, v2 secret shape) must still be probable for
    deployment status. Status is read-only; manifest enforcement belongs
    in setup/deploy, not here.
    """
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    # Bogus field — would fail strict schema.
    (repo / "gapp.yaml").write_text(
        "name: stale-app\n"
        "service:\n"
        "  bogus_field: 1\n"
    )
    monkeypatch.chdir(repo)

    sdk.provider.project_labels["proj-123"] = {"gapp__stale-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-stale-app-proj-123", "terraform/state")] = {
        "service_url": "https://stale-app.run.app",
    }

    res = sdk.status()  # no exception
    assert res.initialized is True
    assert res.name == "stale-app"
    assert res.deployment.project == "proj-123"


# -- custom domain status --


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


def _deployed_with_domain(tmp_path, monkeypatch, sdk, domain="mcp.example.com"):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text(f"name: my-app\ndomain: {domain}\n")
    monkeypatch.chdir(repo)
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state")] = {
        "service_url": "https://my-app.run.app", "custom_domain": domain,
    }


def test_status_fills_domain_from_the_live_mapping(tmp_path, monkeypatch, sdk):
    _deployed_with_domain(tmp_path, monkeypatch, sdk)
    sdk.provider.domain_mappings[("proj-123", "mcp.example.com")] = {
        "status": {
            "conditions": _conditions("False", cert="False",
                                      cert_message="You must configure your DNS records for certificate issuance to begin."),
            "resourceRecords": [{"name": "mcp", "type": "CNAME", "rrdata": "ghs.googlehosted.com."}],
        },
    }

    res = sdk.status()

    assert res.domain is not None
    assert res.domain.name == "mcp.example.com"
    assert res.domain.status == "pending_dns"
    assert res.domain.cname_target == "ghs.googlehosted.com"
    assert "DNS records" in res.domain.detail


def test_status_domain_active(tmp_path, monkeypatch, sdk):
    _deployed_with_domain(tmp_path, monkeypatch, sdk)
    sdk.provider.domain_mappings[("proj-123", "mcp.example.com")] = {
        "status": {"conditions": _conditions("True")},
    }
    assert sdk.status().domain.status == "active"


def test_status_domain_error_when_mapping_is_gone(tmp_path, monkeypatch, sdk):
    _deployed_with_domain(tmp_path, monkeypatch, sdk)
    res = sdk.status()
    assert res.domain.status == "error"
    assert "no mapping" in res.domain.detail


def test_status_without_domain_leaves_domain_unset(tmp_path, monkeypatch, sdk):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app\n")
    monkeypatch.chdir(repo)
    sdk.provider.project_labels["proj-123"] = {"gapp__my-app": "v-3"}
    sdk.provider.tf_outputs[("gapp-my-app-proj-123", "terraform/state")] = {
        "service_url": "https://my-app.run.app", "custom_domain": "",
    }
    assert sdk.status().domain is None


# -- auth failures are reported as auth failures, not as "not deployed" --


def test_status_without_a_usable_identity_is_an_outcome_not_a_crash(tmp_path, monkeypatch, sdk):
    """No GCP identity: status says so and names the login command. It is
    never reported as "not deployed" (next_step=setup), and it never raises."""
    from gapp.admin.sdk.cloud.base import CloudAccessError

    repo = tmp_path / "app"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "gapp.yaml").write_text("name: my-app")
    monkeypatch.chdir(repo)

    def failing_list(*a, **kw):
        raise CloudAccessError("Could not list projects (gcloud exit 1): UNAUTHENTICATED")

    sdk.provider.list_projects = failing_list
    res = sdk.status()
    assert res.initialized is True and res.name == "my-app"
    assert res.deployment.project is None and res.deployment.pending is True
    assert res.next_step.action == "sign in"
    assert "No Google Cloud identity this machine can use" in res.next_step.hint
    assert "gcloud auth login test-user@example.com" in res.next_step.hint
    assert "UNAUTHENTICATED" not in res.next_step.hint


def test_status_uninitialized_carries_a_deployment_and_a_hint(tmp_path, monkeypatch, sdk):
    monkeypatch.chdir(tmp_path)
    res = sdk.status()
    assert res.initialized is False and res.deployment is not None
    assert "gapp init" in res.next_step.hint


def test_resolve_with_explicit_project_surfaces_label_read_errors(sdk):
    from gapp.admin.sdk.cloud.base import CloudAccessError

    def failing_labels(project_id):
        raise CloudAccessError(f"Not authorized to read labels of project '{project_id}' (HTTP 401)")

    sdk.provider.get_project_labels = failing_labels
    with pytest.raises(CloudAccessError, match="HTTP 401"):
        sdk.resolve_project_for_solution("my-app", project="proj-123")
