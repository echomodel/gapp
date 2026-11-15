import json

import pytest

from .conftest import fleet_file, gapp

REFUSED = [
    ("add", "my-app", "--project", "other-project"),                # the name exists
    ("add", "second", "--project", "Not A Project"),                # not a project id
    ("add", "second"),                                               # no project
    ("rename", "my-app", "other"),                                   # the name is taken
    ("rename", "nope", "x"),                                         # no such deployment
    ("remove", "nope"),
    ("set", "colour", "blue", "--deployment", "my-app"),             # no such setting
    ("set", "max_instances", "many", "--deployment", "my-app"),      # not a number
    ("unset", "project", "--deployment", "my-app"),                  # cannot be cleared
    ("show", "--deployment", "nope"),
    ("upgrade", "--deployment", "my-app"),                           # nothing to upgrade
]


@pytest.fixture
def two(fresh_machine):
    assert gapp("add", "my-app", "--project", "my-project", "--repo", "acme/my-app").exit_code == 0
    assert gapp("add", "other", "--project", "my-project", "--repo", "acme/other").exit_code == 0
    return fleet_file(fresh_machine)


@pytest.mark.parametrize("command", REFUSED, ids=lambda c: " ".join(c))
def test_a_refused_command_exits_2_and_leaves_the_fleet_as_it_was(two, command):
    """Commitment: a refusal changes nothing and exits 2. A failure part
    way exits 1; done exits 0.

    Why: scripts and agents branch on the exit code, and "refused" has to
    mean it is safe to fix the cause and run it again (CONTRIBUTING.md,
    "Refuse before changing, and say so").
    """
    before = two.read_bytes()
    result = gapp(*command)
    assert result.exit_code == 2, f"exit {result.exit_code}: {result.output}"
    assert two.read_bytes() == before


@pytest.mark.parametrize("command", REFUSED, ids=lambda c: " ".join(c))
def test_a_refusal_as_json_says_refused_and_why(two, command):
    """Commitment: with --json, a refusal is `{"refused": true, "message": ...}`.

    Why: an agent reads the same outcome a person does, as data
    (CONTRIBUTING.md, "The interface is a contract").
    """
    result = gapp(*command, "--json")
    assert result.exit_code == 2
    said = json.loads(result.stdout)
    assert said["refused"] is True and said["message"]
