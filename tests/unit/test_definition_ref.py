"""What a deploy builds is in the deployment's definition: its `ref`, a
branch, tag or commit of the app's repo. It is set like any other part of
the definition and is never a gapp.yaml key; a deploy takes no ref."""

import inspect

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.mcp import server
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import fleet
from gapp.admin.sdk.fleet import FleetRefusal

from tests.unit.test_ci_setup import FLEET, _setup, wired  # noqa: F401  (fixture)

BASE = {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "ref": "v1.4.0", "public": True}}


# -- the definition --


def test_the_ref_is_the_definitions_own_setting_never_a_gapp_yaml_key():
    entry = BASE["my-app"]
    assert fleet.ref_of(entry) == "v1.4.0" and fleet.ref_of({"id": "x"}) is None
    assert fleet.manifest_keys(entry) == {"public": True}
    assert fleet.overlay_for(entry) == {"public": True, "name": "my-app"}
    assert fleet.unknown_keys(fleet.manifest_keys(entry)) == []


def test_the_file_lists_the_ref_with_the_entrys_own_fields():
    text = fleet.dump({"my-app": {"public": True, "ref": "v1.4.0", "project": "my-proj", "repo": "me/my-app", "id": "my-app"}})
    body = text.split("\nmy-app:\n", 1)[1]
    assert [line.split(":")[0].strip() for line in body.splitlines()] == ["id", "repo", "project", "ref", "public"]
    assert fleet.parse(text) == BASE


def test_a_ref_is_set_changed_and_removed_as_a_change_and_other_settings_are_kept():
    doc, action, entry = fleet.set_entry(BASE, "my-app", repo="me/my-app", project="my-proj", changes={"ref": "main"})
    assert action == "updated" and entry["ref"] == "main" and entry["public"] is True
    doc, action, entry = fleet.set_entry(doc, "my-app", repo="me/my-app", project="my-proj", changes={"ref": None})
    assert action == "updated" and "ref" not in entry and entry["public"] is True
    assert fleet.set_entry(doc, "my-app", repo="me/my-app", project="my-proj")[1] == "unchanged"


def test_a_new_definition_can_start_with_a_ref():
    _, action, entry = fleet.set_entry({}, "my-app", repo="me/my-app", project="my-proj", changes={"ref": "release/1.x"})
    assert action == "added" and entry == {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "ref": "release/1.x"}


@pytest.mark.parametrize("ref", ["main", "v1.4.0", "release/1.x", "feature/add_thing", "0123abc", "user@branch"])
def test_branches_tags_and_commits_are_accepted(ref):
    assert fleet.validate_ref(f" {ref} ") == ref


@pytest.mark.parametrize("ref", ["", "-rf", "--upload-pack=x", "has space", "a..b", "x;rm", "$(x)", "a" * 201])
def test_what_is_not_a_plain_ref_is_refused(ref):
    with pytest.raises(FleetRefusal, match="is not a ref gapp accepts"):
        fleet.validate_changes({"ref": ref})


def test_a_copy_keeps_its_sources_ref_unless_one_is_given():
    _, entry, _ = fleet.copy_entry(BASE, "my-app-staging", "my-app")
    assert entry["ref"] == "v1.4.0"
    _, entry, _ = fleet.copy_entry(BASE, "my-app-try", "my-app", changes={"ref": "my-feature"})
    assert entry["ref"] == "my-feature"
    _, entry, _ = fleet.copy_entry(BASE, "my-app-tip", "my-app", changes={"ref": None})
    assert "ref" not in entry


def test_replacing_the_manifest_keeps_the_ref_and_a_ref_in_the_manifest_file_is_refused():
    changes = fleet.replacement_changes(BASE["my-app"], {"service": {"memory": "1Gi"}})
    assert changes == {"public": None, "service": {"memory": "1Gi"}}
    with pytest.raises(FleetRefusal, match="'ref' is not a gapp.yaml setting"):
        fleet.validate_manifest_upload({"ref": "main"})


# -- gapp ci setup --ref --


def test_setup_sets_a_ref_on_a_new_deployment_and_says_what_it_builds(wired):
    res = _setup(ref="v1.4.0")
    assert res["ok"] is True and res["action"] == "added" and res["ref"] == "v1.4.0"
    assert wired.deployments(FLEET)["my-app"]["ref"] == "v1.4.0"
    assert "Builds: v1.4.0" in res["lines"]


