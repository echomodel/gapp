"""`gapp deploy`, `gapp list` and `gapp project setup` on a fleet: a deploy
takes everything from the deployment's definition, builds from a clone of
its repo at its ref, and applies a gapp upgrade only when told to."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from gapp import __version__
from gapp.admin.cli.main import main
import yaml

from gapp.admin.sdk import versions
from gapp.admin.sdk import definitions, entryfile, fleetstore, record, secrets, settings
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.definitions import Refused

PROJECT = "my-project"
BUCKET = "gapp-my-app-my-project"
PROJECT_BUCKET = "gapp-my-project"
STATE = "deployments/my-app/terraform/state"
REAL_VALIDATE, REAL_MATERIALIZE = secrets.validate_declared_secrets, secrets.materialize_generated_secrets


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(path, message):
    """A commit in a throwaway repo, made from the index with git's plumbing
    so that no hook configured on the machine running the tests takes part."""
    tree = git(path, "write-tree")
    found = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", "-q", "HEAD^{commit}"],
                           capture_output=True, text=True)
    parent = found.stdout.strip() if found.returncode == 0 else None
    new = git(path, "commit-tree", tree, *(["-p", parent] if parent else []), "-m", message)
    git(path, "update-ref", "HEAD", new)


@pytest.fixture(autouse=True)
def mock_git():
    """Real git here, in place of the suite-wide stand-in: a deploy reads the clone's commit, remote and ref."""


@pytest.fixture
def clone(monkeypatch, tmp_path):
    """A clone of acme/my-app with one commit and a Procfile, and a local fleet."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    path = tmp_path / "checkout"
    path.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    git(path, "config", "user.email", "dev" + "@" + "example.test")
    git(path, "config", "user.name", "Dev")
    git(path, "remote", "add", "origin", "https://github.com/acme/my-app.git")
    (path / "Procfile").write_text("web: python -m http.server $PORT\n")
    git(path, "add", "Procfile")
    commit(path, "first")
    monkeypatch.chdir(path)
    monkeypatch.setattr(secrets, "validate_declared_secrets", lambda *a, **k: None)
    monkeypatch.setattr(secrets, "materialize_generated_secrets", lambda *a, **k: [])
    return path


@pytest.fixture
def cloud():
    provider = DummyCloudProvider()
    provider.project_labels[PROJECT] = {"gapp": "hosted"}
    provider.buckets[PROJECT_BUCKET] = {"project": PROJECT, "labels": {}}
    return provider


def last_deployed_by(cloud, version):
    """The project's bucket says this gapp last deployed my-app."""
    cloud.objects[(PROJECT_BUCKET, "deployments/my-app/deployed.yaml")] = yaml.safe_dump({"gapp": version})


def recorded(cloud):
    """The record of my-app's last successful deploy."""
    return yaml.safe_load(cloud.objects[(PROJECT_BUCKET, "deployments/my-app/deployed.yaml")])


def history(cloud):
    return [yaml.safe_load(text) for (bucket, path), text in sorted(cloud.objects.items())
            if path.startswith("deployments/my-app/history/")]


@pytest.fixture
def sdk(cloud, clone):
    definitions.add("my-app", project=PROJECT, repo="acme/my-app")
    return GappSDK(provider=cloud)


def refused(fn, *args, **kwargs):
    with pytest.raises(Refused) as caught:
        fn(*args, **kwargs)
    return caught.value.message


def nothing_happened(cloud):
    return cloud.last_tfvars is None and not cloud.objects and BUCKET not in cloud.buckets


# --- a deploy takes everything from the definition ---


def test_a_deploy_builds_this_clones_commit_and_applies_the_definitions_settings(sdk, cloud, clone):
    definitions.set_value("public", "true", deployment="my-app")
    definitions.set_value("memory", "1Gi", deployment="my-app")
    definitions.env_set("MAX_USERS", "25", deployment="my-app")
    definitions.env_set("APP_USERS_PATH", data_path="users", deployment="my-app")
    definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    result = sdk.deploy_definition()
    head = git(clone, "rev-parse", "HEAD")[:12]
    assert result["commit"] == head and result["image"].endswith(f"/gapp/my-app:{head}")
    assert result["service_url"] == "https://my-app.a.run.app" and result["first_deploy"] is True
    vars = cloud.last_tfvars
    assert (vars["service_name"], vars["project_id"], vars["public"], vars["memory"]) == ("my-app", PROJECT, True, "1Gi")
    assert vars["env"] == {"MAX_USERS": "25", "APP_USERS_PATH": "/mnt/data/users"}
    assert vars["secrets"] == {"APP_KEY": "my-app-app-key"}
    assert vars["data_bucket"] == BUCKET


