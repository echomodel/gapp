"""The real CLI and MCP server against the agreed interface.

`docs/interface.yaml` lists every command, argument, option and MCP tool,
and `docs/INTERFACE.md` says the same for people. These tests hold the
three together: nothing can be added to the CLI or the MCP server without
adding it to the page first, and nothing on the page can go missing.
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
    def walk(command, path):
        name = " ".join(path)
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


def test_every_page_command_is_built():
    missing = set(SPEC["commands"]) - set(_built())
    assert not missing, f"In docs/interface.yaml but not in the CLI: {sorted(missing)}"


def test_the_cli_has_no_command_the_page_lacks():
    extra = set(_built()) - set(SPEC["commands"])
    assert not extra, f"In the CLI but not in docs/interface.yaml (add it to the page first): {sorted(extra)}"


def test_commands_take_the_pages_arguments_and_options():
    built = _built()
    off = [f"{name}: page {_wanted(entry)}, built {built[name]}"
           for name, entry in SPEC["commands"].items() if name in built and built[name] != _wanted(entry)]
    assert not off, "Arguments or options differ from the page:\n  " + "\n  ".join(off)


def test_mcp_tools_are_the_pages():
    wanted, built = set(SPEC["mcp_tools"]), _mcp_tools()
    assert wanted == built, f"missing: {sorted(wanted - built)}; not in the page: {sorted(built - wanted)}"


def test_every_command_in_the_spec_is_in_the_page():
    absent = [n for n in SPEC["commands"] if f"`gapp {n}" not in PAGE]
    assert not absent, f"In docs/interface.yaml but not written as `gapp ...` in INTERFACE.md: {absent}"


def test_every_tool_in_the_spec_is_in_the_page():
    absent = [t for t in SPEC["mcp_tools"] if f"`{t}`" not in PAGE]
    assert not absent, f"In docs/interface.yaml but not in INTERFACE.md: {absent}"
