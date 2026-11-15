"""Defining deployments and changing their settings in a local fleet:
`add`, `rename`, `remove`, `show`, `upgrade`, `set`, `unset`, `apis`, `env`, `apply`.
None of them touches the cloud; a refusal changes nothing and exits 2."""

import json
import subprocess

import pytest
from click.testing import CliRunner

from gapp import __version__
from gapp.admin.cli.main import main
from gapp.admin.sdk import definitions, entryfile, fleetstore
from gapp.admin.sdk.definitions import Refused


@pytest.fixture(autouse=True)
def local_machine(monkeypatch, tmp_path):
    """A machine with a local fleet, standing in a folder that is not a clone."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return tmp_path


def clone(tmp_path, url="https://github.com/acme/my-app.git", folder="checkout"):
    path = tmp_path / folder
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", url], check=True)
    return path


def fleet():
    return fleetstore.read()


def run(*args):
    return CliRunner().invoke(main, list(args))


def refused(fn, *args, **kwargs):
    before = fleetstore.location().path.read_text() if fleetstore.location().path.exists() else None
    with pytest.raises(Refused) as caught:
        fn(*args, **kwargs)
    after = fleetstore.location().path.read_text() if fleetstore.location().path.exists() else None
    assert before == after, "a refusal changed the fleet"
    return caught.value.message


@pytest.fixture
def my_app():
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    return "my-app"


# --- add ---


def test_add_writes_a_definition_with_this_gapps_version_and_no_settings():
    definitions.add("my-app", project="my-project", repo="acme/my-app", ref="main")
    assert fleet() == {"my-app": {"id": "my-app", "repo": "acme/my-app", "project": "my-project",
                                  "ref": "main", "gapp": __version__, "settings": {}}}


def test_add_in_a_clone_takes_its_name_and_repo_from_the_remote(local_machine):
    definitions.add(project="my-project", cwd=clone(local_machine))
    assert fleet()["my-app"]["repo"] == "acme/my-app"


def test_add_never_uses_the_folders_name(local_machine):
    folder = local_machine / "looks-like-a-name"
    folder.mkdir()
    assert "Give the deployment a name" in refused(definitions.add, project="my-project", cwd=folder)


def test_add_needs_a_project():
    assert "--project <project>" in refused(definitions.add, "my-app")


def test_add_refuses_a_name_that_exists_and_says_whose_it_is(my_app):
    message = refused(definitions.add, "my-app", project="other-project")
    assert "already a deployment (acme/my-app, on my-project)" in message


def test_a_name_whose_id_is_taken_on_the_project_gets_the_next_one(my_app):
    definitions.rename("my-app", "prod")
    definitions.add("my-app", project="my-project", repo="acme/other")
    assert fleet()["prod"]["id"] == "my-app" and fleet()["my-app"]["id"] == "my-app-2"


def test_add_takes_no_id():
    assert run("add", "my-app", "--project", "my-project", "--id", "x").exit_code == 2
    assert fleet() == {}


def test_a_repo_given_as_a_url_is_recorded_as_owner_and_name():
    definitions.add("my-app", project="my-project", repo="https://github.com/acme/my-app.git")
    assert fleet()["my-app"]["repo"] == "acme/my-app"


def test_a_copy_takes_the_repo_ref_and_settings_but_not_the_domain(my_app):
    definitions.set_value("ref", "main", deployment="my-app")
    definitions.set_value("domain", "app.example.com", deployment="my-app")
    definitions.env_set("MAX_USERS", "25", deployment="my-app")
    definitions.add("staging", copy_of="my-app", project="my-staging")
    copy = fleet()["staging"]
    assert (copy["repo"], copy["ref"], copy["project"]) == ("acme/my-app", "main", "my-staging")
    assert copy["settings"] == {"env": {"MAX_USERS": {"value": "25"}}}
    assert fleet()["my-app"]["settings"]["domain"] == "app.example.com"


def test_a_copy_needs_its_own_name_and_takes_no_repo(my_app):
    assert "needs its own name" in refused(definitions.add, copy_of="my-app")
    assert "takes no --repo" in refused(definitions.add, "staging", copy_of="my-app", repo="acme/other")


# --- which deployment ---


def test_outside_a_clone_the_deployment_must_be_named(my_app):
    message = refused(definitions.show)
    assert message == "Say which deployment: gapp show --deployment <name>. Your fleet holds: my-app."


def test_in_a_clone_the_remote_picks_the_one_deployment(local_machine, my_app):
    assert definitions.show(cwd=clone(local_machine))["deployment"] == "my-app"


def test_in_a_clone_with_several_deployments_each_is_listed(local_machine, my_app):
    definitions.add("staging", copy_of="my-app", project="my-staging")
    message = refused(definitions.show, cwd=clone(local_machine))
    assert "acme/my-app has 2 deployments" in message and "staging" in message


def test_in_a_clone_of_a_repo_with_no_deployment_the_add_command_is_named(local_machine, my_app):
    message = refused(definitions.show, cwd=clone(local_machine, "https://github.com/acme/other.git"))
    assert message == "acme/other has no deployment in your fleet. Define one: gapp add --project <project>"


def test_a_name_that_is_not_a_deployment_is_refused(my_app):
    assert "'nope' is not a deployment in your fleet" in refused(definitions.show, "nope")


# --- rename ---


def test_rename_changes_the_name_and_keeps_the_id(my_app):
    definitions.rename("my-app", "prod")
    assert list(fleet()) == ["prod"] and fleet()["prod"]["id"] == "my-app"


def test_rename_refuses_a_taken_name(my_app):
    definitions.add("other", project="my-project", repo="acme/other")
    assert "already a deployment" in refused(definitions.rename, "my-app", "other")



# --- remove ---


def test_remove_takes_the_definition_out_and_says_what_it_left(my_app):
    definitions.env_set("API_KEY", secret="api-key", deployment="my-app")
    definitions.set_value("ref", "v1.2.0", deployment="my-app")
    result = definitions.remove("my-app")
    assert fleet() == {}
    assert result["left"] == {"project": "my-project", "service": "my-app",
                              "bucket": "gapp-my-app-my-project", "secrets": ["my-app-api-key"]}
    assert result["add_again"] == ["gapp add my-app --project my-project --repo acme/my-app --ref v1.2.0"]
    assert result["removed"]["settings"]["env"]["API_KEY"] == {"secret": "api-key"}


def test_a_removed_deployment_added_again_gets_the_same_id_even_after_a_rename(my_app):
    definitions.rename("my-app", "prod")
    for command in definitions.remove("prod")["add_again"]:
        assert run(*command.split()[1:]).exit_code == 0
    assert list(fleet()) == ["prod"] and fleet()["prod"]["id"] == "my-app"


def test_remove_leaves_the_other_deployments(my_app):
    definitions.add("other", project="my-project", repo="acme/other")
    definitions.remove("my-app")
    assert list(fleet()) == ["other"]


def test_remove_refuses_a_name_the_fleet_does_not_hold(my_app):
    assert "nope" in refused(definitions.remove, "nope")


def test_remove_says_what_stays_and_gives_no_teardown_commands(my_app):
    out = run("remove", "my-app").output
    assert "Nothing in Google Cloud was touched." in out
    assert "gapp has no command to take them down yet." in out
    assert "  gapp add my-app --project my-project --repo acme/my-app\n" in out
    assert "does not bring back its settings" in out
    assert "gcloud" not in out and "terraform" not in out and "--id" not in out


# --- set and unset ---


def test_set_parses_each_key_as_its_type(my_app):
    for key, value in (("public", "true"), ("max_instances", "3"), ("memory", "1Gi"),
                       ("domain", "App.Example.com"), ("start", "my-app serve")):
        definitions.set_value(key, value, deployment="my-app")
    assert fleet()["my-app"]["settings"] == {"public": True, "domain": "app.example.com",
                                             "start": "my-app serve", "memory": "1Gi", "max_instances": 3}


@pytest.mark.parametrize("key,value,says", [
    ("public", "yes", "true or false"),
    ("max_instances", "0", "whole number"),
    ("memory", "lots", "not a memory size"),
    ("domain", "https://app.example.com", "not a domain name"),
    ("colour", "red", "is not a setting"),
])
def test_set_refuses_a_bad_value_and_says_what_the_key_takes(my_app, key, value, says):
    assert says in refused(definitions.set_value, key, value, deployment="my-app")


def test_the_ref_and_repo_are_set_on_the_definition_itself(my_app):
    result = definitions.set_value("ref", "v1.2.0", deployment="my-app")
    assert result["changes"] == [{"key": "ref", "before": "not set", "after": "v1.2.0"}]
    assert fleet()["my-app"]["ref"] == "v1.2.0" and "ref" not in fleet()["my-app"]["settings"]
    definitions.unset_value("ref", deployment="my-app")
    assert "ref" not in fleet()["my-app"]


@pytest.mark.parametrize("key", ["id", "project", "gapp"])
def test_the_id_project_and_gapp_version_cannot_be_set(my_app, key):
    refused(definitions.set_value, key, "x", deployment="my-app")


def test_setting_a_value_it_already_has_changes_nothing(my_app):
    definitions.set_value("public", "true", deployment="my-app")
    assert definitions.set_value("public", "true", deployment="my-app")["changed"] is False


def test_unset_reports_the_default_that_applies_again(my_app):
    definitions.set_value("memory", "1Gi", deployment="my-app")
    result = definitions.unset_value("memory", deployment="my-app")
    assert result["changes"] == [{"key": "memory", "before": "1Gi", "after": "512Mi"}]
    assert fleet()["my-app"]["settings"] == {}


# --- env and apis ---


def test_a_secret_with_no_name_takes_the_variables_in_lower_case_with_dashes(my_app):
    result = definitions.env_set("APP_KEY", secret="", generate=True, deployment="my-app")
    assert fleet()["my-app"]["settings"]["env"] == {"APP_KEY": {"secret": "app-key", "generate": True}}
    assert "next" not in result


def test_a_secret_that_is_not_generated_names_the_command_that_stores_its_value(my_app):
    result = definitions.env_set("AUTH_ISSUER", secret="auth-issuer", deployment="my-app")
    assert result["next"] == "gapp secrets set auth-issuer --deployment my-app"


def test_a_data_path_is_stored_as_the_placeholder_and_shown_in_words(my_app):
    result = definitions.env_set("APP_USERS_PATH", data_path="users", deployment="my-app")
    assert fleet()["my-app"]["settings"]["env"]["APP_USERS_PATH"] == {"value": "{{DATA_PATH}}/users"}
    assert result["changes"][0]["after"] == "the data folder, users"


@pytest.mark.parametrize("kwargs,says", [
    ({}, "Give a value"),
    ({"value": "x", "secret": "s"}, "Pick one"),
    ({"value": "x", "generate": True}, "--generate goes with --secret"),
    ({"secret": "Not_A_Name"}, "not a secret name"),
])
def test_env_set_takes_exactly_one_kind(my_app, kwargs, says):
    assert says in refused(definitions.env_set, "NAME", deployment="my-app", **kwargs)


def test_env_unset_removes_the_variable(my_app):
    definitions.env_set("MAX_USERS", "25", deployment="my-app")
    definitions.env_unset("MAX_USERS", deployment="my-app")
    assert fleet()["my-app"]["settings"] == {}
    assert definitions.env_unset("MAX_USERS", deployment="my-app")["changed"] is False


def test_apis_are_added_and_removed_one_at_a_time(my_app):
    definitions.apis_add("drive.googleapis.com", deployment="my-app")
    definitions.apis_add("sheets.googleapis.com", deployment="my-app")
    definitions.apis_remove("drive.googleapis.com", deployment="my-app")
    assert fleet()["my-app"]["settings"] == {"apis": ["sheets.googleapis.com"]}
    assert "not a Google Cloud API name" in refused(definitions.apis_add, "drive", deployment="my-app")


# --- apply ---


def test_apply_replaces_every_setting_and_lists_each_change(my_app):
    definitions.set_value("public", "true", deployment="my-app")
    definitions.env_set("OLD", "1", deployment="my-app")
    result = definitions.apply("memory: 1Gi\nenv:\n  NEW: {value: 2}\n", deployment="my-app")
    assert fleet()["my-app"]["settings"] == {"memory": "1Gi", "env": {"NEW": {"value": "2"}}}
    assert {(c["key"], c["before"], c["after"]) for c in result["changes"]} == {
        ("public", "true", "false"), ("memory", "512Mi", "1Gi"),
        ("env OLD", "1", "not set"), ("env NEW", "not set", "2")}


def test_a_dry_run_of_apply_reports_the_same_changes_and_writes_nothing(my_app, local_machine):
    definitions.set_value("public", "true", deployment="my-app")
    before = fleetstore.location().path.read_text()
    result = definitions.apply("memory: 1Gi\n", deployment="my-app", dry_run=True)
    assert result["dry_run"] is True and result["changed"] is True
    assert {(c["key"], c["before"], c["after"]) for c in result["changes"]} == {
        ("public", "true", "false"), ("memory", "512Mi", "1Gi")}
    assert fleetstore.location().path.read_text() == before
    file = local_machine / "settings.yaml"
    file.write_text("memory: 1Gi\n")
    assert run("apply", str(file), "--dry-run", "-d", "my-app").output == (
        "my-app: public: true -> false\nmy-app: memory: 512Mi -> 1Gi\nNothing was changed.\n")
    assert fleetstore.location().path.read_text() == before


def test_a_dry_run_of_apply_refuses_what_apply_refuses(my_app):
    assert "'deployment' is not a setting" in refused(definitions.apply, "deployment: my-app\n", deployment="my-app", dry_run=True)


@pytest.mark.parametrize("text,says", [
    ("deployment: my-app\n", "'deployment' is not a setting"),
    ("env:\n  A: {value: 1, secret: s}\n", "exactly one of"),
    ("public: [\n", "not valid YAML"),
])
def test_apply_with_anything_wrong_changes_nothing(my_app, text, says):
    definitions.set_value("public", "true", deployment="my-app")
    assert says in refused(definitions.apply, text, deployment="my-app")


# --- versions ---


def write_version(name, version):
    deployments = fleet()
    deployments[name]["gapp"] = version
    fleetstore.write(deployments)


def test_upgrade_with_nothing_to_upgrade_is_refused(my_app):
    assert f"already on gapp {__version__}" in refused(definitions.upgrade, "my-app")


def test_upgrade_moves_an_older_definition_to_this_gapp_and_nothing_else(my_app, monkeypatch):
    definitions.set_value("public", "true", deployment="my-app")
    for module in (entryfile, definitions):
        monkeypatch.setattr(module, "__version__", "4.0.1")
    result = definitions.upgrade("my-app")
    assert result["changes"] == [{"key": "gapp", "before": __version__, "after": "4.0.1"}]
    assert fleet()["my-app"]["gapp"] == "4.0.1" and fleet()["my-app"]["settings"] == {"public": True}


def test_an_older_definition_is_shown_but_not_changed_until_upgraded(my_app, monkeypatch):
    for module in (entryfile, definitions):
        monkeypatch.setattr(module, "__version__", "4.0.1")
    assert definitions.show("my-app")["upgrade"] == "available"
    message = refused(definitions.set_value, "public", "true", deployment="my-app")
    assert message.endswith("gapp upgrade --deployment my-app")


def test_a_definition_from_a_newer_gapp_is_neither_shown_changed_nor_upgraded(my_app):
    write_version("my-app", "9.0.0")
    for fn, args in ((definitions.show, ("my-app",)), (definitions.upgrade, ("my-app",)),
                     (definitions.env_set, ("A", "1"))):
        kwargs = {"deployment": "my-app"} if fn is definitions.env_set else {}
        assert "Install gapp 9.0.0 or later" in refused(fn, *args, **kwargs)


def test_a_definition_too_old_to_upgrade_names_the_gapp_to_install_first(my_app):
    write_version("my-app", "3.9.0")
    message = refused(definitions.upgrade, "my-app")
    assert "cannot upgrade from" in message and f"Install gapp {definitions.UPGRADES_FROM}" in message


def test_a_change_to_one_definition_leaves_a_newer_one_beside_it_untouched(my_app):
    definitions.add("theirs", project="my-project", repo="acme/theirs")
    deployments = fleet()
    deployments["theirs"].update({"gapp": "9.0.0", "settings": {"from_the_future": True}})
    fleetstore.write(deployments)
    definitions.set_value("public", "true", deployment="my-app")
    assert fleet()["theirs"]["settings"] == {"from_the_future": True}


# --- a GitHub fleet ---


# --- the commands ---


def test_a_refusal_exits_2_and_a_change_exits_0(my_app):
    assert run("set", "public", "true", "-d", "my-app").exit_code == 0
    result = run("set", "public", "maybe", "-d", "my-app")
    assert result.exit_code == 2 and "true or false" in result.output


def test_the_commands_print_before_and_after(my_app):
    assert run("set", "memory", "1Gi", "--deployment", "my-app").output == "my-app: memory: 512Mi -> 1Gi\n"
    assert run("set", "memory", "1Gi", "-d", "my-app").output == "Nothing to change: memory is already that.\n"


def test_env_set_flags_work_with_and_without_a_value(my_app):
    assert run("env", "set", "APP_KEY", "--secret", "--generate", "-d", "my-app").exit_code == 0
    assert run("env", "set", "AUTH_ISSUER", "--secret", "auth-issuer", "-d", "my-app").exit_code == 0
    assert run("env", "set", "APP_USERS_PATH", "--data-path", "-d", "my-app").exit_code == 0
    env = fleet()["my-app"]["settings"]["env"]
    assert env == {"APP_KEY": {"secret": "app-key", "generate": True},
                   "AUTH_ISSUER": {"secret": "auth-issuer"},
                   "APP_USERS_PATH": {"value": "{{DATA_PATH}}"}}


def test_apply_reads_the_file_and_json_reports_a_refusal(my_app, local_machine):
    file = local_machine / "settings.yaml"
    file.write_text("public: true\n")
    assert run("apply", str(file), "-d", "my-app").output == "my-app: public: false -> true\n"
    result = run("apply", str(local_machine / "missing.yaml"), "-d", "my-app", "--json")
    assert result.exit_code == 2 and json.loads(result.output)["refused"] is True


def test_show_lists_settings_with_where_each_comes_from(my_app):
    run("set", "public", "true", "-d", "my-app")
    output = run("show", "-d", "my-app").output
    assert "    public: true  (definition)\n" in output and "    memory: 512Mi  (default)\n" in output
    shown = json.loads(run("show", "-d", "my-app", "--json").output)
    assert shown["gapp"] == __version__ and shown["upgrade"] is None and "app_json" not in shown
    assert {"key": "public", "value": "true", "from": "definition"} in shown["effective"]


def test_show_gives_the_settings_object_a_file_for_apply_can_start_from(my_app, local_machine):
    run("set", "public", "true", "-d", "my-app")
    run("env", "set", "MAX_USERS", "25", "-d", "my-app")
    shown = json.loads(run("show", "-d", "my-app", "--json").output)
    assert shown["settings"] == fleet()["my-app"]["settings"] == {"public": True, "env": {"MAX_USERS": {"value": "25"}}}
    file = local_machine / "settings.yaml"
    file.write_text(json.dumps(shown["settings"]))
    assert run("apply", str(file), "-d", "my-app").output == "Nothing to change: the settings are already the file's.\n"


# --- the MCP tools ---


def test_the_tools_return_a_refusal_as_data_and_change_nothing(my_app):
    from gapp.admin.mcp import server
    assert server.gapp_set("public", "true", deployment="my-app")["changed"] is True
    result = server.gapp_set("public", "maybe", deployment="my-app")
    assert result == {"refused": True, "message": "'public' is true or false."}
    assert server.gapp_apis("delete", "drive.googleapis.com", deployment="my-app")["refused"] is True


def test_the_apply_tool_takes_the_settings_object_not_a_path(my_app):
    from gapp.admin.mcp import server
    result = server.gapp_apply({"memory": "1Gi", "env": {"A": {"value": "1"}}}, deployment="my-app")
    assert result["changed"] is True
    assert fleet()["my-app"]["settings"] == {"memory": "1Gi", "env": {"A": {"value": "1"}}}
    assert server.gapp_show("my-app")["settings"] == {"memory": "1Gi", "env": {"A": {"value": "1"}}}
    preview = server.gapp_apply({}, deployment="my-app", dry_run=True)
    assert preview["dry_run"] is True and len(preview["changes"]) == 2
    assert fleet()["my-app"]["settings"] == {"memory": "1Gi", "env": {"A": {"value": "1"}}}
