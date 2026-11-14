"""The settings a deploy reads, in the shape the deploy code takes them
(`settings.as_manifest` makes it): variables as one list, sizing under
`service`, extra APIs under `prerequisites`."""


def get_service_config(manifest: dict) -> dict:
    """Return service configuration with defaults."""
    service = manifest.get("service", {})
    return {
        "memory": service.get("memory", "512Mi"),
        "cpu": service.get("cpu", "1"),
        "max_instances": service.get("max_instances", 1),
    }


def get_env_vars(manifest: dict) -> list[dict]:
    """Return the env var declarations from the manifest's `env` list:
    dicts with `name` and either `value` or `secret` ({name, generate})."""
    return manifest.get("env") or []


def get_domain(manifest: dict) -> str | None:
    """Return the custom domain if configured, else None."""
    return manifest.get("domain")


def get_public(manifest: dict) -> bool | None:
    """Return the public access setting if configured, else None."""
    val = manifest.get("public")
    if val is None:
        return None
    return bool(val)


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
                    raise ValueError(f"Unknown gapp variable: {{{{{var_name}}}}}.")
                if var_name not in gapp_vars:
                    raise ValueError(f"gapp variable {{{{{var_name}}}}} not available in this context.")
                return gapp_vars[var_name]
            entry["value"] = re.sub(r"\{\{(\w+)\}\}", replacer, entry["value"])
        result.append(entry)
    return result