def test_changing_the_ref_is_shown_and_needs_update_like_any_change(wired):
    _setup(ref="v1.4.0")
    dispatched = len(wired.dispatched)
    res = _setup(ref="v1.5.0")
    assert res["ok"] is False and res["next_step"]["action"] == "pass --update" and len(wired.dispatched) == dispatched
    assert res["changes_shown"] == ["ref: v1.4.0 → v1.5.0"]
    assert "--ref v1.5.0 --update" in res["next_step"]["hint"]
    assert _setup(ref="v1.5.0", update=True)["action"] == "updated"
    assert wired.deployments(FLEET)["my-app"]["ref"] == "v1.5.0"
    assert _setup()["action"] == "unchanged"                      # leaving --ref off keeps it
    res = _setup(ref="")
    assert res["changes_shown"] == ["ref: v1.5.0 → (default branch)"]
    _setup(ref="", update=True)
    assert "ref" not in wired.deployments(FLEET)["my-app"]


def test_uploading_a_manifest_does_not_touch_the_ref(wired):
    _setup(ref="v1.4.0")
    _setup(manifest={"public": True}, update=True)
    assert wired.deployments(FLEET)["my-app"] == {"id": "my-app", "repo": "me/my-app", "project": "my-proj",
                                                  "ref": "v1.4.0", "public": True}


def test_a_ref_gapp_does_not_accept_is_refused_before_anything_is_dispatched(wired):
    dispatched = len(wired.dispatched)
    res = _setup(ref="--upload-pack=x")
    assert res["ok"] is False and "is not a ref gapp accepts" in res["next_step"]["hint"]
    assert len(wired.dispatched) == dispatched


def test_a_copy_through_setup_can_follow_another_branch(wired):
    _setup(ref="v1.4.0")
    res = ci_mod.setup_deployment("my-app-try", copy_of="my-app", ref="my-feature", watch=True)
    assert res["ok"] is True, res
    assert wired.deployments(FLEET)["my-app-try"]["ref"] == "my-feature"
    assert wired.deployments(FLEET)["my-app"]["ref"] == "v1.4.0"


def test_the_merged_preview_is_of_the_definitions_ref_unless_another_is_asked_for(wired):
    _setup(ref="v1.4.0")
    wired.ref_files[("me/my-app", "v1.4.0")] = {"Procfile": "web: x\n"}
    wired.ref_files[("me/my-app", "v2.0.0")] = {"Procfile": "web: y\n"}
    assert ci_mod.merged_manifest("my-app")["ref"] == "v1.4.0"
    assert ci_mod.merged_manifest("my-app", ref="v2.0.0")["ref"] == "v2.0.0"


# -- no ref on a deploy --


def test_no_deploy_command_or_tool_takes_a_ref():
    for fn in (ci_mod.deploy_deployment, server.gapp_ci_deploy, server.gapp_ci_trigger, server.gapp_deploy):
        assert "ref" not in inspect.signature(fn).parameters, fn.__name__
    assert "ref" in inspect.signature(server.gapp_ci_setup).parameters


def test_ci_deploy_with_a_ref_is_refused_with_the_two_commands_that_replace_it(monkeypatch):
    monkeypatch.setattr(ci_mod, "deploy_deployment", lambda *a, **k: pytest.fail("a deploy was started"))
    res = CliRunner().invoke(main, ["ci", "deploy", "my-app", "--ref", "v1.5.0"])
    assert res.exit_code == 2
    assert "`gapp ci deploy` takes no --ref." in res.output
    assert "gapp ci setup my-app --repo <owner>/<repo> --project <project> --ref v1.5.0 --update" in res.output
    assert "gapp ci deploy my-app" in res.output


def test_gapp_deploy_with_a_ref_is_refused_off_a_runner_and_accepted_on_one(monkeypatch):
    from gapp.admin.sdk.core import GappSDK
    seen = {}
    monkeypatch.setattr(GappSDK, "deploy", lambda self, **kw: seen.update(kw) or {"dry_run": True, "name": "x"})
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    res = CliRunner().invoke(main, ["deploy", "--ref", "v1.5.0"])
    assert res.exit_code == 2 and "`gapp deploy` takes no --ref: it builds the commit this clone is on." in res.output
    assert seen == {}
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    CliRunner().invoke(main, ["deploy", "--ref", "v1.5.0", "--dry-run"])
    assert seen["ref"] == "v1.5.0"


def test_the_ref_option_is_hidden_from_both_deploys_help():
    for argv in (["deploy", "--help"], ["ci", "deploy", "--help"]):
        out = CliRunner().invoke(main, argv).output
        assert "--ref" not in out.split("Options:")[1], argv
    assert "--ref REF" in CliRunner().invoke(main, ["ci", "setup", "--help"]).output
