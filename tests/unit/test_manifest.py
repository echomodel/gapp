"""Tests for gapp.sdk.manifest — manifest.yaml parsing."""

from pathlib import Path

from gapp.admin.sdk.manifest import (
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
        "name: my-app\n"
        "prerequisites:\n"
        "  apis:\n"
        "    - run.googleapis.com\n"
    )
    result = load_manifest(tmp_path)
    assert result["name"] == "my-app"
    assert "run.googleapis.com" in result["prerequisites"]["apis"]


def test_solution_name_from_manifest():
    manifest = {"name": "custom-name"}
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


# --- New env var support ---

from gapp.admin.sdk.manifest import get_domain, get_env_vars, resolve_env_vars


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


# --- Schema validation ---

import pytest

from gapp.admin.sdk.schema import ManifestValidationError, Manifest, validate_manifest


def _write_yaml(tmp_path, body: str):
    (tmp_path / "gapp.yaml").write_text(body)


def test_valid_manifest_loads(tmp_path):
    _write_yaml(tmp_path, """
public: true
domain: mcp.example.com
env:
  - name: LOG_LEVEL
    value: INFO
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true
service:
  entrypoint: app:main
prerequisites:
  apis:
    - run.googleapis.com
""")
    result = load_manifest(tmp_path)
    assert result["domain"] == "mcp.example.com"
    assert result["env"][1]["secret"]["name"] == "signing-key"


def test_secret_missing_name_rejected(tmp_path):
    """The exact silent-skip case from issue #26: secret dict without name."""
    _write_yaml(tmp_path, """
env:
  - name: SIGNING_KEY
    secret:
      generate: true
""")
    with pytest.raises(ManifestValidationError) as exc:
        load_manifest(tmp_path)
    msg = str(exc.value)
    assert "env.0.secret" in msg
    assert "name" in msg


def test_unknown_top_level_field_rejected(tmp_path):
    _write_yaml(tmp_path, "domian: mcp.example.com\n")
    with pytest.raises(ManifestValidationError, match="domian"):
        load_manifest(tmp_path)


def test_unknown_service_field_rejected(tmp_path):
    _write_yaml(tmp_path, "service:\n  entrypnt: app:main\n")
    with pytest.raises(ManifestValidationError, match="entrypnt"):
        load_manifest(tmp_path)


def test_unknown_env_entry_field_rejected(tmp_path):
    _write_yaml(tmp_path, """
env:
  - name: X
    valu: y
""")
    with pytest.raises(ManifestValidationError, match="valu"):
        load_manifest(tmp_path)


def test_env_entry_missing_name_rejected(tmp_path):
    """env[] entries must declare a 'name' field."""
    _write_yaml(tmp_path, """
env:
  - value: INFO
""")
    with pytest.raises(ManifestValidationError) as exc:
        load_manifest(tmp_path)
    msg = str(exc.value)
    assert "env.0.name" in msg
    assert "required" in msg.lower() or "missing" in msg.lower()


def test_prerequisite_secret_unknown_field_rejected(tmp_path):
    """Nested dicts (prerequisites.secrets.<name>) also reject typos."""
    _write_yaml(tmp_path, """
prerequisites:
  secrets:
    api-token:
      desription: typo of description
""")
    with pytest.raises(ManifestValidationError, match="desription"):
        load_manifest(tmp_path)


def test_service_max_instances_wrong_type_rejected(tmp_path):
    _write_yaml(tmp_path, """
service:
  max_instances: "lots"
""")
    with pytest.raises(ManifestValidationError, match="max_instances"):
        load_manifest(tmp_path)


def test_env_value_and_secret_mutually_exclusive(tmp_path):
    _write_yaml(tmp_path, """
env:
  - name: X
    value: plain
    secret:
      name: x
""")
    with pytest.raises(ManifestValidationError, match="both"):
        load_manifest(tmp_path)


def test_removed_service_auth_field_rejected(tmp_path):
    """gapp no longer handles auth — service.auth must fail schema validation."""
    _write_yaml(tmp_path, "service:\n  auth: bearer\n")
    with pytest.raises(ManifestValidationError, match="auth"):
        load_manifest(tmp_path)


def test_removed_service_runtime_field_rejected(tmp_path):
    """gapp no longer ships a runtime wrapper — service.runtime is unknown."""
    _write_yaml(tmp_path, "service:\n  runtime: v1.0.0\n")
    with pytest.raises(ManifestValidationError, match="runtime"):
        load_manifest(tmp_path)


def test_removed_top_level_auth_rejected(tmp_path):
    """Top-level auth:{framework:...} block was dropped — must now fail."""
    _write_yaml(tmp_path, "auth:\n  framework: app-user\n")
    with pytest.raises(ManifestValidationError, match="auth"):
        load_manifest(tmp_path)


def test_type_mismatch_rejected(tmp_path):
    _write_yaml(tmp_path, "public: not-a-bool-string-too\n")
    # "not-a-bool-string-too" can't coerce; pydantic should reject
    with pytest.raises(ManifestValidationError):
        load_manifest(tmp_path)


def test_validate_manifest_empty_returns_empty_model():
    m = validate_manifest({})
    assert isinstance(m, Manifest)
    assert m.env == []


def test_empty_manifest_file_is_valid(tmp_path):
    _write_yaml(tmp_path, "")
    assert load_manifest(tmp_path) == {}


def test_json_schema_generation():
    """Schema must be exportable for tooling/docs."""
    schema = Manifest.model_json_schema()
    assert "properties" in schema
    assert "env" in schema["properties"]
