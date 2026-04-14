"""Tests for gapp.sdk.deploy — build and tfvars logic."""

from gapp.admin.sdk.deploy import _build_tfvars, _get_template, _secret_name_to_env_var


def test_dockerfile_template_exists():
    path = _get_template("Dockerfile")
    assert path.exists()
    content = path.read_text()
    assert "ARG ENTRYPOINT" in content
    assert "uvicorn" in content
    assert "8080" in content


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
        "env": {},
    }
    tfvars = _build_tfvars("my-app", "my-project", "img:abc123", config)
    assert tfvars["project_id"] == "my-project"
    assert tfvars["service_name"] == "my-app"
    assert tfvars["image"] == "img:abc123"
    assert tfvars["data_bucket"] == "gapp-my-app-my-project"
    assert tfvars["secrets"] == {}


def test_build_tfvars_with_secrets():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
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
        "env": {"DB_HOST": "localhost"},
    }
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config)
    assert tfvars["env"] == {"DB_HOST": "localhost"}


def test_build_tfvars_auth_disabled():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {},
    }
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config)
    assert tfvars["auth_enabled"] is False
    assert "GAPP_APP" not in tfvars["env"]


def test_build_tfvars_auth_enabled():
    config = {
        "entrypoint": "monarch.mcp.server:mcp_app",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {"LOG_LEVEL": "INFO"},
    }
    auth = {"enabled": True, "strategy": "bearer"}
    tfvars = _build_tfvars("monarch-access", "proj", "img:abc123", config, auth_config=auth)
    assert tfvars["auth_enabled"] is True
    assert tfvars["data_bucket"] == "gapp-monarch-access-proj"
    assert tfvars["env"]["GAPP_APP"] == "monarch.mcp.server:mcp_app"
    assert tfvars["env"]["LOG_LEVEL"] == "INFO"


def test_build_tfvars_auth_does_not_mutate_original_env():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {"FOO": "bar"},
    }
    original_env = config["env"].copy()
    auth = {"enabled": True, "strategy": "bearer"}
    _build_tfvars("my-app", "proj", "img:abc123", config, auth_config=auth)
    assert config["env"] == original_env


def test_build_tfvars_with_env_vars_list():
    """Test new-format env vars from gapp.yaml env section."""
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {},
    }
    env_vars = [
        {"name": "APP_DATA_PATH", "value": "{{SOLUTION_DATA_PATH}}/users"},
        {"name": "LOG_LEVEL", "value": "INFO"},
        {"name": "SIGNING_KEY", "secret": {"name": "signing-key", "generate": True}},
    ]
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config, env_vars=env_vars)
    assert tfvars["env"]["APP_DATA_PATH"] == "/mnt/data/users"
    assert tfvars["env"]["LOG_LEVEL"] == "INFO"
    assert tfvars["secrets"]["SIGNING_KEY"] == "my-app-signing-key"


def test_build_tfvars_data_bucket_always_set():
    """Data bucket is always set regardless of auth."""
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {},
    }
    tfvars = _build_tfvars("food-agent", "proj", "img:abc123", config)
    assert tfvars["data_bucket"] == "gapp-food-agent-proj"
    assert tfvars["auth_enabled"] is False


def test_build_tfvars_with_domain():
    """Domain is passed through as custom_domain when set."""
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {},
    }
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config, domain="mcp.example.com")
    assert tfvars["custom_domain"] == "mcp.example.com"


def test_build_tfvars_without_domain():
    """No custom_domain key when domain is not set."""
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "env": {},
    }
    tfvars = _build_tfvars("my-app", "proj", "img:abc123", config)
    assert "custom_domain" not in tfvars


def test_dockerfile_template_supports_runtime_install():
    path = _get_template("Dockerfile")
    content = path.read_text()
    assert "RUNTIME_REF" in content
