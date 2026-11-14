"""The fleet lives in exactly one place: a file on this machine by default,
or the GitHub repo set for the profile. `fleetstore` says which, reads it,
and replaces a local one keeping the copy it replaced."""

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import fleetstore
from gapp.admin.sdk.fleetstore import FleetUnavailable

ENTRY = {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}


@pytest.fixture
def local_machine(monkeypatch, tmp_path):
    """A machine with no fleet repo set and its gapp config in a temp dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "gapp"


def test_with_no_fleet_repo_set_the_fleet_is_a_file_under_the_config_dir_for_the_profile(local_machine):
    where = fleetstore.location()
    assert where.kind == "local" and where.repo is None
    assert where.path == local_machine / "fleets" / "default" / "gapp.yaml"


def test_a_local_fleet_with_no_file_yet_is_empty_and_reading_creates_nothing(local_machine):
    assert fleetstore.read() == {}
    assert not (local_machine / "fleets").exists()


def test_a_local_write_reads_back_and_the_first_write_has_no_backup(local_machine):
    assert fleetstore.write_local({"my-app": ENTRY}) is None
    assert fleetstore.read() == {"my-app": ENTRY}


def test_each_local_change_keeps_the_copy_it_replaced(local_machine):
    fleetstore.write_local({"my-app": ENTRY})
    backup = fleetstore.write_local({"my-app": ENTRY, "other": {**ENTRY, "id": "other"}})
    where = fleetstore.location()
    assert backup == where.backup_path and backup.name == "gapp.yaml.bak"
    assert fleetstore.entryfile.parse(backup.read_text())["deployments"] == {"my-app": ENTRY}
    assert sorted(fleetstore.read()) == ["my-app", "other"]
    fleetstore.write_local({})
    assert sorted(fleetstore.entryfile.parse(backup.read_text())["deployments"]) == ["my-app", "other"]


def test_each_profile_has_its_own_local_fleet(local_machine):
    fleetstore.write_local({"my-app": ENTRY})
    cfg = config_mod.load_config()
    cfg["profiles"]["work"] = {"discovery": "on"}
    cfg["active"] = "work"
    config_mod.save_config(cfg)
    assert fleetstore.location().path == local_machine / "fleets" / "work" / "gapp.yaml"
    assert fleetstore.read() == {}


def test_a_local_file_that_is_not_a_fleet_is_an_error_naming_the_backup_never_an_empty_fleet(local_machine):
    fleetstore.write_local({"my-app": ENTRY})
    fleetstore.write_local({"my-app": ENTRY})
    where = fleetstore.location()
    where.path.write_text("- not\n- a mapping\n")
    with pytest.raises(FleetUnavailable) as exc:
        fleetstore.read()
    assert str(where.path) in str(exc.value) and str(where.backup_path) in str(exc.value)


def test_a_document_that_would_not_read_back_is_refused_and_the_fleet_is_untouched(local_machine):
    fleetstore.write_local({"my-app": ENTRY})
    with pytest.raises(fleetstore.FleetRefusal):
        fleetstore.write_local({"my-app": "not an entry"})
    assert fleetstore.read() == {"my-app": ENTRY}
    assert not fleetstore.location().backup_path.exists()


def test_cli_fleet_says_where_a_local_fleet_is_and_what_it_holds(local_machine):
    out = CliRunner().invoke(main, ["fleet"]).output
    assert out == (f"Your fleet is kept in a file on this machine ({fleetstore.local_path()}).\n"
                   "It holds no deployments yet.\n")
    fleetstore.write_local({"my-app": ENTRY})
    fleetstore.write_local({"my-app": ENTRY})
    out = CliRunner().invoke(main, ["fleet"]).output
    assert "It holds 1 deployment.\n" in out
    assert f"The copy from before its last change: {fleetstore.location().backup_path}\n" in out


def test_the_mcp_tool_returns_the_same_description(local_machine):
    from gapp.admin.mcp import server
    assert server.gapp_fleet() == fleetstore.describe_fleet()
    assert server.gapp_fleet()["kind"] == "local" and server.gapp_fleet()["deployments"] == 0
