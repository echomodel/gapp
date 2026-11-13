"""`gapp secrets ... --deployment <name>`: the deployment's id, project and
declared secrets come from the fleet, so a deployment configured only in the
fleet, or a copy, can have its secrets set from any folder. GitHub (the
fleet and the app repo) and Secret Manager are both in-memory here."""

import subprocess

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import secrets as sec_mod

FLEET = "acme/fleet"
PROJECT = "my-proj"


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeSecretManager:
    """The `gcloud secrets` calls gapp makes."""

    def __init__(self):
        self.secrets = {}     # (project, id) -> {"owner": label value, "versions": [values]}
        self.calls = []

    def run(self, cmd, **kwargs):
        args = cmd[1:]
        self.calls.append(args)
        assert args[0] == "secrets", f"only `gcloud secrets` is expected, got {cmd}"
        project = args[args.index("--project") + 1]
        if args[1] == "describe":
            secret = self.secrets.get((project, args[2]))
            return _Result(0, f"{secret['owner']}\n") if secret else _Result(1, "", "NOT_FOUND")
        if args[1] == "create":
            owner = args[args.index("--labels") + 1].split("=", 1)[1]
            self.secrets[(project, args[2])] = {"owner": owner, "versions": []}
            return _Result(0)
        if args[1:3] == ["versions", "add"]:
            self.secrets[(project, args[3])]["versions"].append(kwargs["input"])
            return _Result(0)
        if args[1:3] == ["versions", "access"]:
            secret = self.secrets.get((project, args[args.index("--secret") + 1]))
            return _Result(0, secret["versions"][-1] + "\n") if secret and secret["versions"] else _Result(1)
        if args[1] == "list":
            owner = args[args.index("--filter") + 1].split("=", 1)[1]
            return _Result(0, "".join(f"{sid}\n" for (p, sid), s in sorted(self.secrets.items())
                                      if p == project and s["owner"] == owner))
        raise AssertionError(f"unexpected gcloud command {cmd}")


@pytest.fixture
def machine(monkeypatch, fleet_repo, fake_gh, tmp_path):
    """A machine with gh and gcloud, its fleet set, standing in an empty folder
    that is not an app's checkout."""
    sm = FakeSecretManager()
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: sm.run(cmd, **kw) if cmd[0] == "gcloud" else fake_gh.run(cmd, **kw))
    monkeypatch.chdir(tmp_path)
    fake_gh.private["me/my-app"] = False
    fake_gh.private["them/alerts"] = False
    fake_gh.secret_manager = sm
    return fake_gh


def _secret(machine, secret_id):
    return machine.secret_manager.secrets.get((PROJECT, secret_id))


def test_a_deployment_configured_only_in_the_fleet_gets_its_secret_set_from_any_folder(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT, start="alerts serve",
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}])
    res = sec_mod.set_secret("channels", "the-value", deployment="alerts")
    assert res == {"name": "channels", "secret_id": "alerts-channels", "project_id": PROJECT,
                   "secret_status": "created"}
    assert _secret(machine, "alerts-channels") == {"owner": "alerts", "versions": ["the-value"]}
    assert not machine.puts and not machine.dispatched       # the fleet is only read


def test_a_secret_the_app_repo_declares_is_found_through_the_merge(machine):
    machine.deploy(FLEET, "my-app", "me/my-app", PROJECT, domain="app.example.com")
    machine.files[("me/my-app", "gapp.yaml")] = "env:\n  - name: API_KEY\n    secret:\n      name: api-key\n"
    sec_mod.set_secret("api-key", "v1", deployment="my-app")
    assert _secret(machine, "my-app-api-key")["versions"] == ["v1"]


def test_a_copy_gets_its_own_secret_not_the_originals(machine):
    machine.files[("me/my-app", "gapp.yaml")] = "env:\n  - name: API_KEY\n    secret:\n      name: api-key\n"
    machine.deploy(FLEET, "my-app", "me/my-app", PROJECT)
    machine.deploy(FLEET, "my-app-staging", "me/my-app", PROJECT)
    sec_mod.set_secret("api-key", "staging-value", deployment="my-app-staging")
    assert _secret(machine, "my-app-staging-api-key")["versions"] == ["staging-value"]
    assert _secret(machine, "my-app-api-key") is None


def test_a_renamed_deployment_keeps_the_secret_named_by_its_id(machine):
    machine.deploy(FLEET, "shop", "me/my-app", PROJECT, id="my-app",
                   env=[{"name": "API_KEY", "secret": {"name": "api-key"}}])
    res = sec_mod.set_secret("api-key", "v", deployment="shop")
    assert res["secret_id"] == "my-app-api-key" and _secret(machine, "my-app-api-key")["owner"] == "my-app"


def test_an_unknown_deployment_names_the_list_command(machine):
    with pytest.raises(RuntimeError) as exc:
        sec_mod.set_secret("api-key", "v", deployment="nope")
    assert str(exc.value) == "'nope' is not a deployment in acme/fleet. See the deployments: gapp ci list"
    assert machine.secret_manager.calls == []


