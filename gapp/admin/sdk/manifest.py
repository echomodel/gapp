"""Parse and validate gapp.yaml files."""

from pathlib import Path

import yaml


def load_manifest(repo_path: Path) -> dict:
    """Load gapp.yaml from a repo. Returns empty dict if missing."""
    manifest_path = repo_path / "gapp.yaml"
    if not manifest_path.exists():
        return {}
    with open(manifest_path) as f:
        return yaml.safe_load(f) or {}


def save_manifest(repo_path: Path, manifest: dict) -> None:
    """Write manifest dict back to gapp.yaml."""
    manifest_path = repo_path / "gapp.yaml"
    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)


def get_solution_name(manifest: dict, repo_path: Path) -> str:
    """Derive solution name from manifest or repo directory name."""
    solution = manifest.get("solution", {})
    return solution.get("name", repo_path.name)


def get_prerequisite_secrets(manifest: dict) -> dict:
    """Return prerequisite secrets from the manifest."""
    return manifest.get("prerequisites", {}).get("secrets", {})


def get_required_apis(manifest: dict) -> list:
    """Return required GCP APIs from the manifest."""
    return manifest.get("prerequisites", {}).get("apis", [])


def get_entrypoint(manifest: dict) -> str | None:
    """Return the service entrypoint from the manifest."""
    return manifest.get("service", {}).get("entrypoint")


VALID_AUTH_STRATEGIES = {"bearer", "google_oauth2"}


def get_auth_config(manifest: dict) -> dict | None:
    """Return auth configuration if enabled, else None.

    Format: auth: bearer  (or google_oauth2)
    Absent means no auth.
    """
    auth = manifest.get("service", {}).get("auth")
    if not auth:
        return None
    if auth not in VALID_AUTH_STRATEGIES:
        raise ValueError(
            f"Invalid auth strategy '{auth}'. Must be one of: {', '.join(sorted(VALID_AUTH_STRATEGIES))}"
        )
    return {"enabled": True, "strategy": auth}


def get_runtime_ref(manifest: dict) -> str | None:
    """Return the gapp runtime git ref if configured, else None."""
    return manifest.get("service", {}).get("runtime")


def get_mcp_path(manifest: dict) -> str | None:
    """Return the MCP endpoint path if configured, else None."""
    return manifest.get("service", {}).get("mcp_path")


def get_service_config(manifest: dict) -> dict:
    """Return service configuration with defaults."""
    service = manifest.get("service", {})
    return {
        "entrypoint": service.get("entrypoint"),
        "port": 8080,
        "memory": service.get("memory", "512Mi"),
        "cpu": service.get("cpu", "1"),
        "max_instances": service.get("max_instances", 1),
        "env": service.get("env", {}),
    }


def get_env_vars(manifest: dict) -> list[dict]:
    """Return env var declarations from the manifest.

    Supports both the new `env` section and the legacy `service.env` dict.
    New format:
        env:
          - name: LOG_LEVEL
            value: INFO
          - name: SIGNING_KEY
            secret:
              generate: true

    Legacy format (service.env dict):
        service:
          env:
            LOG_LEVEL: INFO

    Returns a normalized list of dicts, each with:
        name: str
        value: str | None (for plain env vars)
        secret: dict | bool | None (for secret-backed env vars)
    """
    # New format
    env_list = manifest.get("env", [])
    if env_list:
        return env_list

    # Legacy: service.env dict → normalize to list
    legacy = manifest.get("service", {}).get("env", {})
    if legacy and isinstance(legacy, dict):
        return [{"name": k, "value": v} for k, v in legacy.items()]

    return []


def get_public(manifest: dict) -> bool | None:
    """Return the public access setting if configured, else None."""
    val = manifest.get("public")
    if val is None:
        return None
    return bool(val)


def get_auth_framework(manifest: dict) -> str | None:
    """Return the auth framework hint if configured, else None.

    Format in gapp.yaml:
        auth:
          framework: app-user
    """
    auth = manifest.get("auth", {})
    if isinstance(auth, dict):
        return auth.get("framework")
    return None


# -- Substitution --

GAPP_VARIABLES = {
    "SOLUTION_DATA_PATH",
    "SOLUTION_NAME",
}


def resolve_env_vars(env_list: list[dict], gapp_vars: dict) -> list[dict]:
    """Resolve {{VARIABLE}} placeholders in env var values.

    Args:
        env_list: List of env var dicts from get_env_vars().
        gapp_vars: Dict of gapp-provided variable values
            (e.g., {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": "my-app"}).

    Returns: New list with placeholders replaced in value fields.
    """
    import re
    result = []
    for entry in env_list:
        entry = dict(entry)  # copy
        if "value" in entry and isinstance(entry["value"], str):
            def replacer(m):
                var_name = m.group(1)
                if var_name not in GAPP_VARIABLES:
                    raise ValueError(f"Unknown gapp variable: {{{{{var_name}}}}}. "
                                     f"Valid: {', '.join(sorted(GAPP_VARIABLES))}")
                if var_name not in gapp_vars:
                    raise ValueError(f"gapp variable {{{{{var_name}}}}} not available in this context.")
                return gapp_vars[var_name]
            entry["value"] = re.sub(r"\{\{(\w+)\}\}", replacer, entry["value"])
        result.append(entry)
    return result
