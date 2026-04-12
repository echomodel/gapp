"""Tests for gapp.sdk.manifest — manifest.yaml parsing."""

from pathlib import Path

from gapp.admin.sdk.manifest import (
    get_auth_config,
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
    assert config["env"] == {}


def test_service_config_overrides():
    manifest = {"service": {
        "entrypoint": "app:main",
        "memory": "1Gi",
        "env": {"FOO": "bar"},
    }}
    config = get_service_config(manifest)
    assert config["port"] == 8080
    assert config["memory"] == "1Gi"
    assert config["env"] == {"FOO": "bar"}


def test_auth_config_bearer():
    manifest = {"service": {"auth": "bearer"}}
    auth = get_auth_config(manifest)
    assert auth["enabled"] is True
    assert auth["strategy"] == "bearer"


def test_auth_config_google_oauth2():
    manifest = {"service": {"auth": "google_oauth2"}}
    auth = get_auth_config(manifest)
    assert auth["enabled"] is True
    assert auth["strategy"] == "google_oauth2"


def test_auth_config_absent():
    assert get_auth_config({}) is None
    assert get_auth_config({"service": {}}) is None


# --- New env var support ---

from gapp.admin.sdk.manifest import get_domain, get_env_vars, resolve_env_vars, get_auth_framework


def test_get_domain():
    assert get_domain({"domain": "mcp.example.com"}) == "mcp.example.com"


def test_get_domain_absent():
    assert get_domain({}) is None


# --- New env var support ---


def test_get_env_vars_new_format():
    manifest = {
        "env": [
            {"name": "LOG_LEVEL", "value": "INFO"},
            {"name": "SIGNING_KEY", "secret": {"generate": True}},
        ]
    }
    result = get_env_vars(manifest)
    assert len(result) == 2
    assert result[0]["name"] == "LOG_LEVEL"
    assert result[1]["secret"]["generate"] is True


def test_get_env_vars_legacy_format():
    manifest = {"service": {"env": {"DB_HOST": "localhost", "LOG_LEVEL": "DEBUG"}}}
    result = get_env_vars(manifest)
    assert len(result) == 2
    names = {e["name"] for e in result}
    assert names == {"DB_HOST", "LOG_LEVEL"}


def test_get_env_vars_empty():
    assert get_env_vars({}) == []


def test_resolve_env_vars_substitution():
    env_list = [
        {"name": "APP_DATA", "value": "{{SOLUTION_DATA_PATH}}/users"},
        {"name": "APP_NAME", "value": "{{SOLUTION_NAME}}"},
        {"name": "PLAIN", "value": "no-substitution"},
    ]
    gapp_vars = {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": "my-app"}
    result = resolve_env_vars(env_list, gapp_vars)
    assert result[0]["value"] == "/mnt/data/users"
    assert result[1]["value"] == "my-app"
    assert result[2]["value"] == "no-substitution"


def test_resolve_env_vars_unknown_variable():
    import pytest
    env_list = [{"name": "X", "value": "{{UNKNOWN_VAR}}"}]
    with pytest.raises(ValueError, match="Unknown gapp variable"):
        resolve_env_vars(env_list, {})


def test_get_auth_framework():
    assert get_auth_framework({"auth": {"framework": "app-user"}}) == "app-user"
    assert get_auth_framework({}) is None
    assert get_auth_framework({"auth": "bearer"}) is None
