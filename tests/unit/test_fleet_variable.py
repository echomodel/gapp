"""Naming the fleet: the forms `gapp fleet use` and GAPP_FLEET share, and
the backups a fleet kept in a file leaves in gapp's state folder."""

import os
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import definitions, fleetstore
from gapp.admin.sdk.fleet import FleetRefusal
from gapp.admin.sdk.fleetstore import FleetUnavailable


def run(*args):
    return CliRunner().invoke(main, list(args))


# --- the forms ---


@pytest.mark.parametrize("value, kind, repo", [
    ("acme/fleet", "github", "acme/fleet"),
    ("https://github.com/acme/fleet", "github", "acme/fleet"),
    ("git.example.com/team/fleet", "github", "git.example.com/team/fleet"),
    ("git@github.com:acme/fleet.git", "github", "git@github.com:acme/fleet.git"),
    ("/srv/fleet.git", "github", "/srv/fleet.git"),
])
def test_anything_but_a_fleet_file_or_local_is_a_git_repo(value, kind, repo):
    where = fleetstore.target(value)
    assert (where.kind, where.repo, where.path) == (kind, repo, None)


def test_a_full_path_to_a_repo_may_start_with_a_tilde():
    assert fleetstore.target("~/fleet").repo == str(Path.home() / "fleet")


@pytest.mark.parametrize("value", ["/srv/ops/gapp.yaml", "/srv/ops/fleet.yml", "/srv/ops/FLEET.YAML"])
def test_a_full_path_ending_in_yaml_is_a_fleet_file(value):
    where = fleetstore.target(value)
    assert (where.kind, where.path, where.named) == ("local", Path(value).resolve(), True)


def test_a_tilde_fleet_file_is_expanded():
    assert fleetstore.target("~/ops/gapp.yaml").path == (Path.home() / "ops" / "gapp.yaml").resolve()


def test_local_is_the_profiles_own_file():
    where = fleetstore.target("local")
    assert (where.kind, where.path, where.named) == ("local", fleetstore.local_path(), False)


@pytest.mark.parametrize("value", ["gapp.yaml", "ops/gapp.yaml", "./gapp.yaml", "./fleet", "../fleet", "."])
def test_a_relative_path_is_refused(value):
    with pytest.raises(FleetRefusal) as exc:
        fleetstore.target(value)
    assert "relative path" in exc.value.message and "$PWD/" in exc.value.message


def test_nothing_is_refused():
    with pytest.raises(FleetRefusal):
        fleetstore.target("  ")


# --- GAPP_FLEET ---


def test_a_shell_variable_never_reaches_a_test():
    assert "GAPP_FLEET" not in os.environ


def test_the_variable_names_a_repo_over_the_profile(monkeypatch):
    monkeypatch.setenv("GAPP_FLEET", "acme/fleet")
    where = fleetstore.location()
    assert (where.kind, where.repo, where.set_by) == ("github", "acme/fleet", "GAPP_FLEET")
    assert where.describe() == "acme/fleet (gapp.yaml), set by GAPP_FLEET"


def test_the_variable_can_name_a_fresh_file_and_the_first_add_creates_it(monkeypatch, tmp_path):
    file = tmp_path / "try" / "gapp.yaml"
    monkeypatch.setenv("GAPP_FLEET", str(file))
    assert fleetstore.read() == {}
    assert run("fleet").output.startswith(f"Your fleet is kept in the file {file} (no file yet), set by GAPP_FLEET.\n")
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    assert list(yaml.safe_load(file.read_text())["deployments"]) == ["my-app"]
    assert not fleetstore.local_path().exists()                   # the profile's own fleet is untouched


def test_local_in_the_variable_wins_over_a_profile_that_points_at_a_repo(monkeypatch):
    config_mod.set_fleet(repo="acme/fleet")
    monkeypatch.setenv("GAPP_FLEET", "local")
    assert fleetstore.location().path == fleetstore.local_path()


