"""What a definition's own fields may hold, and the id a new one gets: the
id keys the deployment's cloud resources, defaults to its name, and is
never shared by two definitions on one project."""

import pytest

from gapp.admin.sdk import fleet
from gapp.admin.sdk.fleet import FleetRefusal


def test_names_repos_and_projects_are_validated():
    assert fleet.validate_name(" my-app ") == "my-app"
    assert fleet.validate_repo(" me/my-app ") == "me/my-app"
    assert fleet.validate_project(" my-proj ") == "my-proj"
    with pytest.raises(FleetRefusal, match="not a valid deployment name"):
        fleet.validate_name("My App")
    with pytest.raises(FleetRefusal, match="owner/name"):
        fleet.validate_repo("my-app")
    with pytest.raises(FleetRefusal, match="GCP project id"):
        fleet.validate_project("P")


def test_id_defaults_to_the_name_and_takes_the_next_free_variant_on_the_same_project():
    doc = {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}}
    assert fleet.allocate_id(doc, "my-app", "my-proj") == "my-app-2"
    doc["my-app-2"] = {"id": "my-app-2", "repo": "me/other", "project": "my-proj"}
    assert fleet.allocate_id(doc, "my-app", "my-proj") == "my-app-3"
    assert fleet.allocate_id(doc, "my-app", "other-proj") == "my-app"   # another project: the name is free there


def test_id_is_at_most_27_characters():
    ok = "a" * 27
    assert fleet.allocate_id({}, ok, "my-proj") == ok
    with pytest.raises(FleetRefusal) as exc:
        fleet.allocate_id({}, "a" * 28, "my-proj")
    assert exc.value.code == "name-too-long" and "28 characters" in exc.value.message and "27" in exc.value.message
    doc = {ok: {"id": ok, "repo": "me/x", "project": "my-proj"}}
    with pytest.raises(FleetRefusal, match="would be"):
        fleet.allocate_id(doc, ok, "my-proj")                          # the -2 variant would be 29