def test_a_deploy_reads_no_gapp_yaml_from_the_repo(sdk, cloud, clone):
    (clone / "gapp.yaml").write_text("public: true\ndomain: evil.example.com\nenv:\n  - name: X\n    value: y\n")
    sdk.deploy_definition()
    assert cloud.last_tfvars["public"] is False and cloud.last_tfvars["custom_domain"] == ""
    assert cloud.last_tfvars["env"] == {}


def test_terraform_makes_the_data_bucket_and_labels_it_with_the_full_version(sdk, cloud):
    assert BUCKET not in cloud.buckets
    sdk.deploy_definition()
    assert cloud.last_tfvars["adopt_data_bucket"] is False
    assert cloud.buckets[BUCKET]["labels"] == {"gapp-deployment": "my-app",
                                               "gapp-version": settings.version_label(__version__)}
    assert "." not in cloud.buckets[BUCKET]["labels"]["gapp-version"]


def test_a_data_bucket_that_is_there_already_is_taken_over_not_made(sdk, cloud):
    cloud.buckets[BUCKET] = {"project": PROJECT, "labels": {}}
    sdk.deploy_definition()
    assert cloud.last_tfvars["adopt_data_bucket"] is True
    sdk.deploy_definition()
    assert cloud.last_tfvars["adopt_data_bucket"] is True


def test_state_and_the_record_go_in_the_projects_bucket_not_the_apps(sdk, cloud):
    sdk.deploy_definition()
    assert (PROJECT_BUCKET, STATE) in cloud.tf_outputs
    assert {bucket for bucket, _ in cloud.objects} == {PROJECT_BUCKET}
    assert all(path.startswith("deployments/my-app/") for _, path in cloud.objects)


def test_a_deploy_writes_down_what_it_deployed(sdk, cloud, clone):
    definitions.set_value("ref", "main", deployment="my-app")
    definitions.env_set("LOG_LEVEL", "info", deployment="my-app")
    result = sdk.deploy_definition()
    entry = recorded(cloud)
    assert entry["result"] == "deployed" and entry["deployment"] == "my-app"
    assert entry["commit"] == git(clone, "rev-parse", "HEAD") and entry["image"] == result["image"]
    assert entry["gapp"] == __version__ and entry["gapp_installed"] == __version__
    assert entry["from"] == {"kind": "machine"} and entry["deployed_at"].endswith("Z")
    assert entry["definition"] == fleetstore.read()["my-app"]
    assert entry["definition"]["repo"] == "acme/my-app" and entry["definition"]["ref"] == "main"
    assert history(cloud) == [entry]


def test_a_deploy_on_a_runner_records_the_fleet_repo_and_the_run(sdk, cloud, monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_SERVER_URL": "https://github.com",
                       "GITHUB_REPOSITORY": "acme/fleet", "GITHUB_RUN_ID": "42"}.items():
        monkeypatch.setenv(key, value)
    sdk.deploy_definition()
    assert recorded(cloud)["from"] == {"kind": "runner", "fleet_repo": "acme/fleet",
                                       "run": "https://github.com/acme/fleet/actions/runs/42"}


def test_the_record_names_who_deployed_when_no_account_is_configured(sdk, cloud):
    cloud.account = "gapp-deploy" + "@" + "my-project.iam.gserviceaccount.com"
    sdk.deploy_definition()
    assert recorded(cloud)["deployed_by"] == cloud.account


def test_a_failed_deploy_goes_in_the_history_and_leaves_the_last_good_one(sdk, cloud):
    sdk.deploy_definition()
    good = recorded(cloud)
    cloud.apply_fails = "terraform apply failed"
    with pytest.raises(RuntimeError):
        sdk.deploy_definition()
    assert recorded(cloud) == good
    assert [(e["result"], e.get("error")) for e in history(cloud)][-1] == ("failed", "terraform apply failed")


def test_a_deploy_to_a_project_without_its_bucket_names_the_command(sdk, cloud):
    del cloud.buckets[PROJECT_BUCKET]
    assert refused(sdk.deploy_definition).endswith("gapp project setup my-project")
    assert nothing_happened(cloud)


def test_the_cloud_resources_are_named_by_the_id_not_the_name(sdk, cloud):
    definitions.rename("my-app", "prod")
    sdk.deploy_definition("prod")
    assert cloud.last_tfvars["service_name"] == "my-app" and BUCKET in cloud.buckets


