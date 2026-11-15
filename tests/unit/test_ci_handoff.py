"""`gapp ci deploy` and `gapp ci status --live` refuse, before a run is
started, a definition the fleet repo's workflows do not work with: one
older than the workflows' COMPATIBLE_FROM, or one on this gapp's version
when the workflows are older than this gapp's COMPATIBLE_FROM. The runner
makes the same checks from its side (test_ci_runner.py)."""

import pytest

import gapp
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import entryfile

FLEET = "acme/fleet"


def _on(fake_gh, version):
    whole = entryfile.parse(fake_gh.files[(FLEET, entryfile.FILE_NAME)])
    whole["deployments"]["my-app"]["gapp"] = version
    fake_gh.files[(FLEET, entryfile.FILE_NAME)] = entryfile.dump(whole)


def _workflows(fake_gh, ref, minimum=None):
    """The fleet repo's workflows as gapp `ref` installs them, saying `minimum` as their COMPATIBLE_FROM."""
    for path, content in ci_mod._workflow_files("example-org/gapp", ref).items():
        if minimum:
            content = content.replace(f'minimum="{gapp.COMPATIBLE_FROM}"', f'minimum="{minimum}"')
        fake_gh.files[(FLEET, path)] = content


def _start(command):
    return ci_mod.deploy("my-app") if command == "deploy" else ci_mod.status("my-app", live=True)


@pytest.mark.parametrize("command", ["deploy", "status"])
def test_a_definition_older_than_the_workflows_compatible_from_is_refused_with_upgrade(ci, fake_gh, command):
    _workflows(fake_gh, f"v{gapp.__version__}", minimum="4.0.0rc15")
    _on(fake_gh, "4.0.0rc14")
    res = _start(command)
    assert res["refused"] is True and res["dispatched"] is False and ci.dispatched == []
    assert res["next_step"]["action"] == "upgrade"
    assert res["next_step"]["hint"] == (
        "'my-app' is defined with gapp 4.0.0rc14, and the workflows in acme/fleet work with 4.0.0rc15 or later. "
        "Upgrade it first: gapp upgrade --deployment my-app, then gapp ci deploy --deployment my-app --upgrade")


@pytest.mark.parametrize("command", ["deploy", "status"])
def test_workflows_older_than_this_gapps_compatible_from_are_refused_with_ci_init(ci, fake_gh, command, monkeypatch):
    monkeypatch.setattr(gapp, "COMPATIBLE_FROM", "4.0.0rc15")
    _workflows(fake_gh, "v4.0.0rc14", minimum="4.0.0rc13")
    _on(fake_gh, gapp.__version__)
    res = _start(command)
    assert res["refused"] is True and res["dispatched"] is False and ci.dispatched == []
    assert res["next_step"]["action"] == "ci init"
    assert "the workflows in acme/fleet are gapp 4.0.0rc14's, older than it works with (4.0.0rc15 or later)" in (
        res["next_step"]["hint"])


@pytest.mark.parametrize("workflows, definition", [
    (None, None),                        # this gapp's workflows, this gapp's definition
    ("v99.0.0", None),                   # newer workflows, same floor
    (None, "4.0.0rc15"),                 # an older definition, within the floor
])
def test_versions_within_the_floor_are_started(ci, fake_gh, workflows, definition, monkeypatch):
    monkeypatch.setattr(gapp, "COMPATIBLE_FROM", "4.0.0rc15")
    _workflows(fake_gh, workflows or f"v{gapp.__version__}")
    _on(fake_gh, definition or gapp.__version__)
    assert ci_mod.deploy("my-app")["dispatched"] is True


def test_workflows_pinned_to_a_commit_still_say_their_floor(ci, fake_gh):
    _workflows(fake_gh, "c0ffee" * 6 + "c0ff")   # a commit, not a release tag
    _on(fake_gh, "4.0.0rc14")
    assert ci_mod.deploy("my-app")["refused"] is True


def test_the_installed_workflows_carry_compatible_from_and_their_version():
    for content in ci_mod._workflow_files("example-org/gapp", "v4.0.0").values():
        assert "__GAPP_" not in content.replace("__GAPP_REPO__", "")
        assert f'minimum="{gapp.COMPATIBLE_FROM}"' in content
        assert gapp.__version__ in content
