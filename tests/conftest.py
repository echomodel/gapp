"""Global test fixtures for gapp."""

import pytest
import os


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    """Redirect XDG_CONFIG_HOME to a temp dir for every test."""
    test_config = tmp_path / "config"
    test_config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_config))


@pytest.fixture(autouse=True)
def enable_mock_provider(monkeypatch):
    """Force all tests to use the DummyCloudProvider."""
    monkeypatch.setenv("GAPP_MOCK_PROVIDER", "true")
