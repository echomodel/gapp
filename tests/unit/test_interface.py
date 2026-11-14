"""The real CLI and MCP server against the agreed interface.

`docs/interface.yaml` lists every command, argument, option and MCP tool
of 4.0.0, and `docs/INTERFACE.md` says the same for people. These tests
hold the three together.

While 4.0.0 is being built the CLI is not there yet. PENDING, LEAVING and
DIFFERENT below say exactly how far off it is. They are compared for
equality, so they can only be changed on purpose: building a command
means deleting its line here, and nothing can be added to the CLI without
adding it to the page first. When all five sets are empty, delete them.
"""

import asyncio
from pathlib import Path

import click
import yaml

from gapp.admin.cli.main import main
from gapp.admin.mcp import server

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "docs" / "interface.yaml").read_text())
PAGE = (ROOT / "docs" / "INTERFACE.md").read_text()

# In the page, not built yet.
PENDING = {
    "project setup", "add", "upgrade", "rename", "show",
    "set", "unset", "apis add", "apis remove", "env set", "env unset", "apply",
    "fleet use", "fleet export", "fleet import", "fleet recover",
    "config profile list", "config profile use", "config profile add",
    "config profile remove", "config profile rename",
}

# Built, not in the page: to be removed.
LEAVING = {
    "setup", "init",
    "manifest show", "manifest set", "manifest unset", "manifest env set",
    "manifest env unset", "manifest example", "manifest schema",
    "ci init", "ci setup", "ci project setup", "ci rename", "ci list",
    "ci manifest",
    "config", "config profile", "config discovery",
}

# In both, but the arguments or options are not the page's yet.
DIFFERENT = {
    "deploy", "status", "list", "secrets list", "secrets set", "secrets get",
    "ci deploy", "ci status", "ci sources add", "ci sources remove",
    "ci sources list", "config account",
}

MCP_PENDING = {
    "gapp_project_setup", "gapp_add", "gapp_upgrade", "gapp_rename",
    "gapp_show", "gapp_set", "gapp_unset", "gapp_apis", "gapp_env_set",
    "gapp_env_unset", "gapp_apply", "gapp_secrets_list", "gapp_secrets_set",
    "gapp_secrets_get", "gapp_fleet_use", "gapp_fleet_export",
    "gapp_fleet_import", "gapp_fleet_recover",
}

MCP_LEAVING = {
    "gapp_schema", "gapp_manifest_example", "gapp_manifest_show",
    "gapp_manifest_set", "gapp_manifest_unset", "gapp_manifest_env_set",
    "gapp_manifest_env_unset", "gapp_user", "gapp_init", "gapp_setup",
    "gapp_secret_get", "gapp_secret_set", "gapp_secret_list",
    "gapp_ci_init", "gapp_ci_project_setup", "gapp_ci_setup",
    "gapp_ci_manifest", "gapp_ci_rename", "gapp_ci_trigger", "gapp_ci_list",
}


def _wanted(entry: dict) -> tuple:
    """A spec entry as (arguments, options), in the form `_built` gives."""
    entry = entry or {}
    options = set(entry.get("options", [])) | {"--json"}
    if entry.get("one"):
        options.add("--deployment")
    if entry.get("runs"):
        options.add("--no-watch")
    return tuple(a.lower() for a in entry.get("args", [])), frozenset(options)


def _shape(command: click.Command) -> tuple:
    args, options = [], set()
    for p in command.params:
        if isinstance(p, click.Argument):
            args.append(("<%s>" if p.required else "[%s]") % p.name.lower())
        elif not getattr(p, "hidden", False):
            options.update(o for o in p.opts if o.startswith("--"))
    return tuple(args), frozenset(options)


def _built() -> dict:
    """Every visible command of the real CLI: path -> (arguments, options)."""
    found = {}
    runner = SPEC["runner"]["prefix"]

    def walk(command, path):
        name = " ".join(path)
        if name == runner or name.startswith(runner + " "):
            return
        if isinstance(command, click.Group):
            if command.invoke_without_command and path:
                found[name] = _shape(command)
            for sub_name, sub in command.commands.items():
                if not sub.hidden:
                    walk(sub, path + [sub_name])
        else:
            found[name] = _shape(command)

    walk(main, [])
    return found


def _mcp_tools() -> set:
    return {t.name for t in asyncio.run(server.mcp.list_tools())}


def test_every_page_command_is_built_or_listed_as_pending():
    missing = set(SPEC["commands"]) - set(_built())
    assert missing == PENDING, (
        "Commands in docs/interface.yaml that the CLI lacks differ from PENDING.\n"
        f"  built since (delete from PENDING): {sorted(PENDING - missing)}\n"
        f"  missing and not listed: {sorted(missing - PENDING)}"
    )


def test_the_cli_has_no_command_the_page_lacks():
    extra = set(_built()) - set(SPEC["commands"])
    assert extra == LEAVING, (
        "Commands the CLI has that docs/interface.yaml lacks differ from LEAVING.\n"
        f"  removed since (delete from LEAVING): {sorted(LEAVING - extra)}\n"
        f"  new and not in the page (add it to the page first): {sorted(extra - LEAVING)}"
    )


def test_shared_commands_take_the_pages_arguments_and_options():
    built = _built()
    off = {
        name for name, entry in SPEC["commands"].items()
        if name in built and built[name] != _wanted(entry)
    }
    detail = "\n".join(
        f"  {n}: page {_wanted(SPEC['commands'][n])}, built {built[n]}"
        for n in sorted(off ^ DIFFERENT) if n in built
    )
    assert off == DIFFERENT, (
        "Commands whose arguments or options differ from the page are not the "
        f"ones in DIFFERENT.\n{detail}"
    )


def test_mcp_tools_are_the_pages():
    wanted, built = set(SPEC["mcp_tools"]), _mcp_tools()
    assert wanted - built == MCP_PENDING, (
        f"built since: {sorted(MCP_PENDING - (wanted - built))}; "
        f"missing and not listed: {sorted((wanted - built) - MCP_PENDING)}"
    )
    assert built - wanted == MCP_LEAVING, (
        f"removed since: {sorted(MCP_LEAVING - (built - wanted))}; "
        f"new and not in the page: {sorted((built - wanted) - MCP_LEAVING)}"
    )


def test_every_command_in_the_spec_is_in_the_page():
    absent = [n for n in SPEC["commands"] if f"`gapp {n}" not in PAGE]
    assert not absent, f"In docs/interface.yaml but not written as `gapp ...` in INTERFACE.md: {absent}"


def test_every_tool_in_the_spec_is_in_the_page():
    absent = [t for t in SPEC["mcp_tools"] if f"`{t}`" not in PAGE]
    assert not absent, f"In docs/interface.yaml but not in INTERFACE.md: {absent}"


def test_the_pending_lists_only_name_things_in_the_spec():
    assert PENDING <= set(SPEC["commands"])
    assert DIFFERENT <= set(SPEC["commands"])
    assert MCP_PENDING <= set(SPEC["mcp_tools"])
    assert not (LEAVING & set(SPEC["commands"]))
    assert not (MCP_LEAVING & set(SPEC["mcp_tools"]))
