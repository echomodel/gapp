"""What a deploy builds is in the deployment's definition: its `ref`, a
branch, tag or commit of the app's repo. It is set like any other part of
the definition (`gapp set ref`), and no deploy takes one."""

import inspect

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.mcp import server
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import fleet
from gapp.admin.sdk.core import GappSDK
from gapp.admin.sdk.fleet import FleetRefusal


@pytest.mark.parametrize("ref", ["main", "v1.4.0", "release/1.x", "feature/add_thing", "0123abc", "user@branch"])
def test_branches_tags_and_commits_are_accepted(ref):
    assert fleet.validate_ref(f" {ref} ") == ref


@pytest.mark.parametrize("ref", ["", "-rf", "--upload-pack=x", "has space", "a..b", "x;rm", "$(x)", "a" * 201])
def test_what_is_not_a_plain_ref_is_refused(ref):
    with pytest.raises(FleetRefusal, match="is not a ref gapp accepts"):
        fleet.validate_ref(ref)


def test_no_deploy_command_or_tool_takes_a_ref():
    for fn in (ci_mod.deploy, GappSDK.deploy_definition, server.gapp_ci_deploy, server.gapp_deploy):
        assert "ref" not in inspect.signature(fn).parameters, fn.__name__
    for argv in (["deploy", "--help"], ["ci", "deploy", "--help"]):
        assert "--ref" not in CliRunner().invoke(main, argv).output.split("Options:")[1], argv
