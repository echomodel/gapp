import subprocess

import pytest

from .conftest import gapp


def commands(settings):
    return (
        ("add", "my-app", "--project", "my-project", "--repo", "acme/my-app"),
        ("add", "my-app-test", "--copy-of", "my-app", "--project", "my-test-project"),
        ("set", "ref", "main", "--deployment", "my-app"),
        ("set", "domain", "app.example.com", "--deployment", "my-app"),
        ("unset", "domain", "--deployment", "my-app"),
        ("env", "set", "LOG_LEVEL", "info", "--deployment", "my-app"),
        ("env", "set", "APP_KEY", "--secret", "--generate", "--deployment", "my-app"),
        ("env", "unset", "LOG_LEVEL", "--deployment", "my-app"),
        ("apis", "add", "drive.googleapis.com", "--deployment", "my-app"),
        ("apply", str(settings), "--deployment", "my-app"),
        ("show", "--deployment", "my-app"),
        ("list",),
        ("fleet",),
        ("fleet", "export"),
        ("rename", "my-app", "prod"),
        ("remove", "prod"),
    )


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("public: true\nmemory: 1Gi\n")
    return path


@pytest.fixture
def fleet_in_a_git_repo(monkeypatch, tmp_path):
    """The fleet kept in a plain git repo that is not on GitHub, on a
    machine with a git identity and none of its own hooks."""
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    for key, value in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(hooks),
                       "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev" + "@" + "example.test",
                       "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev" + "@" + "example.test",
                       "GIT_TERMINAL_PROMPT": "0"}.items():
        monkeypatch.setenv(key, value)
    remote = tmp_path / "fleet.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    return str(remote)


def test_with_a_local_fleet_they_run_no_gcloud_terraform_or_gh(fresh_machine, no_cloud, settings):
    """Commitment: the commands that define, change, read or remove a
    deployment never touch Google Cloud and never need the GitHub CLI.

    Why: defining what should run and deploying it need different access,
    so each must work where the other's is missing (CONTRIBUTING.md,
    "Properties kept on purpose").
    """
    for command in commands(settings):
        result = gapp(*command)
        assert result.exit_code == 0, f"gapp {' '.join(command)}: {result.output}{result.exception!r}"


def test_with_the_fleet_in_a_git_repo_they_use_git_and_nothing_else(fresh_machine, fleet_in_a_git_repo, no_cloud,
                                                                    settings):
    """Commitment: with the fleet in a git repo, the same commands use
    git alone: no gcloud, no Terraform, and no GitHub CLI, so the repo
    can be on any git host.

    Why: a fleet in git is separate from CI and from GitHub. Only `gapp
    fleet use --create` and `gapp ci` use the GitHub CLI (CONTRIBUTING.md,
    "One fleet"; docs/FLEET-IN-GIT.md).
    """
    used = gapp("fleet", "use", fleet_in_a_git_repo)
    assert used.exit_code == 0, f"{used.output}{used.exception!r}"
    for command in commands(settings):
        result = gapp(*command)
        assert result.exit_code == 0, f"gapp {' '.join(command)}: {result.output}{result.exception!r}"
    log = subprocess.run(["git", "-C", fleet_in_a_git_repo, "log", "--format=%s", "main"],
                         capture_output=True, text=True, check=True).stdout
    assert "gapp: add my-app" in log and "gapp: remove prod" in log
