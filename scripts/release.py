#!/usr/bin/env python3
"""One-shot release prep for gapp.

Usage:
    scripts/release.py X.Y.Z

What it does:
    1. Validates the new version is strictly greater than the current one.
    2. Updates `__version__` in gapp/__init__.py.
    3. Updates `version` in .claude-plugin/plugin.json so the optional
       plugin manifest stays in lockstep. (pyproject.toml is dynamic — it
       sources its version from gapp.__version__.)
    4. Runs the unit-test suite (`python3 -m pytest tests/unit/`). Fails the
       release if anything is red.
    5. Stages and commits the version-file changes with a templated message.
    6. Tags `vX.Y.Z` at the new commit.
    7. Prints the publish steps the user runs themselves: push the commit
       and the tag, then reinstall the CLI at the tag wherever it is used.

The pre-commit privacy hook still runs on the commit. The script does not
push; that crosses the remote boundary and the user controls it.

See CONTRIBUTING.md "Release workflow" for the full procedure.
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


def print_followup(new_version: str) -> None:
    print()
    print(f"  Local prep done. v{new_version} committed and tagged.")
    print()
    print("  Remaining publish steps (run from your shell):")
    print()
    print("  # 1. Push the commit and the tag")
    print(f"  git push origin main && git push origin v{new_version}")
    print()
    print("  # 2. Reinstall the CLI at the tag on each machine that uses it")
    print("  #    (run from $HOME: from inside the repo, pipx parses the bare")
    print("  #    package name as the ./gapp directory)")
    print(f'  cd ~ && pipx install --force "gapp @ git+https://github.com/echomodel/gapp.git@v{new_version}"')
    print()
    print("  # 3. If skills/ changed, republish them wherever they are")
    print("  #    distributed (a saved skill, a plugin listing).")
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
    print_followup(new_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
