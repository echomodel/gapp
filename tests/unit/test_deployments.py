"""`gapp deployments list` (the fleet, with what each deployment's last
deploy was) and `gapp deployments scan` (gapp's projects, with what the
fleet does not hold). Both only report, and bound what they read."""

import json

import pytest
import yaml
from click.testing import CliRunner

from gapp import __version__
from gapp.admin.cli.main import main
from gapp.admin.sdk import definitions, deployments, fleetstore, versions
from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.definitions import Refused


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    """A local fleet and the stand-in cloud every command reads."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    provider = DummyCloudProvider()
    monkeypatch.setattr("gapp.admin.sdk.cloud.get_provider", lambda: provider)
    return provider


def project(cloud, name):
    """A project set up for gapp: marked, with its gapp bucket."""
    cloud.project_labels[name] = {"gapp": "hosted"}
    cloud.buckets[f"gapp-{name}"] = {"project": name, "labels": {}}


def deployed(cloud, project_id, deployment_id, version=__version__, name=None, repo=None):
    definition = {"id": deployment_id, "project": project_id, "gapp": version, "settings": {}}
    if repo:
        definition["repo"] = repo
    cloud.objects[(f"gapp-{project_id}", f"deployments/{deployment_id}/deployed.yaml")] = yaml.safe_dump(
        {"gapp": version, "deployment": name or deployment_id, "deployed_at": "2026-11-15T10:00:00Z",
         "definition": definition})


def on(name, version):
    whole = fleetstore.read()
    whole[name]["gapp"] = version
    fleetstore.write(whole)


def rows(result):
    return {r["deployment"]: r for r in result["deployments"]}


# --- list ---


@pytest.fixture
def fleet(cloud):
    project(cloud, "proj-a")
    project(cloud, "proj-b")
    for name, proj in (("alpha", "proj-a"), ("beta", "proj-a"), ("gamma", "proj-b")):
        definitions.add(name, project=proj, repo=f"acme/{name}")
    deployed(cloud, "proj-a", "alpha")
    deployed(cloud, "proj-b", "gamma", version="4.0.0rc15")
    return cloud


def test_list_shows_both_versions_and_where_each_stands(fleet):
    on("gamma", "4.0.0rc15")
    result = rows(deployments.list_deployments(fleet))
    assert (result["alpha"]["definition"], result["alpha"]["deployed"], result["alpha"]["stands"]) == (
        __version__, __version__, "current")
    assert result["beta"]["deployed"] is None and result["beta"]["stands"] == "current"
    assert result["gamma"]["stands"] == "upgrade available"


def test_a_definition_upgraded_and_not_deployed_is_an_upgrade_pending(fleet):
    result = rows(deployments.list_deployments(fleet))
    assert (result["gamma"]["definition"], result["gamma"]["deployed"], result["gamma"]["stands"]) == (
        __version__, "4.0.0rc15", "upgrade pending")


def test_infrastructure_below_compatible_from_is_upgrade_required_whatever_the_definition_says(fleet):
    deployed(fleet, "proj-a", "alpha", version="4.0.0rc13")
    assert rows(deployments.list_deployments(fleet))["alpha"]["stands"] == "upgrade required"


def test_filters_on_the_fleet_narrow_before_anything_is_read(fleet, monkeypatch):
    read = []
    real = fleet.read_object
    monkeypatch.setattr(fleet, "read_object", lambda b, p: read.append(p) or real(b, p))
    result = deployments.list_deployments(fleet, project="proj-a", name="a*")
    assert list(rows(result)) == ["alpha"] and read == ["deployments/alpha/deployed.yaml"]
    assert list(rows(deployments.list_deployments(fleet, repo="ACME/gamma"))) == ["gamma"]


def test_limit_caps_the_reads_in_name_order_and_says_so(fleet):
    result = deployments.list_deployments(fleet, limit=2)
    assert list(rows(result)) == ["alpha", "beta"] and result["read"] == 2 and result["limited"] is True
    assert "Read the first 2 by name (--limit)" in CliRunner().invoke(main, ["deployments", "list", "--limit", "2"]).output


def test_below_keeps_what_a_newer_gapp_would_leave_behind(fleet):
    on("gamma", "4.0.0rc15")
    assert list(rows(deployments.list_deployments(fleet, below="4.0.0rc16"))) == ["gamma"]


def test_bad_options_are_refused_before_anything_is_read(fleet):
    with pytest.raises(Refused):
        deployments.list_deployments(fleet, below="latest")
    with pytest.raises(Refused):
        deployments.list_deployments(fleet, limit=0)


def test_a_project_that_cannot_be_read_is_reported_on_its_rows_and_the_rest_still_come(fleet):
    fleet.unreadable_projects.add("proj-b")
    result = rows(deployments.list_deployments(fleet))
    assert result["gamma"]["deployed"] is None and "permission denied" in result["gamma"]["problem"]
    assert result["gamma"]["stands"] == "couldn't read its deploy record"
    assert result["alpha"]["stands"] == "current"


def test_the_list_command_prints_a_row_per_deployment_and_json(fleet):
    out = CliRunner().invoke(main, ["deployments", "list"]).output
    assert f"definition {__version__}  deployed not deployed  current" in out
    data = json.loads(CliRunner().invoke(main, ["deployments", "list", "--json"]).output)
    assert [r["deployment"] for r in data["deployments"]] == ["alpha", "beta", "gamma"]


# --- scan ---


def test_scan_finds_gapps_projects_and_says_what_the_fleet_does_not_hold(cloud):
    project(cloud, "proj-a")
    project(cloud, "proj-b")
    cloud.project_labels["not-gapp"] = {"team": "x"}
    definitions.add("alpha", project="proj-a", repo="acme/alpha")
    deployed(cloud, "proj-a", "alpha")
    deployed(cloud, "proj-b", "lost", repo="acme/lost", name="old-name")
    result = deployments.scan(cloud)
    assert [p["project"] for p in result["projects"]] == ["proj-a", "proj-b"]
    found = {d["id"]: d for p in result["projects"] for d in p["deployments"]}
    assert found["alpha"]["in_fleet"] == "alpha" and found["alpha"]["add_again"] is None
    assert found["lost"]["in_fleet"] is None
    assert found["lost"]["add_again"] == ["gapp add lost --project proj-b --repo acme/lost",
                                         "gapp rename lost old-name"]
    assert (result["found"], result["not_in_fleet"]) == (2, 1)


def test_a_scan_adds_back_what_it_finds(cloud):
    project(cloud, "proj-b")
    deployed(cloud, "proj-b", "lost", repo="acme/lost")
    command = deployments.scan(cloud)["projects"][0]["deployments"][0]["add_again"][0]
    CliRunner().invoke(main, command.split()[1:])
    assert deployments.scan(cloud)["not_in_fleet"] == 0


def test_scan_of_one_project_reads_only_that_one(cloud):
    project(cloud, "proj-a")
    project(cloud, "proj-b")
    deployed(cloud, "proj-b", "lost")
    result = deployments.scan(cloud, project="proj-a")
    assert [p["project"] for p in result["projects"]] == ["proj-a"] and result["found"] == 0


def test_scan_refuses_more_projects_than_it_reads_at_once(cloud, monkeypatch):
    monkeypatch.setattr(deployments, "SCAN_MAX_PROJECTS", 1)
    project(cloud, "proj-a")
    project(cloud, "proj-b")
    with pytest.raises(Refused) as caught:
        deployments.scan(cloud)
    assert "gapp deployments scan --project <project>" in caught.value.message


def test_a_project_that_cannot_be_read_is_reported_and_the_rest_still_come(cloud):
    project(cloud, "proj-a")
    project(cloud, "proj-b")
    deployed(cloud, "proj-a", "alpha")
    cloud.unreadable_projects.add("proj-b")
    result = {p["project"]: p for p in deployments.scan(cloud)["projects"]}
    assert "permission denied" in result["proj-b"]["problem"] and result["proj-a"]["deployments"]


def test_the_scan_command_prints_the_commands_to_add_back(cloud):
    project(cloud, "proj-b")
    deployed(cloud, "proj-b", "lost", repo="acme/lost")
    out = CliRunner().invoke(main, ["deployments", "scan"]).output
    assert "  lost  gapp " in out and "not in your fleet. Add it back with:\n      gapp add lost --project proj-b" in out
    assert "1 found, 1 not in your fleet" in out


def test_the_mcp_tools_return_the_same_results(cloud):
    from gapp.admin.mcp import server
    project(cloud, "proj-b")
    deployed(cloud, "proj-b", "lost")
    assert server.gapp_deployments_scan() == deployments.scan(cloud)
    assert server.gapp_deployments_list() == deployments.list_deployments(cloud)
