"""Global test fixtures for gapp."""

import pytest
import os
import yaml
import subprocess
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME to a temp dir and initialize a default profile."""
    config_dir = tmp_path / "gapp"
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    
    # Initialize config.yaml with a default profile
    config_file = config_dir / "config.yaml"
    default_config = {
        "active": "default",
        "profiles": {
            "default": {
                "discovery": "on",
                "account": "test-user@example.com"
            }
        }
    }
    with open(config_file, "w") as f:
        yaml.dump(default_config, f)
    
    return config_dir


@pytest.fixture(autouse=True)
def mock_git(monkeypatch):
    """Mock git calls to return the current CWD as the git root."""
    orig_run = subprocess.run
    
    def _mock_run(args, **kwargs):
        if "git" in args and "rev-parse" in args:
            class MockProc:
                returncode = 0
                stdout = str(kwargs.get("cwd", os.getcwd())) + "\n"
            return MockProc()
        return orig_run(args, **kwargs)
    
    monkeypatch.setattr(subprocess, "run", _mock_run)
