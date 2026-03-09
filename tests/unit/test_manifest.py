"""Tests for gapp.sdk.manifest — manifest.yaml parsing."""

from pathlib import Path

from gapp.sdk.manifest import (
    get_entrypoint,
    get_prerequisite_secrets,
    get_required_apis,
    get_service_config,
    get_solution_name,
    load_manifest,
)


def test_load_manifest_missing(tmp_path):
    """No gapp.yaml returns empty dict."""
    result = load_manifest(tmp_path)
    assert result == {}


def test_load_manifest_exists(tmp_path):
    (tmp_path / "gapp.yaml").write_text(
        "solution:\n"
        "  name: my-app\n"
        "prerequisites:\n"
        "  apis:\n"
        "    - run.googleapis.com\n"
    )
    result = load_manifest(tmp_path)
    assert result["solution"]["name"] == "my-app"
    assert "run.googleapis.com" in result["prerequisites"]["apis"]


def test_solution_name_from_manifest():
    manifest = {"solution": {"name": "custom-name"}}
    assert get_solution_name(manifest, Path("/tmp/fallback")) == "custom-name"


def test_solution_name_falls_back_to_dir_name():
    manifest = {}
    assert get_solution_name(manifest, Path("/tmp/my-repo")) == "my-repo"


def test_prerequisite_secrets():
    manifest = {"prerequisites": {"secrets": {"API_KEY": {"description": "API key"}}}}
    assert "API_KEY" in get_prerequisite_secrets(manifest)


def test_prerequisite_secrets_empty():
    assert get_prerequisite_secrets({}) == {}


def test_required_apis():
    manifest = {"prerequisites": {"apis": ["run.googleapis.com"]}}
    assert get_required_apis(manifest) == ["run.googleapis.com"]


def test_required_apis_empty():
    assert get_required_apis({}) == []


def test_entrypoint():
    manifest = {"service": {"entrypoint": "my_app.mcp.server:mcp_app"}}
    assert get_entrypoint(manifest) == "my_app.mcp.server:mcp_app"


def test_entrypoint_missing():
    assert get_entrypoint({}) is None


def test_service_config_defaults():
    manifest = {"service": {"entrypoint": "app:main"}}
    config = get_service_config(manifest)
    assert config["entrypoint"] == "app:main"
    assert config["port"] == 8080
    assert config["memory"] == "512Mi"
    assert config["cpu"] == "1"
    assert config["max_instances"] == 1
    assert config["public"] is False
    assert config["env"] == {}


def test_service_config_overrides():
    manifest = {"service": {
        "entrypoint": "app:main",
        "memory": "1Gi",
        "public": True,
        "env": {"FOO": "bar"},
    }}
    config = get_service_config(manifest)
    assert config["port"] == 8080
    assert config["memory"] == "1Gi"
    assert config["public"] is True
    assert config["env"] == {"FOO": "bar"}