def test_a_secret_the_deployment_does_not_declare_is_refused_with_the_declared_ones(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT,
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}, {"name": "A", "value": "1"}])
    with pytest.raises(RuntimeError) as exc:
        sec_mod.set_secret("chanels", "v", deployment="alerts")
    assert str(exc.value) == "No secret 'chanels' is declared for alerts. Declared: channels."
    assert machine.secret_manager.calls == []


def test_an_app_repo_this_login_cannot_read_still_serves_what_the_fleet_declares(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT,
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}])
    machine.unreadable.add("them/alerts")
    sec_mod.set_secret("channels", "v", deployment="alerts")
    assert _secret(machine, "alerts-channels")["versions"] == ["v"]
    with pytest.raises(RuntimeError) as exc:
        sec_mod.set_secret("other", "v", deployment="alerts")
    assert "them/alerts's gapp.yaml could not be read from here, so only the settings the fleet holds" in str(exc.value)


def test_list_reports_each_declared_secret_for_the_deployment(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT, env=[
        {"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}},
        {"name": "SIGNING_KEY", "secret": {"name": "signing-key", "generate": True}}])
    res = sec_mod.list_secrets(deployment="alerts")
    assert res["deployment"] == "alerts" and res["solution"] == "alerts" and res["project_id"] == PROJECT
    assert [(s["name"], s["secret_id"], s["status"]) for s in res["secrets"]] == [
        ("channels", "alerts-channels", "missing"), ("signing-key", "alerts-signing-key", "missing-generate")]
    assert res["notes"] == []
    sec_mod.set_secret("channels", "v", deployment="alerts")
    assert sec_mod.list_secrets(deployment="alerts")["secrets"][0]["status"] == "ready"


def test_list_says_when_the_app_repo_was_not_read(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT)
    machine.unreadable.add("them/alerts")
    notes = sec_mod.list_secrets(deployment="alerts")["notes"]
    assert len(notes) == 1 and notes[0].startswith("them/alerts's gapp.yaml could not be read from here")


def test_get_reads_the_deployments_secret(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT,
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}])
    sec_mod.set_secret("channels", "abc", deployment="alerts")
    got = sec_mod.get_secret("channels", deployment="alerts")
    assert got["secret_id"] == "alerts-channels" and got["length"] == 3 and "value" not in got
    assert sec_mod.get_secret("channels", plaintext=True, deployment="alerts")["value"] == "abc"


def test_a_name_and_a_deployment_together_are_refused(machine):
    with pytest.raises(RuntimeError, match="Give a name or --deployment, not both."):
        sec_mod.list_secrets(name="x", deployment="y")


def test_without_a_fleet_repo_the_message_says_how_to_set_one(machine):
    from gapp.admin.sdk import ci as ci_mod
    from gapp.admin.sdk import config as config_mod
    config_mod.save_config({})
    with pytest.raises(RuntimeError) as exc:
        sec_mod.list_secrets(deployment="alerts")
    assert str(exc.value) == ci_mod.NO_FLEET_REPO_MESSAGE


def test_cli_set_reads_the_value_from_stdin_and_list_names_the_deployment(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT,
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}])
    res = CliRunner().invoke(main, ["secrets", "set", "channels", "--deployment", "alerts", "--from-stdin"],
                             input="piped-value\n")
    assert res.exit_code == 0, res.output
    assert res.output.strip() == "set: alerts-channels"
    assert _secret(machine, "alerts-channels")["versions"] == ["piped-value"]
    out = CliRunner().invoke(main, ["secrets", "list", "--deployment", "alerts"]).output
    assert "App:     alerts" in out and f"Project: {PROJECT}" in out and "ready" in out


def test_cli_set_checks_the_secret_before_asking_for_a_value(machine):
    machine.deploy(FLEET, "alerts", "them/alerts", PROJECT,
                   env=[{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}])
    res = CliRunner().invoke(main, ["secrets", "set", "nope", "--deployment", "alerts"])
    assert res.exit_code == 1
    assert "Value" not in res.output and "No secret 'nope' is declared for alerts." in res.output


def test_a_missing_secret_on_a_runner_names_the_deployment_form(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(sec_mod, "list_secrets_by_label", lambda p, s: [])
    monkeypatch.setattr(sec_mod, "_classify_unlabeled", lambda p, s: {"kind": "missing", "owner": None})
    manifest = {"env": [{"name": "ALERTS_CHANNELS", "secret": {"name": "channels"}}]}
    with pytest.raises(RuntimeError) as exc:
        sec_mod.validate_declared_secrets(PROJECT, "alerts", manifest)
    text = str(exc.value)
    assert text.startswith("1 secret(s) declared for this deployment are not deployable:")
    assert "Resolve, from a machine with gcloud: gapp secrets set channels --deployment alerts" in text
