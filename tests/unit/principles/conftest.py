"""A machine nothing has been set up on: empty config and cache folders,
standing in a folder that is not a clone, with real git."""

import subprocess

import pytest
from click.testing import CliRunner

from gapp.admin.cli.main import main

CLOUD_PROGRAMS = ("gcloud", "terraform", "gh")


@pytest.fixture(autouse=True)
def mock_git():
    """Real git, in place of the suite-wide stand-in."""


@pytest.fixture(autouse=True)
def fresh_machine(monkeypatch, tmp_path):
    config, cache, here = tmp_path / "fresh-config", tmp_path / "fresh-cache", tmp_path / "elsewhere"
    for folder in (config, cache, here):
        folder.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.chdir(here)
    return config


@pytest.fixture
def no_cloud(monkeypatch):
    """Any call to gcloud, terraform or gh fails the test."""
    real_run, real_popen = subprocess.run, subprocess.Popen

    def program(args):
        first = args[0] if isinstance(args, (list, tuple)) and args else str(args).split()[0]
        return str(first).rsplit("/", 1)[-1]

    def run(args, *a, **k):
        assert program(args) not in CLOUD_PROGRAMS, f"a fleet command ran {program(args)}: {args}"
        return real_run(args, *a, **k)

    class Popen(real_popen):
        def __init__(self, args, *a, **k):
            assert program(args) not in CLOUD_PROGRAMS, f"a fleet command ran {program(args)}: {args}"
            super().__init__(args, *a, **k)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", Popen)


def gapp(*args):
    return CliRunner().invoke(main, list(args))


def fleet_file(config):
    return config / "gapp" / "fleets" / "default" / "gapp.yaml"