def test_a_variable_gapp_cannot_use_fails_every_read_and_write_and_never_falls_back(monkeypatch):
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    monkeypatch.setenv("GAPP_FLEET", "ops/gapp.yaml")
    with pytest.raises(FleetUnavailable) as exc:
        fleetstore.read()
    assert "GAPP_FLEET is set to 'ops/gapp.yaml'" in str(exc.value) and "relative path" in str(exc.value)
    with pytest.raises(FleetUnavailable):
        fleetstore.write({})
    result = run("list")
    assert result.exit_code != 0 and "GAPP_FLEET" in result.output


def test_fleet_use_is_refused_while_the_variable_is_set(monkeypatch):
    monkeypatch.setenv("GAPP_FLEET", "acme/fleet")
    result = run("fleet", "use", "local")
    assert result.exit_code == 2 and "Unset GAPP_FLEET" in result.output
    assert config_mod.get_active_config().get("fleet_repo") is None


def test_profile_list_says_the_variable_names_the_fleet(monkeypatch):
    monkeypatch.setenv("GAPP_FLEET", "acme/fleet")
    out = run("config", "profile", "list").output
    assert "GAPP_FLEET is set to acme/fleet: it names the fleet, whatever the active profile says.\n" in out


# --- fleet use with a file ---


def test_fleet_use_moves_the_fleet_into_a_file_and_back(tmp_path):
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    own = fleetstore.local_path()
    file = tmp_path / "ops" / "gapp.yaml"
    result = fleetstore.use(str(file))
    assert result["moved"] and result["carried"] and result["where"] == f"the file {file}"
    assert config_mod.get_active_config().get("fleet_file") == str(file)
    assert list(fleetstore.read()) == ["my-app"] and not own.exists()
    assert "my-app" in Path(result["backup"]).read_text()
    back = fleetstore.use("local")
    assert back["moved"] and list(fleetstore.read()) == ["my-app"] and not file.exists()
    assert config_mod.get_active_config().get("fleet_file") is None


def test_create_takes_only_a_repo_on_github(tmp_path):
    with pytest.raises(FleetRefusal):
        fleetstore.use(str(tmp_path / "gapp.yaml"), create=True)


def test_a_profile_whose_fleet_is_a_named_file_can_be_removed(tmp_path):
    config_mod.add_profile("work")
    config_mod.use_profile("work")
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    fleetstore.use(str(tmp_path / "gapp.yaml"))
    config_mod.use_profile("default")
    assert config_mod.remove_profile("work")["changed"]
    assert (tmp_path / "gapp.yaml").exists()                      # the file is the operator's; it stays


# --- backups ---


def test_backups_live_in_the_state_folder_one_per_change_and_never_beside_the_fleet(tmp_path):
    for n in range(3):
        definitions.add(f"app-{n}", project="my-project", repo=f"acme/app-{n}")
    backups = sorted(fleetstore.backups_dir().glob("*.yaml"))
    assert len(backups) == 2                                      # the first write replaced nothing
    assert fleetstore.backups_dir() == tmp_path / "state" / "gapp" / "backups"
    assert [p.name for p in fleetstore.local_path().parent.iterdir()] == ["gapp.yaml"]


def test_a_backup_says_which_fleet_and_when_and_import_takes_it_as_it_is():
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    definitions.remove("my-app")
    backup = fleetstore.latest_backup(fleetstore.location())
    head = backup.read_text().splitlines()[:3]
    assert head[0] == "# gapp backup" and head[1] == f"# fleet: {fleetstore.local_path()}"
    assert head[2].startswith("# taken: ") and head[2].endswith("before: remove my-app")
    fleetstore.import_text(backup.read_text(), str(backup))
    assert list(fleetstore.read()) == ["my-app"]


def test_the_newest_backup_is_found_for_its_own_fleet_only(tmp_path, monkeypatch):
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    definitions.add("other", project="my-project", repo="acme/other")
    mine = fleetstore.latest_backup(fleetstore.location())
    monkeypatch.setenv("GAPP_FLEET", str(tmp_path / "elsewhere.yaml"))
    assert fleetstore.latest_backup(fleetstore.location()) is None
    definitions.add("third", project="my-project", repo="acme/third")
    definitions.add("fourth", project="my-project", repo="acme/fourth")
    assert fleetstore.latest_backup(fleetstore.location()) not in (None, mine)
    monkeypatch.delenv("GAPP_FLEET")
    assert fleetstore.latest_backup(fleetstore.location()) == mine