def test_the_extra_apis_in_the_definition_are_turned_on(sdk, cloud):
    definitions.apis_add("drive.googleapis.com", deployment="my-app")
    sdk.deploy_definition()
    assert (PROJECT, "drive.googleapis.com") in cloud.apis_enabled


def test_the_definitions_start_command_is_used_when_the_repo_has_no_procfile(sdk, cloud, clone):
    git(clone, "rm", "-q", "Procfile")
    commit(clone, "no procfile")
    assert "nothing was built or deployed" in refused(sdk.deploy_definition)
    definitions.set_value("start", "python -m http.server 8080", deployment="my-app")
    assert sdk.deploy_definition()["start"] == "start"


# --- where it runs ---


def test_outside_a_clone_a_deploy_is_refused(sdk, cloud, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert "Run it in a clone of acme/my-app" in refused(sdk.deploy_definition, "my-app")
    assert nothing_happened(cloud)


def test_a_clone_of_another_repo_is_refused(sdk, cloud, clone):
    definitions.add("other", project=PROJECT, repo="acme/other")
    assert refused(sdk.deploy_definition, "other") == (
        "'other' runs acme/other, and this clone is acme/my-app. Run it in a clone of acme/other.")
    assert nothing_happened(cloud)


def test_a_tag_must_be_checked_out_exactly(sdk, cloud, clone):
    git(clone, "tag", "v1.0.0")
    (clone / "later.txt").write_text("x")
    git(clone, "add", "later.txt")
    commit(clone, "second")
    definitions.set_value("ref", "v1.0.0", deployment="my-app")
    message = refused(sdk.deploy_definition)
    assert "builds 'v1.0.0'" in message and "gapp set ref <ref> --deployment my-app" in message
    assert nothing_happened(cloud)
    git(clone, "checkout", "-q", "v1.0.0")
    assert sdk.deploy_definition()["ref"] == "v1.0.0"


def test_a_branch_must_be_the_one_checked_out_and_its_unpushed_commits_are_built(sdk, cloud, clone):
    definitions.set_value("ref", "main", deployment="my-app")
    (clone / "later.txt").write_text("x")
    git(clone, "add", "later.txt")
    commit(clone, "a commit nobody pushed")
    assert sdk.deploy_definition()["commit"] == git(clone, "rev-parse", "HEAD")[:12]
    git(clone, "checkout", "-q", "-b", "experiment")
    cloud.last_tfvars = None
    assert refused(sdk.deploy_definition).startswith("'my-app' builds 'main', and this clone is on 'experiment'.")
    assert cloud.last_tfvars is None


def test_with_no_ref_the_default_branch_must_be_checked_out_when_it_is_known(sdk, cloud, clone):
    git(clone, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    sdk.deploy_definition()
    git(clone, "checkout", "-q", "-b", "experiment")
    assert "builds 'main', the repo's default branch, and this clone is on 'experiment'" in refused(sdk.deploy_definition)


def test_a_ref_the_clone_does_not_have_is_refused(sdk, cloud):
    definitions.set_value("ref", "release", deployment="my-app")
    assert "which this clone does not have" in refused(sdk.deploy_definition)


def test_a_project_that_is_not_set_up_names_the_command(sdk, cloud):
    cloud.project_labels[PROJECT] = {}
    assert refused(sdk.deploy_definition) == (
        "my-project is not set up for gapp yet. Once per project: gapp project setup my-project")
    assert nothing_happened(cloud)


# --- versions ---


def as_gapp(monkeypatch, version):
    for module in (entryfile, definitions, versions):
        monkeypatch.setattr(module, "__version__", version)


def test_a_second_deploy_by_the_same_gapp_is_an_ordinary_deploy(sdk, cloud):
    sdk.deploy_definition()
    again = sdk.deploy_definition()
    assert again["first_deploy"] is False and again["upgrade_pending"] is False
    assert "No upgrade is pending" in refused(sdk.deploy_definition, upgrade=True)


def test_an_upgrade_is_applied_only_when_the_deploy_says_so(sdk, cloud, monkeypatch):
    sdk.deploy_definition()
    as_gapp(monkeypatch, "4.0.1")
    assert "gapp upgrade --deployment my-app, then deploy it with --upgrade" in refused(sdk.deploy_definition)
    definitions.upgrade("my-app")
    cloud.last_tfvars = None
    message = refused(sdk.deploy_definition)
    assert "This deploy would apply that upgrade" in message and message.endswith("--upgrade")
    assert cloud.last_tfvars is None
    result = sdk.deploy_definition(upgrade=True)
    assert result["upgraded_from"] == __version__
    assert recorded(cloud)["gapp"] == "4.0.1" and recorded(cloud)["upgraded_from"] == __version__
    assert cloud.buckets[BUCKET]["labels"]["gapp-version"] == "4-0-1"


def test_a_dry_run_says_an_upgrade_is_pending_and_changes_nothing(sdk, cloud, monkeypatch):
    sdk.deploy_definition()
    as_gapp(monkeypatch, "4.0.1")
    definitions.upgrade("my-app")
    cloud.last_tfvars, written = None, dict(cloud.objects)
    plan = sdk.deploy_definition(dry_run=True)
    assert plan["upgrade_pending"] is True and plan["last_deployed_by"] == __version__
    assert cloud.last_tfvars is None and cloud.objects == written


def test_a_deployment_last_deployed_by_a_newer_gapp_than_its_definition_is_refused(sdk, cloud):
    last_deployed_by(cloud, "9.0.0")
    assert "was last deployed by gapp 9.0.0" in refused(sdk.deploy_definition)
    assert "Install gapp 9.0.0 or later to deploy it" in refused(sdk.deploy_definition)
    assert cloud.last_tfvars is None and history(cloud) == []


def test_which_gapp_deployed_last_is_recorded_only_after_the_deploy_succeeds(sdk, cloud):
    cloud.apply_fails = "terraform apply failed"
    with pytest.raises(RuntimeError):
        sdk.deploy_definition()
    assert (PROJECT_BUCKET, "deployments/my-app/deployed.yaml") not in cloud.objects
    assert sdk.deploy_definition(dry_run=True)["first_deploy"] is True


# --- the app's app.json, read from the commit the deploy builds ---


def app_json(clone, env):
    (clone / "app.json").write_text(json.dumps({"env": env}))
    git(clone, "add", "app.json")
    commit(clone, "app.json")


def test_a_default_from_app_json_is_passed_and_nothing_is_written_to_the_fleet(sdk, cloud, clone):
    app_json(clone, {"LOG_LEVEL": {"value": "info"}})
    before = fleetstore.read()
    result = sdk.deploy_definition()
    assert cloud.last_tfvars["env"] == {"LOG_LEVEL": "info"}
    assert result["app_json"] == "read" and result["defaults_from_app_json"] == ["LOG_LEVEL"]
    assert fleetstore.read() == before


def test_the_definitions_value_wins_over_app_jsons_default(sdk, cloud, clone):
    app_json(clone, {"LOG_LEVEL": {"value": "info"}})
    definitions.env_set("LOG_LEVEL", "debug", deployment="my-app")
    assert sdk.deploy_definition()["defaults_from_app_json"] == []
    assert cloud.last_tfvars["env"] == {"LOG_LEVEL": "debug"}


def test_a_variable_app_json_needs_and_the_definition_lacks_refuses_the_deploy_with_the_commands(sdk, cloud, clone):
    app_json(clone, {"API_URL": {"description": "Upstream API address"}, "APP_KEY": {"generator": "secret"}})
    message = refused(sdk.deploy_definition)
    assert "its app.json needs 2 variables the definition does not set" in message
    assert "gapp env set API_URL <value> --deployment my-app" in message
    assert "gapp env set APP_KEY --secret --generate --deployment my-app" in message
    assert nothing_happened(cloud)


def test_once_the_definition_supplies_them_the_deploy_goes_ahead(sdk, cloud, clone):
    app_json(clone, {"API_URL": {}, "APP_KEY": {"generator": "secret"}})
    definitions.env_set("API_URL", "https://api.example.com", deployment="my-app")
    definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    sdk.deploy_definition()
    assert cloud.last_tfvars["env"] == {"API_URL": "https://api.example.com"}
    assert cloud.last_tfvars["secrets"] == {"APP_KEY": "my-app-app-key"}


def test_a_generator_in_app_json_never_makes_a_secret_by_itself(sdk, cloud, clone, monkeypatch):
    monkeypatch.setattr(secrets, "materialize_generated_secrets", lambda *a, **k: pytest.fail("a secret was made"))
    app_json(clone, {"APP_KEY": {"generator": "secret"}})
    refused(sdk.deploy_definition)


def test_a_dry_run_shows_what_app_json_adds_and_what_is_missing_and_refuses_nothing(sdk, cloud, clone):
    app_json(clone, {"LOG_LEVEL": {"value": "info"}, "API_URL": {}})
    definitions.env_set("OLD_SETTING", "x", deployment="my-app")
    plan = sdk.deploy_definition(dry_run=True)
    assert plan["defaults_from_app_json"] == ["LOG_LEVEL"]
    assert plan["missing_variables"] == [{"name": "API_URL", "description": None, "generated": False}]
    assert plan["warnings"] == [
        "'my-app' sets OLD_SETTING, and this version's app.json does not list it. The app may not read it."]
    assert nothing_happened(cloud)


def test_a_variable_app_json_does_not_list_is_warned_about_and_still_deployed(sdk, cloud, clone):
    app_json(clone, {"LOG_LEVEL": {"value": "info"}})
    definitions.env_set("OLD_SETTING", "x", deployment="my-app")
    result = sdk.deploy_definition()
    assert len(result["warnings"]) == 1 and "OLD_SETTING" in result["warnings"][0]
    assert cloud.last_tfvars["env"] == {"LOG_LEVEL": "info", "OLD_SETTING": "x"}


def test_an_app_with_no_app_json_is_never_warned_about(sdk, cloud):
    definitions.env_set("ANYTHING", "x", deployment="my-app")
    result = sdk.deploy_definition()
    assert result["app_json"] == "none" and result["warnings"] == [] and result["missing_variables"] == []


def test_an_app_json_that_is_not_committed_is_not_read(sdk, cloud, clone):
    (clone / "app.json").write_text(json.dumps({"env": {"API_URL": {}}}))
    assert sdk.deploy_definition()["app_json"] == "none"


def test_an_app_json_gapp_cannot_read_refuses_the_deploy(sdk, cloud, clone):
    (clone / "app.json").write_text("{")
    git(clone, "add", "app.json")
    commit(clone, "broken")
    message = refused(sdk.deploy_definition)
    assert "app.json is not valid JSON" in message and nothing_happened(cloud)


def test_the_commands_print_the_warning_and_the_dry_run_prints_what_is_missing(sdk, cloud, clone, monkeypatch):
    monkeypatch.setattr("gapp.admin.cli.main.GappSDK", lambda: sdk)
    app_json(clone, {"LOG_LEVEL": {"value": "info"}, "API_URL": {"description": "Upstream API address"}})
    definitions.env_set("OLD_SETTING", "x", deployment="my-app")
    out = CliRunner().invoke(main, ["deploy", "--dry-run"]).output
    assert "From the app's app.json, as defaults: LOG_LEVEL." in out
    assert "'my-app' sets OLD_SETTING, and this version's app.json does not list it." in out
    assert "A deploy would be refused:\n  API_URL (Upstream API address)\n" in out
    assert out.endswith("Nothing was built or changed.\n")
    definitions.env_set("API_URL", "u", deployment="my-app")
    out = CliRunner().invoke(main, ["deploy"]).output
    assert out.startswith("'my-app' sets OLD_SETTING") and "my-app is deployed to my-project" in out


# --- what Terraform is given ---


@pytest.mark.parametrize("key", ["project_id", "service_name", "image", "memory", "cpu", "max_instances", "env",
                                 "secrets", "public", "custom_domain", "data_bucket",
                                 "adopt_data_bucket", "labels"])
def test_every_variable_the_service_module_requires_is_passed(sdk, cloud, key):
    """The module declares these with no default, so a deploy that drops
    one fails at plan instead of deploying without, say, its data volume."""
    sdk.deploy_definition()
    assert key in cloud.last_tfvars
    assert cloud.last_tfvars["labels"] == {"gapp-deployment": "my-app",
                                           "gapp-version": settings.version_label(__version__)}


# --- secrets at deploy ---


@pytest.fixture
def secret_calls(monkeypatch, clone):
    """The real secret checks of a deploy, with Secret Manager stood in for.
    `present` holds the ids of the secrets the project already has."""
    monkeypatch.setattr(secrets, "validate_declared_secrets", REAL_VALIDATE)
    monkeypatch.setattr(secrets, "materialize_generated_secrets", REAL_MATERIALIZE)
    calls = {"present": set(), "made": [], "versions": []}
    monkeypatch.setattr(secrets, "list_secrets_by_label", lambda project, id: [{"id": i} for i in calls["present"]])
    monkeypatch.setattr(secrets, "_ensure_secret", lambda project, secret_id, id: calls["made"].append(secret_id) or "created")
    monkeypatch.setattr(secrets, "_add_secret_version",
                        lambda project, secret_id, value: calls["versions"].append((secret_id, len(value))))
    monkeypatch.setattr(secrets, "_classify_unlabeled", lambda project, secret_id: {"kind": "missing"})
    return calls


def test_a_generated_secret_is_made_at_the_first_deploy_with_a_value_no_one_sees(sdk, cloud, secret_calls):
    definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    sdk.deploy_definition()
    assert secret_calls["made"] == ["my-app-app-key"] and secret_calls["versions"] == [("my-app-app-key", 32)]
    assert cloud.last_tfvars["secrets"] == {"APP_KEY": "my-app-app-key"}


def test_a_generated_secret_that_exists_is_never_written_again(sdk, cloud, secret_calls):
    definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    secret_calls["present"].add("my-app-app-key")
    sdk.deploy_definition()
    assert secret_calls["made"] == [] and secret_calls["versions"] == []
    assert cloud.last_tfvars["secrets"] == {"APP_KEY": "my-app-app-key"}


def test_a_secret_with_no_value_stops_the_deploy_before_terraform(sdk, cloud, secret_calls):
    definitions.env_set("API_TOKEN", secret="", deployment="my-app")
    with pytest.raises(RuntimeError, match="api-token"):
        sdk.deploy_definition()
    assert cloud.last_tfvars is None and BUCKET not in cloud.buckets
    assert [e["result"] for e in history(cloud)] == ["failed"]


# --- the custom domain is never taken down by accident ---
#
# Terraform replaces a changed domain mapping by destroying the old one
# first, so a typo or a removed `domain` would take the live domain down. A
# deploy refuses those before any build or apply unless told to replace
# it.


@pytest.fixture
def builds(sdk, cloud, monkeypatch):
    count = {"n": 0}
    real = cloud.submit_build_sync

    def counting(*args, **kwargs):
        count["n"] += 1
        return real(*args, **kwargs)
    monkeypatch.setattr(cloud, "submit_build_sync", counting)
    monkeypatch.setattr(cloud, "image_exists", lambda *a, **k: False)
    return count


def live_domain(cloud, domain, serving=True):
    last_deployed_by(cloud, __version__)
    cloud.tf_outputs[(PROJECT_BUCKET, STATE)] = {"service_url": "https://my-app.a.run.app", "custom_domain": domain}
    ready = "True" if serving else "False"
    cloud.domain_mappings[(PROJECT, domain)] = {"status": {"conditions": [
        {"type": "Ready", "status": ready}, {"type": "DomainRoutable", "status": ready}]}}


def test_a_domain_whose_mapping_never_came_up_is_changed_or_removed_without_the_flag(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.com", serving=False)
    sdk.deploy_definition()
    assert cloud.last_tfvars["custom_domain"] == ""


def test_a_mapping_that_cannot_be_read_counts_as_serving(sdk, cloud, builds, monkeypatch):
    live_domain(cloud, "mcp.example.com")
    def unreadable(*a):
        raise RuntimeError("denied")
    monkeypatch.setattr(cloud, "get_domain_mapping", unreadable)
    with pytest.raises(RuntimeError, match="remove the live domain mapping"):
        sdk.deploy_definition()


def test_replacing_a_live_domain_is_refused_before_anything_is_built(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.net")
    definitions.set_value("domain", "mcp.example.com", deployment="my-app")
    with pytest.raises(RuntimeError, match="replace the live domain mapping") as caught:
        sdk.deploy_definition()
    text = str(caught.value)
    assert "mcp.example.net" in text and "mcp.example.com" in text and "--replace-domain" in text
    assert text.endswith("gapp set domain mcp.example.net")
    assert builds["n"] == 0 and cloud.last_tfvars is None


def test_removing_a_live_domain_is_refused_before_anything_is_built(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.com")
    with pytest.raises(RuntimeError, match="remove the live domain mapping mcp.example.com") as caught:
        sdk.deploy_definition()
    assert str(caught.value).endswith("gapp set domain mcp.example.com")
    assert builds["n"] == 0 and cloud.last_tfvars is None


def test_replace_domain_allows_the_change_and_the_removal(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.net")
    definitions.set_value("domain", "mcp.example.com", deployment="my-app")
    sdk.deploy_definition(replace_domain=True)
    assert cloud.last_tfvars["custom_domain"] == "mcp.example.com"
    definitions.unset_value("domain", deployment="my-app")
    sdk.deploy_definition(replace_domain=True)
    assert cloud.last_tfvars["custom_domain"] == ""


def test_a_new_domain_is_deployed_without_asking_whether_it_is_verified(sdk, cloud, builds):
    """Cloud Run refuses a domain the deploying identity has not verified;
    gapp does not check it first (CONTRIBUTING, "Considered and rejected")."""
    definitions.set_value("domain", "mcp.app.example.com", deployment="my-app")
    sdk.deploy_definition()
    assert cloud.last_tfvars["custom_domain"] == "mcp.app.example.com"


def test_a_redeploy_that_keeps_the_domain_needs_no_flag(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.com")
    definitions.set_value("domain", "mcp.example.com", deployment="my-app")
    sdk.deploy_definition()
    assert cloud.last_tfvars["custom_domain"] == "mcp.example.com"


def test_state_that_cannot_be_read_is_never_taken_for_no_live_domain(sdk, cloud, builds):
    live_domain(cloud, "mcp.example.com")
    cloud.unreadable_state.add((PROJECT_BUCKET, STATE))
    with pytest.raises(RuntimeError, match="Refusing to deploy 'my-app': its Terraform state could not be read") as caught:
        sdk.deploy_definition(replace_domain=True)          # not even the flag gets past an unknown state
    assert "Nothing was built or changed" in str(caught.value) and PROJECT_BUCKET in str(caught.value)
    assert builds["n"] == 0 and cloud.last_tfvars is None




# --- project setup and list ---


def test_project_setup_turns_on_the_apis_grants_the_build_and_marks_the_project(clone):
    cloud = DummyCloudProvider()
    result = GappSDK(provider=cloud).project_setup(PROJECT)
    assert result["was_set_up"] is False and cloud.project_labels[PROJECT] == {"gapp": "hosted"}
    assert (PROJECT, "run.googleapis.com") in cloud.apis_enabled and cloud.iam_bindings
    again = GappSDK(provider=cloud).project_setup(PROJECT)
    assert again["was_set_up"] is True and again["added"] == [] and again["checked"] is True


def test_project_setup_again_says_what_it_added(clone):
    cloud = DummyCloudProvider()
    GappSDK(provider=cloud).project_setup(PROJECT)
    cloud.apis_enabled.discard((PROJECT, "run.googleapis.com"))
    del cloud.buckets[PROJECT_BUCKET]
    again = GappSDK(provider=cloud).project_setup(PROJECT)
    assert again["added"] == ["run.googleapis.com", f"the bucket {PROJECT_BUCKET}"]
    assert (PROJECT, "run.googleapis.com") in cloud.apis_enabled


def test_project_setup_that_cannot_list_the_apis_turns_them_all_on(clone, monkeypatch):
    cloud = DummyCloudProvider()
    GappSDK(provider=cloud).project_setup(PROJECT)
    monkeypatch.setattr(cloud, "enabled_apis", lambda project: None)
    cloud.apis_enabled.clear()
    again = GappSDK(provider=cloud).project_setup(PROJECT)
    assert again["checked"] is False and again["added"] == []
    assert (PROJECT, "run.googleapis.com") in cloud.apis_enabled


def test_project_setup_makes_the_projects_bucket_once(clone):
    cloud = DummyCloudProvider()
    assert GappSDK(provider=cloud).project_setup(PROJECT)["bucket"] == PROJECT_BUCKET
    assert cloud.buckets[PROJECT_BUCKET]["project"] == PROJECT
    cloud.objects[(PROJECT_BUCKET, "kept")] = "x"
    GappSDK(provider=cloud).project_setup(PROJECT)
    assert cloud.objects[(PROJECT_BUCKET, "kept")] == "x"


def test_project_setup_says_so_when_the_bucket_cannot_be_made(clone, monkeypatch):
    cloud = DummyCloudProvider()
    def taken(project, name):
        raise RuntimeError("409 the name is not available")
    monkeypatch.setattr(cloud, "create_bucket", taken)
    message = refused(GappSDK(provider=cloud).project_setup, PROJECT)
    assert "gapp-my-project could not be made" in message and PROJECT not in cloud.project_labels


def test_project_setup_keeps_the_projects_other_labels(clone):
    cloud = DummyCloudProvider()
    cloud.project_labels[PROJECT] = {"team": "platform"}
    GappSDK(provider=cloud).project_setup(PROJECT)
    assert cloud.project_labels[PROJECT] == {"team": "platform", "gapp": "hosted"}


def test_list_shows_every_definition_and_marks_one_this_gapp_is_newer_than(sdk, monkeypatch):
    definitions.add("staging", copy_of="my-app", project="my-staging", ref="develop")
    rows = sdk.list_definitions()["deployments"]
    assert [(r["deployment"], r["project"], r["ref"], r["upgrade"]) for r in rows] == [
        ("my-app", PROJECT, None, None), ("staging", "my-staging", "develop", None)]
    as_gapp(monkeypatch, "4.0.1")
    assert {r["upgrade"] for r in sdk.list_definitions()["deployments"]} == {"available"}


# --- the commands ---


def test_the_deploy_command_exits_2_on_a_refusal_and_prints_the_url_on_success(sdk, cloud, monkeypatch):
    monkeypatch.setattr("gapp.admin.cli.main.GappSDK", lambda: sdk)
    cloud.project_labels[PROJECT] = {}
    result = CliRunner().invoke(main, ["deploy"])
    assert result.exit_code == 2 and "gapp project setup my-project" in result.output
    cloud.project_labels[PROJECT] = {"gapp": "hosted"}
    result = CliRunner().invoke(main, ["deploy"])
    assert result.exit_code == 0 and "URL: https://my-app.a.run.app" in result.output


def test_the_dry_run_command_says_what_would_happen(sdk, cloud, monkeypatch):
    monkeypatch.setattr("gapp.admin.cli.main.GappSDK", lambda: sdk)
    out = CliRunner().invoke(main, ["deploy", "--dry-run"]).output
    assert "my-app would deploy to my-project, building the default branch at" in out
    assert "this would be its first deploy" in out and out.endswith("Nothing was built or changed.\n")
    assert cloud.last_tfvars is None


def test_the_list_command_names_the_fleet_and_each_deployment(sdk, monkeypatch):
    monkeypatch.setattr("gapp.admin.cli.main.GappSDK", lambda: sdk)
    out = CliRunner().invoke(main, ["list"]).output
    assert out.startswith("Your fleet is kept in a file on this machine")
    assert f"  my-app  acme/my-app  on my-project  builds the default branch  gapp {__version__}\n" in out
    listed = json.loads(CliRunner().invoke(main, ["list", "--json"]).output)
    assert listed["deployments"][0]["deployment"] == "my-app"


# --- the commands that touch a deployment's infrastructure ---
# CONTRIBUTING, "Commands that touch a deployment's infrastructure", lists
# them; each gets its deployment through loading.load_deployment (or its
# two steps) and so refuses infrastructure this gapp does not work with.


INFRASTRUCTURE_COMMANDS = {
    "gapp deploy": lambda sdk: sdk.deploy_definition(),
    "gapp status": lambda sdk: sdk.status_definition("my-app"),
    "gapp secrets list": lambda sdk: secrets.list_secrets("my-app"),
    "gapp secrets set": lambda sdk: secrets.set_secret("API_KEY", "x", deployment="my-app"),
    "gapp secrets get": lambda sdk: secrets.get_secret("API_KEY", deployment="my-app"),
}


@pytest.fixture
def infrastructure(sdk, cloud, monkeypatch):
    """my-app with a secret, and every infrastructure command reading `cloud`."""
    definitions.env_set("API_KEY", secret="", deployment="my-app")
    monkeypatch.setattr(secrets, "_provider", lambda: cloud)
    monkeypatch.setattr("gapp.admin.sdk.core.get_provider", lambda: cloud, raising=False)
    monkeypatch.setattr(GappSDK, "__init__", lambda self, provider=None: setattr(self, "provider", cloud))
    return sdk


@pytest.mark.parametrize("command", sorted(INFRASTRUCTURE_COMMANDS))
def test_every_infrastructure_command_refuses_infrastructure_below_compatible_from(infrastructure, cloud, command):
    last_deployed_by(cloud, "4.0.0rc13")
    message = refused(INFRASTRUCTURE_COMMANDS[command], infrastructure)
    assert "'my-app' was last deployed by gapp 4.0.0rc13" in message
    assert cloud.last_tfvars is None


@pytest.mark.parametrize("command", sorted(INFRASTRUCTURE_COMMANDS))
def test_every_infrastructure_command_refuses_infrastructure_from_a_newer_gapp(infrastructure, cloud, command):
    last_deployed_by(cloud, "9.0.0")
    assert "Install gapp 9.0.0 or later" in refused(INFRASTRUCTURE_COMMANDS[command], infrastructure)


def test_a_deploy_with_upgrade_carries_infrastructure_from_upgrades_from(sdk, cloud):
    last_deployed_by(cloud, "4.0.0rc13")
    result = sdk.deploy_definition(upgrade=True)
    assert result["upgraded_from"] == "4.0.0rc13" and recorded(cloud)["gapp"] == __version__
