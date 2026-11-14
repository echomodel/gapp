"""This machine's config: profiles (separate fleets and logins) and the
Google Cloud login the active one uses. A refusal changes nothing and
exits 2."""

import json

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import config, definitions, fleetstore
from gapp.admin.sdk.config import ConfigRefusal

LOGIN = "@".join(["me", "example.com"])


@pytest.fixture(autouse=True)
def machine(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args):
    return CliRunner().invoke(main, ["config", *args])


def refused(fn, *args):
    before = config.load_config()
    with pytest.raises(ConfigRefusal) as caught:
        fn(*args)
    assert config.load_config() == before, "a refusal changed the config"
    return str(caught.value)


# -- the file --


def test_with_no_file_there_is_one_profile_and_it_is_active():
    assert config.load_config() == {"active": "default", "profiles": {"default": {}}}
    assert not config.get_config_file().exists()                   # reading writes nothing


def test_the_file_holds_only_what_is_set():
    config.account(LOGIN)
    config.set_fleet_repo("acme/fleet")
    assert yaml.safe_load(config.get_config_file().read_text()) == {
        "active": "default", "profiles": {"default": {"account": LOGIN, "fleet_repo": "acme/fleet"}}}
    config.set_fleet_repo(None)
    assert yaml.safe_load(config.get_config_file().read_text())["profiles"] == {"default": {"account": LOGIN}}


def test_a_key_gapp_does_not_know_is_left_out_of_what_it_reads_and_next_writes():
    path = config.get_config_file()
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"active": "work", "unknown": 1,
                                    "profiles": {"work": {"account": LOGIN, "something_else": "x"}}}))
    assert config.load_config() == {"active": "work", "profiles": {"work": {"account": LOGIN}}}
    config.add_profile("play")
    assert yaml.safe_load(path.read_text()) == {"active": "work", "profiles": {"work": {"account": LOGIN}, "play": {}}}


def test_a_file_that_cannot_be_read_is_an_error_never_an_empty_config():
    path = config.get_config_file()
    path.parent.mkdir(parents=True)
    path.write_text("profiles: [\n")
    with pytest.raises(config.ConfigError, match="could not be read"):
        config.load_config()
    assert run("profile", "list").exit_code == 1


# -- the account --


def test_the_account_is_shown_and_set_for_the_active_profile_only():
    assert config.account() == {"profile": "default", "account": None, "changed": False}
    assert config.account(" Me@Example.com ") == {"profile": "default", "account": LOGIN, "changed": True}
    config.add_profile("work")
    config.use_profile("work")
    assert config.account()["account"] is None
    config.use_profile("default")
    assert config.account()["account"] == LOGIN


def test_gcloud_runs_as_the_active_profiles_account():
    from gapp.admin.sdk.util import configured_account
    assert configured_account() is None
    config.account(LOGIN)
    assert configured_account() == LOGIN


def test_what_is_not_an_email_is_refused():
    assert "is not an email address" in refused(config.account, "me")


# -- profiles --


def test_a_new_profile_is_empty_and_is_not_switched_to():
    assert config.add_profile("work") == {"profile": "work", "changed": True}
    assert [(p["name"], p["active"]) for p in config.profiles()["profiles"]] == [("default", True), ("work", False)]


def test_add_refuses_a_name_that_exists_or_is_not_a_name():
    assert "already a profile 'default'" in refused(config.add_profile, "default")
    assert "is not a profile name" in refused(config.add_profile, "My Work")


def test_use_switches_and_is_refused_for_a_profile_that_does_not_exist():
    config.add_profile("work")
    assert config.use_profile("work") == {"profile": "work", "changed": True}
    assert config.use_profile("work")["changed"] is False
    message = refused(config.use_profile, "nope")
    assert message == "There is no profile 'nope'. You have: default, work. Make one: gapp config profile add nope"


def test_each_profile_has_its_own_fleet():
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    config.add_profile("work")
    config.use_profile("work")
    assert fleetstore.read() == {}
    definitions.add("other", project="work-project", repo="acme/other")
    config.use_profile("default")
    assert list(fleetstore.read()) == ["my-app"]


def test_the_active_profile_cannot_be_removed():
    assert "'default' is the active profile" in refused(config.remove_profile, "default")


def test_a_profile_whose_fleet_on_this_machine_holds_deployments_cannot_be_removed():
    config.add_profile("work")
    config.use_profile("work")
    definitions.add("other", project="work-project", repo="acme/other")
    config.use_profile("default")
    message = refused(config.remove_profile, "work")
    assert "it holds 1 deployment." in message
    assert message.endswith("gapp config profile use work, then gapp fleet use <owner>/<repo>")
    assert fleetstore.local_path("work").exists()


def test_removing_a_profile_whose_fleet_is_in_a_repo_leaves_the_repo_alone(monkeypatch):
    from gapp.admin.sdk import fleetrepo
    monkeypatch.setattr(fleetrepo, "read_text", lambda repo: pytest.fail("the fleet repo was read"))
    config.add_profile("work")
    config.use_profile("work")
    config.set_fleet_repo("acme/fleet")
    config.use_profile("default")
    assert config.remove_profile("work") == {"profile": "work", "changed": True}
    assert [p["name"] for p in config.profiles()["profiles"]] == ["default"]


def test_an_empty_profile_is_removed():
    config.add_profile("work")
    config.remove_profile("work")
    assert "There is no profile 'work'" in refused(config.remove_profile, "work")


def test_rename_takes_the_login_the_fleet_and_the_active_mark_with_it():
    config.account(LOGIN)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    assert config.rename_profile("default", "home") == {"profile": "home", "was": "default", "changed": True}
    assert config.get_active_profile() == "home" and config.account()["account"] == LOGIN
    assert list(fleetstore.read()) == ["my-app"]
    assert not fleetstore.local_path("default").exists()


def test_rename_refuses_a_missing_profile_a_taken_name_and_a_bad_one():
    config.add_profile("work")
    assert "There is no profile 'nope'" in refused(config.rename_profile, "nope", "x")
    assert "already a profile 'work'" in refused(config.rename_profile, "default", "work")
    assert "is not a profile name" in refused(config.rename_profile, "default", "Home!")


# -- the commands --


def test_the_commands_print_one_line_each():
    assert run("account").output == "No login is set in profile 'default': gapp runs gcloud as its active login.\n"
    assert run("account", LOGIN).output == f"gapp runs gcloud as {LOGIN} in profile 'default'.\n"
    assert run("profile", "add", "work").output == "Made profile 'work'.\nNext: gapp config profile use work\n"
    assert run("profile", "list").output == "* default\n  work\n"
    assert run("profile", "use", "work").output == "Profile 'work' is active.\n"
    assert run("profile", "rename", "default", "home").output == "Profile 'default' is now 'home'.\n"
    assert run("profile", "remove", "home").output == "Removed profile 'home'.\n"


def test_a_refusal_exits_2_and_json_carries_it():
    result = run("profile", "use", "nope")
    assert result.exit_code == 2 and "There is no profile 'nope'" in result.output
    result = run("profile", "use", "nope", "--json")
    assert result.exit_code == 2 and json.loads(result.output)["refused"] is True
    listed = json.loads(run("profile", "list", "--json").output)
    assert listed == {"active": "default", "profiles": [
        {"name": "default", "active": True, "account": None, "fleet_repo": None}]}
