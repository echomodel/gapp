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


def get_auth_config(manifest: dict) -> dict | None:
    """Return auth configuration if enabled, else None."""
    auth = manifest.get("service", {}).get("auth", {})
    if not auth.get("enabled"):
        return None
    return {
        "enabled": True,
        "strategy": auth.get("strategy", "bearer"),
    }


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
        "public": service.get("public", False),
        "env": service.get("env", {}),
    }
