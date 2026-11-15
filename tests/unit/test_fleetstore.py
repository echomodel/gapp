"""The fleet lives in exactly one place: a file on this machine by default,
or the GitHub repo set for the profile. `fleetstore` says which, reads it,
and replaces a local one keeping the copy it replaced."""

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import config as config_mod
from gapp.admin.sdk import fleetstore
from gapp.admin.sdk.fleet import FleetRefusal
from gapp.admin.sdk.fleetstore import FleetUnavailable

ENTRY = {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}
DEFINED = {**ENTRY, "gapp": "4.0.0", "settings": {}}


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
    assert fleetstore.write({"my-app": ENTRY}) is None
    assert fleetstore.read() == {"my-app": ENTRY}


def test_each_local_change_keeps_the_copy_it_replaced(local_machine):
    fleetstore.write({"my-app": ENTRY})
    backup = fleetstore.write({"my-app": ENTRY, "other": {**ENTRY, "id": "other"}})
    where = fleetstore.location()
    assert backup.parent == fleetstore.backups_dir() and fleetstore.latest_backup(where) == backup
    assert fleetstore.entryfile.parse(backup.read_text())["deployments"] == {"my-app": ENTRY}
    assert sorted(fleetstore.read()) == ["my-app", "other"]
    later = fleetstore.write({})
    assert later != backup and fleetstore.latest_backup(where) == later
    assert sorted(fleetstore.entryfile.parse(later.read_text())["deployments"]) == ["my-app", "other"]
    assert fleetstore.entryfile.parse(backup.read_text())["deployments"] == {"my-app": ENTRY}   # never overwritten
    assert sorted(p.name for p in where.path.parent.iterdir()) == ["gapp.yaml"]               # nothing beside the fleet


def test_each_profile_has_its_own_local_fleet(local_machine):
    fleetstore.write({"my-app": ENTRY})
    cfg = config_mod.load_config()
    cfg["profiles"]["work"] = {"discovery": "on"}
    cfg["active"] = "work"
    config_mod.save_config(cfg)
    assert fleetstore.location().path == local_machine / "fleets" / "work" / "gapp.yaml"
    assert fleetstore.read() == {}


def test_a_local_file_that_is_not_a_fleet_is_an_error_naming_the_backup_never_an_empty_fleet(local_machine):
    fleetstore.write({"my-app": ENTRY})
    fleetstore.write({"my-app": ENTRY})
    where = fleetstore.location()
    where.path.write_text("- not\n- a mapping\n")
    with pytest.raises(FleetUnavailable) as exc:
        fleetstore.read()
    assert str(where.path) in str(exc.value) and str(fleetstore.latest_backup(where)) in str(exc.value)


def test_a_document_that_would_not_read_back_is_refused_and_the_fleet_is_untouched(local_machine):
    fleetstore.write({"my-app": ENTRY})
    with pytest.raises(fleetstore.FleetRefusal):
        fleetstore.write({"my-app": "not an entry"})
    assert fleetstore.read() == {"my-app": ENTRY}
    assert fleetstore.latest_backup(fleetstore.location()) is None


def test_cli_fleet_says_where_a_local_fleet_is_and_what_it_holds(local_machine):
    out = CliRunner().invoke(main, ["fleet"]).output
    assert out == (f"Your fleet is kept in a file on this machine ({fleetstore.local_path()}).\n"
                   "It holds no deployments yet.\n")
    fleetstore.write({"my-app": ENTRY})
    fleetstore.write({"my-app": ENTRY})
    out = CliRunner().invoke(main, ["fleet"]).output
    assert "It holds 1 deployment.\n" in out
    assert f"The copy from before its last change: {fleetstore.latest_backup(fleetstore.location())}\n" in out


def test_the_mcp_tool_returns_the_same_description(local_machine):
    from gapp.admin.mcp import server
    assert server.gapp_fleet() == fleetstore.describe_fleet()
    assert server.gapp_fleet()["kind"] == "local" and server.gapp_fleet()["deployments"] == 0


# -- copying the fleet out and in --


def test_an_export_is_the_whole_fleet_file_and_an_import_of_it_changes_nothing(local_machine):
    fleetstore.write({"my-app": DEFINED})
    text = fleetstore.export_text()
    assert yaml.safe_load(text) == {"format": 1, "deployments": {"my-app": DEFINED}}
    result = fleetstore.import_text(text, "copy.yaml")
    assert (result["deployments"], result["replaced"]) == (1, 1) and fleetstore.read() == {"my-app": DEFINED}


