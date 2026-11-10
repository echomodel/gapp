"""The fleet file's pure operations: one entry per deployment, keyed by the
operator's name; `id` keys the GCP resources and is never copied; every key
but id, repo and project is a gapp.yaml key the deploy merges over the
repo's file.
"""

import pytest
import yaml

from gapp.admin.sdk import fleet
from gapp.admin.sdk.fleet import FleetRefusal


def _doc(**entries):
    return {name: dict(entry) for name, entry in entries.items()}


# -- the file --


def test_empty_file_is_an_empty_fleet_and_dumps_with_its_header():
    assert fleet.parse(fleet.DEPLOYMENTS_FILE_INITIAL) == {}
    assert fleet.parse("") == {}
    text = fleet.dump({})
    assert text.startswith("# Deployments of this fleet.") and text.endswith("{}\n")
    assert fleet.parse(text) == {}


def test_dump_orders_entries_by_name_with_own_fields_first():
    doc, _, _ = fleet.set_entry({}, "zeta", repo="me/zeta", project="my-proj")
    doc, _, _ = fleet.set_entry(doc, "alpha", repo="me/alpha", project="my-proj", changes={"domain": "a.example.com"})
    text = fleet.dump(doc)
    body = yaml.safe_load(text)
    assert list(body) == ["alpha", "zeta"]
    assert list(body["alpha"]) == ["id", "repo", "project", "domain"]
    assert fleet.parse(text) == doc


def test_a_file_that_is_not_a_mapping_is_refused():
    with pytest.raises(FleetRefusal) as exc:
        fleet.parse("- just\n- a list\n")
    assert exc.value.code == "bad-file"


# -- set: add and update --


def test_set_adds_an_entry_with_the_name_as_id():
    doc, action, entry = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    assert action == "added"
    assert entry == {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}
    assert doc == {"my-app": entry}


def test_set_again_with_the_same_values_is_unchanged_and_with_a_key_is_an_update():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    doc, action, _ = fleet.set_entry(doc, "my-app", repo="me/my-app", project="my-proj")
    assert action == "unchanged"
    doc, action, entry = fleet.set_entry(doc, "my-app", repo="me/my-app", project="my-proj",
                                         changes={"domain": "mcp.example.com", "env": [{"name": "LOG_LEVEL", "value": "INFO"}]})
    assert action == "updated"
    assert entry["domain"] == "mcp.example.com" and entry["env"] == [{"name": "LOG_LEVEL", "value": "INFO"}]
    assert entry["id"] == "my-app"                                  # an update never changes the id
    doc, action, entry = fleet.set_entry(doc, "my-app", repo="me/my-app", project="my-proj", changes={"domain": None})
    assert action == "updated" and "domain" not in entry


def test_set_refuses_the_same_name_for_another_repo_or_project():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    with pytest.raises(FleetRefusal) as exc:
        fleet.set_entry(doc, "my-app", repo="me/other", project="my-proj")
    assert exc.value.code == "name-taken" and "gapp ci rename my-app <new-name>" in exc.value.message
    with pytest.raises(FleetRefusal) as exc:
        fleet.set_entry(doc, "my-app", repo="me/my-app", project="other-proj")
    assert exc.value.code == "project-bound"
    assert "gapp ci setup <new-name> --copy-of my-app --project other-proj" in exc.value.message


def test_set_refuses_a_repo_already_deployed_under_another_name_and_names_both_ways_on():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    with pytest.raises(FleetRefusal) as exc:
        fleet.set_entry(doc, "my-app-staging", repo="me/my-app", project="staging-proj")
    assert exc.value.code == "repo-deployed"
    assert "me/my-app is already deployed as my-app" in exc.value.message
    assert "Update it: gapp ci setup my-app --repo me/my-app --project my-proj" in exc.value.message
    assert "Add another: gapp ci setup my-app-staging --copy-of my-app" in exc.value.message


def test_set_refuses_own_fields_and_name_as_changes():
    for key in ("id", "repo", "project", "name"):
        with pytest.raises(FleetRefusal) as exc:
            fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj", changes={key: "x"})
        assert exc.value.code == "invalid-input"


def test_set_validates_name_repo_and_project():
    with pytest.raises(FleetRefusal, match="not a valid deployment name"):
        fleet.set_entry({}, "My App", repo="me/my-app", project="my-proj")
    with pytest.raises(FleetRefusal, match="owner/name"):
        fleet.set_entry({}, "my-app", repo="my-app", project="my-proj")
    with pytest.raises(FleetRefusal, match="GCP project id"):
        fleet.set_entry({}, "my-app", repo="me/my-app", project="P")


# -- the id --


