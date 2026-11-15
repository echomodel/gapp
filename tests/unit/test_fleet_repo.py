"""A fleet kept in a git repo: one file, `gapp.yaml`, changed by ordinary
commits. Plain git, no CI. These tests use a real bare repo as the remote."""

import subprocess

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import definitions, entryfile, fleetrepo, fleetstore
from gapp.admin.sdk.config import get_fleet_repo
from gapp.admin.sdk.definitions import Refused
from gapp.admin.sdk.fleet import FleetRefusal
from gapp.admin.sdk.fleetstore import FleetUnavailable


def git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(autouse=True)
def mock_git():
    """Real git here, in place of the suite-wide stand-in."""


@pytest.fixture(autouse=True)
def machine(monkeypatch, tmp_path):
    """A machine with its own config and cache, a git identity, and none of
    the hooks the machine running the tests may have configured."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    for key, value in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(hooks),
                       "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev" + "@" + "example.test",
                       "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev" + "@" + "example.test",
                       "GIT_TERMINAL_PROMPT": "0"}.items():
        monkeypatch.setenv(key, value)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return tmp_path


@pytest.fixture
def remote(machine):
    """An empty repo somewhere else, standing in for the fleet repo."""
    path = machine / "fleet.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(path)], check=True)
    return str(path)


def in_remote(remote, rev="main"):
    return git(remote, "show", f"{rev}:gapp.yaml")


def log(remote):
    return git(remote, "log", "--format=%s", "main").splitlines()


# --- moving the fleet ---


def test_moving_to_a_repo_carries_the_fleet_as_one_file_and_nothing_else(remote):
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    local_file = fleetstore.location().path
    result = fleetstore.use(remote)
    assert result["moved"] and result["carried"] and result["deployments"] == 1
    assert get_fleet_repo() == remote
    assert in_remote(remote).startswith("format: 1\ndeployments:\n  my-app:\n")
    assert git(remote, "ls-tree", "--name-only", "-r", "main").splitlines() == ["gapp.yaml"]
    assert log(remote) == ["gapp: the fleet moves here"]
    assert not local_file.exists() and local_file.with_name("gapp.yaml.bak").exists()


def test_a_repo_with_no_commit_yet_is_an_empty_fleet(remote):
    assert fleetstore.use(remote)["deployments"] == 0
    assert fleetstore.read() == {}
    assert git(remote, "for-each-ref") == ""            # choosing it wrote nothing


def test_each_change_is_one_commit_that_says_what_changed(remote):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    definitions.set_value("public", "true", deployment="my-app")
    definitions.env_set("MAX_USERS", "25", deployment="my-app")
    definitions.rename("my-app", "prod")
    assert log(remote) == ["gapp: rename my-app to prod", "gapp: change env MAX_USERS on my-app",
                           "gapp: change public on my-app", "gapp: add my-app"]
    assert fleetstore.read()["prod"]["settings"] == {"public": True, "env": {"MAX_USERS": {"value": "25"}}}


def test_remove_gives_the_git_command_that_shows_what_it_took_out(remote):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    definitions.set_value("public", "true", deployment="my-app")
    command = definitions.remove("my-app")["see_removed"]
    assert command.startswith("git -C ") and " show " in command
    shown = subprocess.run(command.split(), capture_output=True, text=True).stdout
    assert "gapp: remove my-app" in shown and "-      public: true" in shown


def test_a_change_that_changes_nothing_makes_no_commit(remote):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    definitions.set_value("public", "false", deployment="my-app")
    assert definitions.set_value("public", "false", deployment="my-app")["changed"] is False
    assert len(log(remote)) == 2


def test_another_machine_sees_the_same_fleet(remote, machine, monkeypatch):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(machine / "other-xdg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(machine / "other-cache"))
    assert fleetstore.location().kind == "local" and fleetstore.read() == {}
    fleetstore.use(remote)
    assert list(fleetstore.read()) == ["my-app"]
    definitions.set_value("ref", "main", deployment="my-app")
    assert "ref: main" in in_remote(remote)


def test_moving_is_refused_when_both_places_hold_deployments(remote):
    fleetstore.use(remote)
    definitions.add("theirs", project="my-project", repo="acme/theirs")
    fleetstore.use("local")
    assert list(fleetstore.read()) == ["theirs"]
    git(remote, "update-ref", "refs/heads/main", git(remote, "rev-parse", "main"))   # the repo still holds it
    with pytest.raises(FleetRefusal) as caught:
        fleetstore.use(remote)
    assert "gapp does not merge two fleets" in caught.value.message
    assert fleetstore.location().kind == "local"


def test_moving_back_to_this_machine_carries_the_fleet_and_forgets_the_repo(remote):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    result = fleetstore.use("local")
    assert result["where"].startswith("a file on this machine") and get_fleet_repo() is None
    assert list(fleetstore.read()) == ["my-app"]


def test_choosing_the_place_it_already_is_changes_nothing(remote):
    fleetstore.use(remote)
    assert fleetstore.use(remote)["moved"] is False


# --- when it goes wrong ---


def test_a_repo_that_cannot_be_reached_is_an_error_not_an_empty_fleet(machine):
    with pytest.raises(FleetUnavailable) as caught:
        fleetstore.use(str(machine / "no-such-repo.git"))
    assert "reading it failed" in str(caught.value)
    assert fleetstore.location().kind == "local"


def test_a_file_in_the_repo_with_no_format_is_refused_and_not_written_over(remote, machine):
    work = machine / "work"
    subprocess.run(["git", "clone", "-q", remote, str(work)], check=True, capture_output=True)
    (work / "gapp.yaml").write_text("public: true\n")
    git(work, "add", "gapp.yaml")
    git(work, "commit", "-q", "-m", "an app's old file")
    git(work, "push", "-q", "origin", "HEAD:main")
    with pytest.raises(FleetUnavailable) as caught:
        fleetstore.use(remote)
    assert "has no 'format' at its top" in str(caught.value)
    assert in_remote(remote) == "public: true"


def test_a_change_made_elsewhere_while_this_ran_is_refused_and_can_be_run_again(remote, machine, monkeypatch):
    fleetstore.use(remote)
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    work = machine / "work"
    subprocess.run(["git", "clone", "-q", remote, str(work)], check=True, capture_output=True)
    real_git, raced = fleetrepo._git, []

    def someone_else_pushes_first(args, cwd=None):
        if args[0] == "push" and not raced:
            raced.append(True)
            (work / "note.txt").write_text("someone else\n")
            git(work, "add", "note.txt")
            git(work, "commit", "-q", "-m", "someone else")
            git(work, "push", "-q", "origin", "HEAD:main")
        return real_git(args, cwd)

    monkeypatch.setattr(fleetrepo, "_git", someone_else_pushes_first)
    with pytest.raises(Refused) as caught:
        definitions.set_value("public", "true", deployment="my-app")
    assert "changed while this ran, so nothing was changed. Run it again." in caught.value.message
    assert "public" not in in_remote(remote)
    definitions.set_value("public", "true", deployment="my-app")
    assert "public: true" in in_remote(remote) and "someone else" in log(remote)


# --- the commands ---


def test_the_fleet_commands_say_where_it_is_kept(remote):
    definitions.add("my-app", project="my-project", repo="acme/my-app")
    out = CliRunner().invoke(main, ["fleet", "use", remote]).output
    assert f"Your fleet is now kept in {remote} (gapp.yaml).\nIt holds 1 deployment, carried across.\n" in out
    assert CliRunner().invoke(main, ["fleet"]).output.startswith(f"Your fleet is kept in {remote} (gapp.yaml).\n")
    assert CliRunner().invoke(main, ["list"]).output.startswith(f"Your fleet is kept in {remote} (gapp.yaml).\n")
    assert CliRunner().invoke(main, ["fleet", "use", remote]).output == f"Your fleet is already kept in {remote} (gapp.yaml).\n"


def test_a_repo_is_written_the_way_a_definition_writes_one():
    assert fleetrepo.normalize("https://github.com/acme/fleet.git") == "acme/fleet"
    assert fleetrepo.normalize("https://gitlab.com/group/fleet") == "gitlab.com/group/fleet"
    assert fleetrepo.normalize("git@github.com:acme/fleet.git") == "git@github.com:acme/fleet.git"
    assert fleetrepo.url_of("acme/fleet") == "https://github.com/acme/fleet.git"
    assert fleetrepo.url_of("gitlab.com/group/fleet") == "https://gitlab.com/group/fleet.git"
    assert fleetrepo.url_of("/srv/git/fleet.git") == "/srv/git/fleet.git"
