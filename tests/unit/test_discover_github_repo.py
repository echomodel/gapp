"""Tests for GappSDK.discover_github_repo.

Closes the regression introduced by the v-3 GappSDK consolidation:
`setup_ci` reads the GitHub repo identity from the dict returned by
`resolve_solution`, but the v-3 redesign removed the populator
without removing the consumer. `discover_github_repo` is the missing
reader.

The implementation is purely local — `git remote get-url origin`
plus a regex parse. No `gh` CLI, no GitHub API. Any caller of
`gapp ci setup` is necessarily working on the solution they're
setting up, so reading the repo's own origin remote is the correct
and deterministic source of truth.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
from gapp.admin.sdk.core import GappSDK


@pytest.fixture
def sdk():
    return GappSDK(provider=DummyCloudProvider())


def _git_ok(url):
    return subprocess.CompletedProcess(
        args=[], returncode=0, stdout=f"{url}\n", stderr=""
    )


def _git_fail():
    return subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="fatal: No such remote 'origin'"
    )


def test_parses_https_remote(sdk, tmp_path):
    """`https://github.com/owner/name.git` — the most common form
    after `git clone https://...`."""
    sol_ctx = {
        "name": "example-app",
        "project_id": None,
        "repo_path": str(tmp_path),
    }
    with patch(
        "gapp.admin.sdk.core.subprocess.run",
        return_value=_git_ok("https://github.com/example-org/example-app.git"),
    ) as mock_run:
        result = sdk.discover_github_repo(sol_ctx)

    assert result == "example-org/example-app"
    args, kwargs = mock_run.call_args
    assert args[0] == ["git", "remote", "get-url", "origin"]
    assert kwargs.get("cwd") == Path(str(tmp_path))


def test_parses_https_remote_without_dot_git(sdk, tmp_path):
    """Some clones produce `https://github.com/owner/name` with no
    `.git` suffix (`gh repo clone` does this by default)."""
    sol_ctx = {
        "name": "example-app",
        "project_id": None,
        "repo_path": str(tmp_path),
    }
    with patch(
        "gapp.admin.sdk.core.subprocess.run",
        return_value=_git_ok("https://github.com/example-owner/example-app"),
    ):
        result = sdk.discover_github_repo(sol_ctx)

    assert result == "example-owner/example-app"


def test_parses_ssh_remote(sdk, tmp_path):
    """`git@github.com:owner/name.git` — the form used when cloning
    over SSH or after `gh repo set-default --ssh`."""
    sol_ctx = {
        "name": "example-app",
        "project_id": None,
        "repo_path": str(tmp_path),
    }
    with patch(
        "gapp.admin.sdk.core.subprocess.run",
        return_value=_git_ok("git@github.com:example-owner/example-app.git"),
    ):
        result = sdk.discover_github_repo(sol_ctx)

    assert result == "example-owner/example-app"


def test_returns_none_when_no_repo_path(sdk):
    """Name-only invocation (e.g. `gapp ci setup <name>` from
    outside any checkout) — return None, caller raises its own
    error message. No magical cross-repo search."""
    result = sdk.discover_github_repo(
        {"name": "anywhere", "project_id": None, "repo_path": None}
    )
    assert result is None


def test_returns_none_when_repo_path_does_not_exist(sdk):
    """Stale config or deleted clone — return None rather than
    crashing on a missing directory."""
    sol_ctx = {
        "name": "x",
        "project_id": None,
        "repo_path": "/tmp/does-not-exist-anywhere-zzz",
    }
    result = sdk.discover_github_repo(sol_ctx)
    assert result is None


def test_returns_none_when_no_origin_remote(sdk, tmp_path):
    """`git remote get-url origin` fails (no `origin` configured) —
    return None, caller decides what to say."""
    sol_ctx = {
        "name": "x",
        "project_id": None,
        "repo_path": str(tmp_path),
    }
    with patch(
        "gapp.admin.sdk.core.subprocess.run",
        return_value=_git_fail(),
    ):
        result = sdk.discover_github_repo(sol_ctx)

    assert result is None


def test_returns_none_for_non_github_remote(sdk, tmp_path):
    """Origin points at GitLab, Bitbucket, or a private git host —
    not a GitHub repo, so gapp CI (which is GitHub Actions only)
    cannot use it. Return None rather than guessing."""
    sol_ctx = {
        "name": "x",
        "project_id": None,
        "repo_path": str(tmp_path),
    }
    for non_github_url in [
        "https://gitlab.example.com/owner/name.git",
        "https://bitbucket.example.com/owner/name.git",
        "https://internal-git.example.com/some/path.git",
    ]:
        with patch(
            "gapp.admin.sdk.core.subprocess.run",
            return_value=_git_ok(non_github_url),
        ):
            assert sdk.discover_github_repo(sol_ctx) is None, non_github_url
