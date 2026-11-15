import click

from gapp.admin.cli.main import main

from .conftest import fleet_file, gapp


def commands(group, prefix=()):
    for name, command in group.commands.items():
        if isinstance(command, click.Group):
            yield from commands(command, prefix + (name,))
        else:
            yield prefix + (name,), command


def test_no_command_takes_a_deployments_id():
    """Commitment: the operator never chooses or types a deployment's id.
    gapp picks it when the deployment is added.

    Why: the id names cloud resources and is gapp's to manage; a flag for
    it puts a word in the operator's hands they otherwise never need
    (CONTRIBUTING.md, "A deployment's identity").
    """
    for path, command in commands(main):
        for param in command.params:
            names = [n.lstrip("-") for n in getattr(param, "opts", [])] + [param.name]
            assert "id" not in names and "deployment-id" not in names, f"gapp {' '.join(path)} takes an id"


def test_defining_and_changing_a_deployment_never_shows_where_the_fleet_is_stored(fresh_machine):
    """Commitment: an operator does not need to know where the fleet is
    stored. The commands that define and change a deployment never print
    its path.

    Why: path opacity. Only someone who asks (`gapp fleet`, `gapp list`)
    or needs a file to look at is told (CONTRIBUTING.md, "Properties kept
    on purpose").
    """
    stored = str(fleet_file(fresh_machine).parent)
    for command in (
        ("add", "my-app", "--project", "my-project", "--repo", "acme/my-app"),
        ("set", "public", "true", "--deployment", "my-app"),
        ("env", "set", "LOG_LEVEL", "info", "--deployment", "my-app"),
        ("apis", "add", "drive.googleapis.com", "--deployment", "my-app"),
        ("show", "--deployment", "my-app"),
        ("rename", "my-app", "prod"),
    ):
        result = gapp(*command)
        assert result.exit_code == 0, result.output
        assert stored not in result.output, f"gapp {command[0]} printed the fleet's path"
