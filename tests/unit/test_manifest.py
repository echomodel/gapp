"""The settings a deploy reads, in the shape the deploy code takes them."""

import pytest

from gapp.admin.sdk.manifest import get_domain, get_env_vars, get_service_config, resolve_env_vars


def test_service_config_defaults():
    manifest = {"service": {"memory": "512Mi"}}
    config = get_service_config(manifest)
    assert "entrypoint" not in config
    assert config["memory"] == "512Mi"
    assert config["cpu"] == "1"
    assert config["max_instances"] == 1


def test_service_config_overrides():
    config = get_service_config({"service": {"memory": "1Gi"}})
    assert config["memory"] == "1Gi"


def test_get_domain():
    assert get_domain({"domain": "mcp.example.com"}) == "mcp.example.com"


def test_get_domain_absent():
    assert get_domain({}) is None


def test_get_env_vars_new_format():
    manifest = {
        "env": [
            {"name": "LOG_LEVEL", "value": "INFO"},
            {"name": "APP_KEY", "secret": {"generate": True}},
        ]
    }
    result = get_env_vars(manifest)
    assert len(result) == 2
    assert result[0]["name"] == "LOG_LEVEL"
    assert result[1]["secret"]["generate"] is True


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