def test_an_export_of_an_empty_fleet_is_an_empty_fleet_file(local_machine):
    assert yaml.safe_load(fleetstore.export_text()) == {"format": 1, "deployments": {}}


def test_an_import_replaces_the_fleet_and_keeps_the_one_it_replaced_as_the_backup(local_machine):
    fleetstore.write({"my-app": DEFINED})
    other = {**DEFINED, "id": "other"}
    result = fleetstore.import_text(yaml.safe_dump({"format": 1, "deployments": {"other": other}}), "copy.yaml")
    assert fleetstore.read() == {"other": other}
    backup = fleetstore.latest_backup(fleetstore.location())
    assert result["backup"] == str(backup) and "my-app" in yaml.safe_load(backup.read_text())["deployments"]


def test_an_import_takes_the_files_other_top_level_keys(local_machine):
    fleetstore.write({"my-app": DEFINED})
    fleetstore.import_text("format: 1\ndeployments: {}\nsomething_later: 7\n", "copy.yaml")
    assert yaml.safe_load(fleetstore.location().path.read_text()) == {"format": 1, "deployments": {}, "something_later": 7}


@pytest.mark.parametrize("text, says", [
    ("", "is empty, so nothing was imported"),
    ("deployments: {}\n", "has no 'format' at its top"),
    ("format: 2\ndeployments: {}\n", "is in format 2"),
    ("format: 1\ndeployments:\n  My App: {id: x, project: my-proj, gapp: 4.0.0}\n", "not a valid deployment name"),
    ("format: 1\ndeployments:\n  my-app: {project: my-proj, gapp: 4.0.0}\n", "it has no 'id'"),
    ("format: 1\ndeployments:\n  my-app: {id: my-app, project: my-proj}\n", "it has no 'gapp'"),
    ("format: 1\ndeployments:\n  my-app: {id: my-app, project: my-proj, gapp: main}\n", "is not a gapp version"),
    ("format: 1\ndeployments:\n  my-app: {id: my-app, project: my-proj, gapp: 4.0.0, settings: {frob: 1}}\n",
     "'frob' is not a setting"),
    ("format: 1\ndeployments:\n  a: {id: x, project: my-proj, gapp: 4.0.0}\n  b: {id: x, project: my-proj, gapp: 4.0.0}\n",
     "have the same id, 'x', on my-proj"),
])
def test_an_import_with_anything_wrong_changes_nothing(local_machine, text, says):
    fleetstore.write({"my-app": DEFINED})
    before = fleetstore.location().path.read_text()
    with pytest.raises(FleetRefusal) as caught:
        fleetstore.import_text(text, "copy.yaml")
    assert says in caught.value.message
    assert fleetstore.location().path.read_text() == before


def test_the_export_and_import_commands(local_machine, tmp_path):
    from click.testing import CliRunner
    from gapp.admin.cli.main import main
    fleetstore.write({"my-app": DEFINED})
    copy = tmp_path / "copy.yaml"
    printed = CliRunner().invoke(main, ["fleet", "export"])
    assert yaml.safe_load(printed.output) == {"format": 1, "deployments": {"my-app": DEFINED}}
    assert CliRunner().invoke(main, ["fleet", "export", str(copy)]).output == f"Copied your fleet to {copy}: 1 deployment.\n"
    again = CliRunner().invoke(main, ["fleet", "export", str(copy)])
    assert again.exit_code == 2 and "gapp does not write over it" in again.output
    fleetstore.write({})
    result = CliRunner().invoke(main, ["fleet", "import", str(copy)])
    assert result.exit_code == 0 and "now holds 1 deployment (it held 0)." in result.output
    assert "The fleet it replaced is kept as " in result.output
    missing = CliRunner().invoke(main, ["fleet", "import", str(tmp_path / "nope.yaml")])
    assert missing.exit_code == 2 and "is not a file" in missing.output


def test_the_export_and_import_tools_take_and_return_the_document_not_a_path(local_machine):
    from gapp.admin.mcp import server
    fleetstore.write({"my-app": DEFINED})
    exported = server.gapp_fleet_export()
    assert exported == {"fleet": {"format": 1, "deployments": {"my-app": DEFINED}}}
    exported["fleet"]["deployments"]["my-app"]["ref"] = "main"
    assert server.gapp_fleet_import(exported["fleet"])["deployments"] == 1
    assert fleetstore.read()["my-app"]["ref"] == "main"
    assert server.gapp_fleet_import({"deployments": {}})["refused"] is True
