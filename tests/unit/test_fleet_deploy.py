"""`gapp deploy`, `gapp list` and `gapp project setup` on a fleet: a deploy
takes everything from the deployment's definition, builds from a clone of
its repo at its ref, and applies a gapp upgrade only when told to."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from gapp import __version__
from gapp.admin.cli.main import main
from gapp.admin.sdk import definitions, entryfile, fleetstore, secrets, settings
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.definitions import Refused

PROJECT = "my-project"
BUCKET = "gapp-my-app-my-project"


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
    return provider


@pytest.fixture
def sdk(cloud, clone):
    definitions.add("my-app", project=PROJECT, repo="acme/my-app")
    return GappSDK(provider=cloud)


def refused(fn, *args, **kwargs):
    with pytest.raises(Refused) as caught:
        fn(*args, **kwargs)
    return caught.value.message


def nothing_happened(cloud):
    return cloud.last_tfvars is None and not cloud.label_writes


# --- a deploy takes everything from the definition ---


def test_a_deploy_builds_this_clones_commit_and_applies_the_definitions_settings(sdk, cloud, clone):
    definitions.set_value("public", "true", deployment="my-app")
    definitions.set_value("memory", "1Gi", deployment="my-app")
    definitions.env_set("MAX_USERS", "25", deployment="my-app")
    definitions.env_set("APP_USERS_PATH", data_path="users", deployment="my-app")
    definitions.env_set("SIGNING_KEY", secret="", generate=True, deployment="my-app")
    result = sdk.deploy_definition()
    head = git(clone, "rev-parse", "HEAD")[:12]
    assert result["commit"] == head and result["image"].endswith(f"/gapp/my-app:{head}")
    assert result["service_url"] == "https://my-app.a.run.app" and result["first_deploy"] is True
    vars = cloud.last_tfvars
    assert (vars["service_name"], vars["project_id"], vars["public"], vars["memory"]) == ("my-app", PROJECT, True, "1Gi")
    assert vars["env"] == {"MAX_USERS": "25", "APP_USERS_PATH": "/mnt/data/users"}
    assert vars["secrets"] == {"SIGNING_KEY": "my-app-signing-key"}
    assert vars["data_bucket"] == BUCKET


def test_a_deploy_reads_no_gapp_yaml_from_the_repo(sdk, cloud, clone):
    (clone / "gapp.yaml").write_text("public: true\ndomain: evil.example.com\nenv:\n  - name: X\n    value: y\n")
    sdk.deploy_definition()
    assert cloud.last_tfvars["public"] is False and cloud.last_tfvars["custom_domain"] == ""
    assert cloud.last_tfvars["env"] == {}


def test_the_first_deploy_creates_the_bucket_and_labels_it_with_the_full_version(sdk, cloud):
    assert BUCKET not in cloud.buckets
    sdk.deploy_definition()
    assert cloud.buckets[BUCKET]["labels"] == {"gapp-deployment": "my-app",
                                               "gapp-version": settings.version_label(__version__)}
    assert "." not in cloud.buckets[BUCKET]["labels"]["gapp-version"]


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
    for module in (entryfile, definitions):
        monkeypatch.setattr(module, "__version__", version)


def test_a_second_deploy_by_the_same_gapp_is_an_ordinary_deploy(sdk, cloud):
    sdk.deploy_definition()
    again = sdk.deploy_definition()
    assert again["first_deploy"] is False and again["upgrade_pending"] is False
    assert "No upgrade is pending" in refused(sdk.deploy_definition, upgrade=True)


def test_an_upgrade_is_applied_only_when_the_deploy_says_so(sdk, cloud, monkeypatch):
    sdk.deploy_definition()
    as_gapp(monkeypatch, "4.0.1")
    assert refused(sdk.deploy_definition).endswith("gapp upgrade --deployment my-app")
    definitions.upgrade("my-app")
    cloud.last_tfvars = None
    message = refused(sdk.deploy_definition)
    assert "This deploy would apply that upgrade" in message and message.endswith("--upgrade")
    assert cloud.last_tfvars is None
    result = sdk.deploy_definition(upgrade=True)
    assert result["upgraded_from"] == __version__
    assert cloud.buckets[BUCKET]["labels"]["gapp-version"] == "4-0-1"


def test_a_dry_run_says_an_upgrade_is_pending_and_changes_nothing(sdk, cloud, monkeypatch):
    sdk.deploy_definition()
    as_gapp(monkeypatch, "4.0.1")
    definitions.upgrade("my-app")
    cloud.last_tfvars, writes = None, list(cloud.label_writes)
    plan = sdk.deploy_definition(dry_run=True)
    assert plan["upgrade_pending"] is True and plan["last_deployed_by"] == __version__
    assert cloud.last_tfvars is None and cloud.label_writes == writes


def test_a_deployment_last_deployed_by_a_newer_gapp_than_its_definition_is_refused(sdk, cloud):
    cloud.buckets[BUCKET] = {"project": PROJECT, "labels": {"gapp-version": "9-0-0"}}
    assert "A deployment does not go back" in refused(sdk.deploy_definition)
    assert nothing_happened(cloud)


def test_a_bucket_labelled_with_only_a_major_number_counts_as_an_earlier_gapp(sdk, cloud):
    cloud.buckets[BUCKET] = {"project": PROJECT, "labels": {"gapp-version": "4"}}
    assert sdk.deploy_definition(dry_run=True)["upgrade_pending"] is True


def test_the_label_is_written_only_after_the_deploy_succeeds(sdk, cloud, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("terraform apply failed")
    monkeypatch.setattr(cloud, "apply_infrastructure", fail)
    with pytest.raises(RuntimeError):
        sdk.deploy_definition()
    assert not cloud.label_writes


# --- project setup and list ---


def test_project_setup_turns_on_the_apis_grants_the_build_and_marks_the_project(clone):
    cloud = DummyCloudProvider()
    result = GappSDK(provider=cloud).project_setup(PROJECT)
    assert result["was_set_up"] is False and cloud.project_labels[PROJECT] == {"gapp": "hosted"}
    assert (PROJECT, "run.googleapis.com") in cloud.apis_enabled and cloud.iam_bindings
    assert GappSDK(provider=cloud).project_setup(PROJECT)["was_set_up"] is True


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
