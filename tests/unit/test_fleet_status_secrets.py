"""`gapp status` and `gapp secrets` on a fleet: both take the deployment,
its id, its project and the secrets it reads from its definition."""

import json

import pytest
from click.testing import CliRunner

from gapp import __version__
from gapp.admin.cli.main import main
from gapp.admin.sdk import definitions, entryfile, secrets, settings
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.definitions import Refused

PROJECT = "my-project"
BUCKET = "gapp-my-app-my-project"


@pytest.fixture(autouse=True)
def fleet(monkeypatch, tmp_path):
    """A local fleet with my-app, standing in a folder that is not a clone."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    definitions.add("my-app", project=PROJECT, repo="acme/my-app")
    definitions.env_set("API_KEY", secret="", deployment="my-app")
    definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    definitions.env_set("MAX_USERS", "25", deployment="my-app")


@pytest.fixture
def gcp(monkeypatch):
    """Secret Manager as two stand-ins: the secrets labelled for the deployment, and what a describe finds."""
    state = {"labelled": [], "unlabelled": {}}
    monkeypatch.setattr(secrets, "list_secrets_by_label", lambda project, id: [{"id": s} for s in state["labelled"]])
    monkeypatch.setattr(secrets, "_classify_unlabeled",
                        lambda project, secret_id: state["unlabelled"].get(secret_id, {"kind": "missing"}))
    return state


def by_name(result):
    return {s["name"]: s for s in result["secrets"]}


# --- secrets ---


def test_list_takes_the_secrets_from_the_definition_and_names_them_by_the_id(gcp):
    gcp["labelled"] = ["my-app-api-key"]
    result = secrets.list_secrets("my-app")
    assert (result["deployment"], result["project_id"]) == ("my-app", PROJECT)
    found = by_name(result)
    assert set(found) == {"api-key", "app-key"}
    assert found["api-key"] == {"name": "api-key", "env_var": "API_KEY", "secret_id": "my-app-api-key",
                                "generate": False, "status": "ready"}
    assert found["app-key"]["status"] == "missing-generate"


def test_a_renamed_deployment_keeps_its_secrets_under_its_id(gcp):
    definitions.rename("my-app", "prod")
    assert by_name(secrets.list_secrets("prod"))["api-key"]["secret_id"] == "my-app-api-key"


def test_a_secret_no_variable_reads_is_an_orphan_with_the_command_that_declares_it(gcp):
    gcp["labelled"] = ["my-app-api-key", "my-app-old-token"]
    result = secrets.list_secrets("my-app")
    assert result["orphans"] == ["my-app-old-token"]
    commands = " ".join(opt["command"] for hint in result["hints"] for opt in hint["options"])
    assert "gapp env set" in commands and "--deployment my-app" in commands
    assert "gapp manifest" not in commands and "gapp ci" not in commands


def test_a_secret_owned_by_another_deployment_is_a_conflict(gcp):
    gcp["unlabelled"]["my-app-api-key"] = {"kind": "conflict", "owner": "other-app"}
    result = secrets.list_secrets("my-app")
    assert by_name(result)["api-key"]["status"] == "conflict"
    assert any("other-app" in hint["message"] for hint in result["hints"])


def test_outside_a_clone_the_deployment_must_be_named(gcp):
    with pytest.raises(Refused) as caught:
        secrets.list_secrets()
    assert caught.value.message.startswith("Say which deployment: gapp secrets list --deployment <name>.")


def test_a_secret_the_definition_does_not_have_is_refused_with_the_ones_it_has(gcp):
    with pytest.raises(Refused) as caught:
        secrets._find_secret("nope", "my-app")
    message = caught.value.message
    assert "'my-app' has no secret 'nope'. Its secrets: api-key, app-key." in message
    assert "gapp env set <NAME> --secret nope --deployment my-app" in message


def test_set_stores_the_value_under_the_deployments_id(monkeypatch):
    stored = {}
    monkeypatch.setattr(secrets, "_ensure_secret", lambda project, secret_id, id: "created")
    monkeypatch.setattr(secrets, "_add_secret_version",
                        lambda project, secret_id, value: stored.update({(project, secret_id): value}))
    result = secrets.set_secret("api-key", "s3cr3t", deployment="my-app")
    assert stored == {(PROJECT, "my-app-api-key"): "s3cr3t"} and result["secret_id"] == "my-app-api-key"


def test_the_set_command_takes_no_value_argument_and_reads_stdin(monkeypatch):
    stored = {}
    monkeypatch.setattr(secrets, "_ensure_secret", lambda *a: "exists")
    monkeypatch.setattr(secrets, "_add_secret_version", lambda project, secret_id, value: stored.update({secret_id: value}))
    result = CliRunner().invoke(main, ["secrets", "set", "api-key", "s3cr3t", "-d", "my-app"])
    assert result.exit_code != 0 and stored == {}
    result = CliRunner().invoke(main, ["secrets", "set", "api-key", "--from-stdin", "-d", "my-app"], input="s3cr3t\n")
    assert result.exit_code == 0 and stored == {"my-app-api-key": "s3cr3t"}


def test_the_set_command_refuses_an_unknown_secret_before_asking_for_a_value():
    result = CliRunner().invoke(main, ["secrets", "set", "nope", "-d", "my-app"], input="never-read\n")
    assert result.exit_code == 2 and "has no secret 'nope'" in result.output and "Value" not in result.output


def test_the_list_command_prints_the_table_and_json(gcp):
    gcp["labelled"] = ["my-app-api-key"]
    out = CliRunner().invoke(main, ["secrets", "list", "-d", "my-app"]).output
    assert "Deployment: my-app\nProject:    my-project\n" in out
    assert "api-key" in out and "API_KEY" in out and "ready" in out
    listed = json.loads(CliRunner().invoke(main, ["secrets", "list", "-d", "my-app", "--json"]).output)
    assert listed["deployment"] == "my-app"


def test_there_is_no_tool_that_takes_a_secrets_value():
    from gapp.admin.mcp import server
    assert not hasattr(server, "gapp_secrets_set") and not hasattr(server, "gapp_secret_set")


# --- status ---


@pytest.fixture
def cloud():
    return DummyCloudProvider()


def deployed(cloud, version=__version__, url="https://my-app.a.run.app", domain=""):
    cloud.buckets[BUCKET] = {"project": PROJECT, "labels": {"gapp-version": settings.version_label(version)}}
    cloud.tf_outputs[(BUCKET, "terraform/state")] = {"service_url": url, "custom_domain": domain}


def test_a_deployment_that_was_never_deployed_names_the_deploy_command(cloud):
    result = GappSDK(provider=cloud).status_definition("my-app")
    assert result["deployed"] is False and result["url"] is None
    assert result["next_step"] == (
        "It is not deployed yet. From a clone of its repo: gapp deploy --deployment my-app")


def test_a_live_deployment_reports_its_url_health_and_who_deployed_it(cloud):
    deployed(cloud)
    result = GappSDK(provider=cloud).status_definition("my-app")
    assert (result["deployed"], result["url"], result["last_deployed_by"]) == (
        True, "https://my-app.a.run.app", __version__)
    assert result["upgrade_pending"] is False and result["next_step"] is None


def test_status_says_when_the_next_deploy_would_apply_an_upgrade(cloud, monkeypatch):
    deployed(cloud)
    for module in (entryfile, definitions):
        monkeypatch.setattr(module, "__version__", "4.0.1")
    definitions.upgrade("my-app")
    result = GappSDK(provider=cloud).status_definition("my-app")
    assert result["upgrade_pending"] is True
    assert result["next_step"].endswith("gapp deploy --deployment my-app --upgrade")


def test_status_looks_under_the_id_after_a_rename(cloud):
    deployed(cloud)
    definitions.rename("my-app", "prod")
    assert GappSDK(provider=cloud).status_definition("prod")["deployed"] is True


def test_the_status_command_prints_it_and_refuses_an_unknown_name(cloud, monkeypatch):
    monkeypatch.setattr("gapp.admin.cli.main.GappSDK", lambda: GappSDK(provider=cloud))
    deployed(cloud)
    out = CliRunner().invoke(main, ["status", "-d", "my-app"]).output
    assert out.startswith("my-app, on my-project\n  URL      https://my-app.a.run.app\n")
    result = CliRunner().invoke(main, ["status", "-d", "nope"])
    assert result.exit_code == 2 and "'nope' is not a deployment in your fleet" in result.output
