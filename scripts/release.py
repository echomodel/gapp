#!/usr/bin/env python3
"""One-shot release prep for gapp.

Usage:
    scripts/release.py X.Y.Z

What it does:
    1. Validates the new version is strictly greater than the current one.
    2. Updates `__version__` in gapp/__init__.py.
    3. Updates `version` in .claude-plugin/plugin.json.
       (pyproject.toml is dynamic — sources its version from gapp.__version__,
       so it does not need a separate edit.)
    4. Runs the unit-test suite (`python3 -m pytest tests/unit/`). Fails the
       release if anything is red.
    5. Stages and commits the version-file changes with a templated message.
    6. Tags `vX.Y.Z` at the new commit.
    7. If the marketplace repo is checked out as a sibling
       (`echomodel-claude-plugins`), edits its marketplace.json to pin the
       gapp plugin at the new tag and commits that change in the marketplace
       repo. No push.
    8. Prints the remaining publish steps the user must run themselves
       (push gapp, push marketplace, plugin update, pipx upgrade,
       Claude Code restart).

The pre-commit privacy hook still runs on every commit step. The script
does not push, and does not invoke any `claude plugin` commands. Those
steps cross machine and remote-repo boundaries; the user controls them.

See CONTRIBUTING.md "Release workflow" for the full procedure including
the post-tag steps.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PY = REPO_ROOT / "gapp" / "__init__.py"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
PYPROJECT_TOML = REPO_ROOT / "pyproject.toml"
MARKETPLACE_REPO = REPO_ROOT.parent / "echomodel-claude-plugins"
MARKETPLACE_JSON = MARKETPLACE_REPO / ".claude-plugin" / "marketplace.json"
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def fatal(msg: str) -> None:
    print(f"  ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def read_current_version() -> str:
    txt = INIT_PY.read_text()
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', txt, re.M)
    if not match:
        fatal(f"Could not find __version__ in {INIT_PY}")
    return match.group(1)


def parse_version(v: str) -> tuple[int, int, int]:
    if not VERSION_RE.match(v):
        fatal(f"Version must be X.Y.Z (got {v!r})")
    return tuple(int(p) for p in v.split("."))  # type: ignore[return-value]


def update_init_py(new_version: str) -> None:
    txt = INIT_PY.read_text()
    new = re.sub(
        r'^__version__\s*=\s*"[^"]+"',
        f'__version__ = "{new_version}"',
        txt,
        count=1,
        flags=re.M,
    )
    INIT_PY.write_text(new)


def update_plugin_json(new_version: str) -> None:
    data = json.loads(PLUGIN_JSON.read_text())
    data["version"] = new_version
    PLUGIN_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


def assert_pyproject_is_dynamic() -> None:
    txt = PYPROJECT_TOML.read_text()
    if 'dynamic = ["version"]' not in txt or 'attr = "gapp.__version__"' not in txt:
        fatal(
            f"{PYPROJECT_TOML.name} is not configured for dynamic version. "
            "Make `__version__` in gapp/__init__.py the single source by setting "
            'project.dynamic = ["version"] and tool.setuptools.dynamic.version.attr.'
        )


def assert_clean_tree() -> None:
    res = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    if res.stdout.strip():
        fatal(
            "Working tree is not clean. Commit or stash before releasing:\n"
            + res.stdout
        )


def run_tests() -> None:
    print("  Running unit tests...")
    res = subprocess.run(
        ["python3", "-m", "pytest", "tests/unit/", "-q"],
        cwd=REPO_ROOT,
    )
    if res.returncode != 0:
        fatal("Tests failed. Aborting release.")


def stage_and_commit(new_version: str) -> None:
    subprocess.run(
        ["git", "add", str(INIT_PY), str(PLUGIN_JSON)],
        cwd=REPO_ROOT, check=True,
    )
    msg = f"chore: bump version to {new_version}"
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=REPO_ROOT, check=True,
    )


def tag(new_version: str) -> None:
    subprocess.run(
        ["git", "tag", f"v{new_version}"],
        cwd=REPO_ROOT, check=True,
    )


def bump_marketplace(new_version: str) -> bool:
    """Update gapp's ref pin in the sibling marketplace repo and commit.

    Returns True if the bump succeeded, False if the marketplace repo is
    not checked out as a sibling (in which case the user must do it
    manually).
    """
    if not MARKETPLACE_JSON.exists():
        return False

    data = json.loads(MARKETPLACE_JSON.read_text())
    plugins = data.get("plugins", [])
    gapp_entry = next((p for p in plugins if p.get("name") == "gapp"), None)
    if gapp_entry is None:
        fatal(
            f"No 'gapp' plugin entry in {MARKETPLACE_JSON}. "
            "Marketplace structure has changed; update this script."
        )

    new_ref = f"v{new_version}"
    if gapp_entry["source"].get("ref") == new_ref:
        print(f"  Marketplace already pinned to {new_ref}; skipping.")
        return True

    gapp_entry["source"]["ref"] = new_ref
    MARKETPLACE_JSON.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )

    # Commit the change inside the marketplace repo.
    subprocess.run(
        ["git", "add", str(MARKETPLACE_JSON)],
        cwd=MARKETPLACE_REPO, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", f"Bump gapp to v{new_version}"],
        cwd=MARKETPLACE_REPO, check=True,
    )
    return True


def print_followup(new_version: str, marketplace_done: bool) -> None:
    print()
    print(f"  Local prep done. v{new_version} committed and tagged.")
    print()
    print("  Remaining publish steps (run from your shell):")
    print()
    print(f"  # 1. Push gapp")
    print(f"  git push origin main && git push origin v{new_version}")
    print()
    if marketplace_done:
        print(f"  # 2. Push marketplace bump")
        print(f"  cd ../echomodel-claude-plugins && git push origin main")
    else:
        print(f"  # 2. Marketplace repo not found at sibling path.")
        print(f"  #     Bump and push it manually before continuing.")
    print()
    print(f"  # 3. Refresh plugin from marketplace")
    print(f"  claude plugin marketplace update echomodel && "
          f"claude plugin update gapp@echomodel")
    print()
    print(f"  # 4. Upgrade the pipx CLI on PATH (run from $HOME to dodge")
    print(f"  #     pipx's path-parse gotcha when CWD is the gapp repo)")
    print(f"  cd ~ && pipx upgrade gapp")
    print()
    print("  # 5. Restart Claude Code so the SessionStart hook reinstalls")
    print("  #     the plugin's site-packages with the new version.")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("version", help="New version, e.g. 3.0.8")
    args = parser.parse_args()

    new_version = args.version
    parse_version(new_version)

    current = read_current_version()
    if parse_version(new_version) <= parse_version(current):
        fatal(
            f"New version {new_version} is not greater than current {current}. "
            "Aborting."
        )

    assert_pyproject_is_dynamic()
    assert_clean_tree()

    print(f"  Bumping {current} -> {new_version}")
    update_init_py(new_version)
    update_plugin_json(new_version)
    run_tests()
    stage_and_commit(new_version)
    tag(new_version)
    marketplace_done = bump_marketplace(new_version)
    print_followup(new_version, marketplace_done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
