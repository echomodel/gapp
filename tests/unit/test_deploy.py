"""Tests for gapp.sdk.deploy — build and tfvars logic."""

from gapp.sdk.deploy import _build_tfvars, _secret_name_to_env_var


def test_secret_name_to_env_var():
    assert _secret_name_to_env_var("api-token") == "API_TOKEN"
    assert _secret_name_to_env_var("some-api-key") == "SOME_API_KEY"


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
    tfvars = _build_tfvars("my-app", "my-project", "img:abc123", config)
    assert tfvars["project_id"] == "my-project"
    assert tfvars["service_name"] == "my-app"
    assert tfvars["image"] == "img:abc123"
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
    secrets = {"api-token": {"description": "Auth token"}}
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config, secrets)
    assert tfvars["secrets"] == {"API_TOKEN": "api-token"}


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
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config)
    assert tfvars["env"] == {"DB_HOST": "localhost"}
    assert tfvars["public"] is True