def test_id_defaults_to_the_name_and_takes_the_next_free_variant_on_the_same_project():
    doc = _doc(**{"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}})
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


# -- copy --


def test_copy_takes_repo_and_keys_but_never_id_domain_or_secret_values():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj", changes={
        "domain": "mcp.example.com",
        "env": [{"name": "LOG_LEVEL", "value": "INFO"}, {"name": "API_KEY", "secret": {"name": "api-key"}}],
        "service": {"memory": "1Gi"},
    })
    doc, entry, info = fleet.copy_entry(doc, "my-app-staging", "my-app", project="staging-proj")
    assert entry["id"] == "my-app-staging" and entry["repo"] == "me/my-app" and entry["project"] == "staging-proj"
    assert "domain" not in entry
    assert entry["env"] == doc["my-app"]["env"] and entry["service"] == {"memory": "1Gi"}
    assert info == {"project_from_source": False}
    assert doc["my-app"]["domain"] == "mcp.example.com"               # the source is untouched


def test_copy_defaults_to_the_source_project_and_a_same_project_copy_is_independent():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    doc, entry, info = fleet.copy_entry(doc, "my-app-2", "my-app")
    assert entry["project"] == "my-proj" and info["project_from_source"] is True
    assert entry["id"] == "my-app-2" and doc["my-app"]["id"] == "my-app"   # its own service, account, bucket
    doc, _, e2 = fleet.set_entry(doc, "my-app-2", repo="me/my-app", project="my-proj", changes={"domain": "b.example.com"})
    assert "domain" not in doc["my-app"] and e2["domain"] == "b.example.com"


def test_copy_allocates_a_variant_id_when_the_name_is_taken_as_an_id_on_that_project():
    doc = _doc(**{"old": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}})
    doc, entry, _ = fleet.copy_entry(doc, "my-app", "old")
    assert entry["id"] == "my-app-2"


def test_copy_refuses_a_missing_source_or_a_taken_name():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj")
    with pytest.raises(FleetRefusal) as exc:
        fleet.copy_entry(doc, "new", "nope")
    assert exc.value.code == "source-missing" and "gapp ci list" in exc.value.message
    with pytest.raises(FleetRefusal) as exc:
        fleet.copy_entry(doc, "my-app", "my-app")
    assert exc.value.code == "name-taken"


# -- rename --


def test_rename_changes_the_name_only():
    doc, _, _ = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj", changes={"domain": "d.example.com"})
    doc = fleet.rename_entry(doc, "my-app", "renamed")
    assert list(doc) == ["renamed"]
    assert doc["renamed"] == {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "d.example.com"}
    with pytest.raises(FleetRefusal) as exc:
        fleet.rename_entry(doc, "gone", "x")
    assert exc.value.code == "deployment-missing"


# -- the overlay the deploy run applies --


def test_merge_supplies_missing_keys_overrides_present_ones_and_leaves_the_rest():
    base = {"name": "from-repo", "service": {"entrypoint": "app:app", "memory": "512Mi"}, "public": True,
            "env": [{"name": "LOG_LEVEL", "value": "INFO"}, {"name": "API_KEY", "secret": {"name": "api-key"}}]}
    overlay = {"name": "my-app", "domain": "mcp.example.com", "service": {"memory": "1Gi"},
               "env": [{"name": "LOG_LEVEL", "value": "DEBUG"}, {"name": "REGION", "value": "us"}]}
    merged = fleet.merge_manifest(base, overlay)
    assert merged["domain"] == "mcp.example.com"                          # supplied
    assert merged["name"] == "my-app"                                     # overridden: the deployment governs
    assert merged["service"] == {"entrypoint": "app:app", "memory": "1Gi"}  # merged key by key
    assert merged["public"] is True                                       # left alone
    assert merged["env"] == [{"name": "LOG_LEVEL", "value": "DEBUG"}, {"name": "API_KEY", "secret": {"name": "api-key"}},
                             {"name": "REGION", "value": "us"}]           # named entries merge by name
    assert base["service"]["memory"] == "512Mi"                           # inputs untouched


def test_overlay_for_an_entry_is_its_gapp_keys_with_name_set_to_the_id():
    entry = {"id": "my-app-2", "repo": "me/my-app", "project": "my-proj", "domain": "d.example.com"}
    assert fleet.overlay_for(entry) == {"domain": "d.example.com", "name": "my-app-2"}
    assert fleet.merge_manifest({}, fleet.overlay_for(entry))["name"] == "my-app-2"   # a repo with no gapp.yaml


def test_the_fleet_file_header_says_the_entry_carries_the_domain_and_gapp_yaml_the_rest():
    header = fleet.DEPLOYMENTS_FILE_INITIAL
    assert "domain (set\n# with `gapp ci setup --domain`). Every other setting comes from the app\n# repo's own gapp.yaml." in header
    assert "supply or override" not in header
