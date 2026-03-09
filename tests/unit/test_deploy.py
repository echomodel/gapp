"""Tests for gapp.sdk.deploy — Dockerfile generation and tfvars building."""

from gapp.sdk.deploy import _generate_dockerfile, _build_tfvars, _secret_name_to_env_var


def test_generate_dockerfile():
    config = {
        "entrypoint": "monarch.mcp.server:mcp_app",
        "port": 8080,
    }
    dockerfile = _generate_dockerfile(config)
    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert '"monarch.mcp.server:mcp_app"' in dockerfile
    assert '"8080"' in dockerfile


def test_secret_name_to_env_var():
    assert _secret_name_to_env_var("monarch-token") == "MONARCH_TOKEN"
    assert _secret_name_to_env_var("gemini-api-key") == "GEMINI_API_KEY"


def test_build_tfvars():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "public": False,
        "env": {},
    }
    tfvars = _build_tfvars("my-app", "my-project", "img:latest", config)
    assert tfvars["project_id"] == "my-project"
    assert tfvars["service_name"] == "my-app"
    assert tfvars["image"] == "img:latest"
    assert tfvars["public"] is False
    assert tfvars["secrets"] == {}


def test_build_tfvars_with_secrets():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "public": False,
        "env": {},
    }
    secrets = {"monarch-token": {"description": "Auth token"}}
    tfvars = _build_tfvars("my-app", "proj", "img:latest", config, secrets)
    assert tfvars["secrets"] == {"MONARCH_TOKEN": "monarch-token"}


def test_build_tfvars_with_env():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "public": True,
        "env": {"DB_HOST": "localhost"},
    }
    tfvars = _build_tfvars("my-app", "proj", "img:latest", config)
    assert tfvars["env"] == {"DB_HOST": "localhost"}
    assert tfvars["public"] is True
