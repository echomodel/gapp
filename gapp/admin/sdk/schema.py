"""Single source of truth for the gapp.yaml schema.

The Pydantic `Manifest` model below is the ONLY definition of what
gapp.yaml may contain. Validation, error responses, and on-demand
schema dumps (`gapp manifest schema`, `gapp_schema` MCP tool) all derive from
this model at call time. There is no separate JSON Schema file to
keep in sync — if tooling needs JSON Schema, it runs `gapp manifest schema`.

When adding or changing a field: edit the model here. Error messages,
the `gapp manifest schema` output, and the `gapp_schema` MCP tool all pick up
the change automatically.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class _StrictModel(BaseModel):
    """Reject unknown fields so typos surface as validation errors."""
    model_config = ConfigDict(extra="forbid")


class SecretSpec(_StrictModel):
    """A secret-backed env var configuration."""
    name: str = Field(description="Short secret name (becomes '<solution>-<name>' in Secret Manager).")
    generate: bool = Field(default=False, description="Auto-generate a random value on first deploy if missing.")


class EnvEntry(_StrictModel):
    """A single environment variable declaration."""
    name: str = Field(description="Environment variable name as seen by the service.")
    value: str | None = Field(default=None, description="Plain string value. Mutually exclusive with 'secret'.")
    secret: SecretSpec | bool | None = Field(
        default=None,
        description="Secret-backed value. Prefer a dict with 'name' (and optional 'generate').",
    )

    @model_validator(mode="after")
    def _value_xor_secret(self) -> "EnvEntry":
        has_value = self.value is not None
        has_secret = self.secret is not None and self.secret is not False
        if has_value and has_secret:
            raise ValueError("env entry cannot set both 'value' and 'secret'")
        return self


class ServiceSpec(_StrictModel):
    """Cloud Run service configuration."""
    entrypoint: str | None = Field(default=None, description="ASGI module:app — gapp wraps with uvicorn.")
    cmd: str | None = Field(default=None, description="Exact command to run in the container.")
    mcp_path: str | None = Field(default=None, description="MCP endpoint path (e.g. /mcp).")
    memory: str | None = Field(default=None, description="Memory limit (e.g. 512Mi).")
    cpu: str | None = Field(default=None, description="CPU limit (e.g. '1').")
    max_instances: int | None = Field(default=None, description="Max Cloud Run instances.")
    port: int | None = Field(default=None, description="Container port (default 8080).")
    env: dict[str, str] | None = Field(
        default=None,
        description="Legacy dict form of env vars. Prefer the top-level 'env' list.",
    )


class PrerequisiteSecret(_StrictModel):
    description: str | None = Field(default=None, description="Human-readable purpose of the secret.")


class Prerequisites(_StrictModel):
    apis: list[str] = Field(default_factory=list, description="GCP APIs to enable.")
    secrets: dict[str, PrerequisiteSecret] = Field(
        default_factory=dict,
        description="Named prerequisite secrets in Secret Manager.",
    )


class Manifest(_StrictModel):
    """Top-level gapp.yaml schema."""
    name: str | None = Field(default=None, description="Solution name override (falls back to repo directory name).")
    paths: list[str] = Field(default_factory=list, description="Subpaths for multi-service repos.")
    domain: str | None = Field(default=None, description="Custom domain (subdomain only, CNAME to ghs.googlehosted.com).")
    public: bool | None = Field(default=None, description="Allow unauthenticated HTTP access?")
    env: list[EnvEntry] = Field(default_factory=list, description="Environment variables for the service.")
    service: ServiceSpec | None = Field(default=None, description="Service runtime configuration.")
    prerequisites: Prerequisites | None = Field(default=None, description="GCP APIs and secrets required.")


class ManifestValidationError(ValueError):
    """Raised when gapp.yaml fails schema validation.

    Carries a structured `issues` list and exposes `to_dict()` for
    uniform JSON delivery through the SDK, CLI, and MCP layers.
    """

    def __init__(self, issues: list[dict]):
        self.issues = issues
        super().__init__(self._format_text())

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "manifest_invalid",
            "message": "gapp.yaml failed schema validation.",
            "issues": self.issues,
            "hint": (
                "Fix each issue above. Run `gapp manifest schema` to see every valid field "
                "with its type, description, and whether it's required. The `schema` "
                "field below is the same content, embedded for convenience."
            ),
            "schema": Manifest.model_json_schema(),
        }

    def _format_text(self) -> str:
        lines = ["gapp.yaml is invalid:"]
        for issue in self.issues:
            lines.append(f"  {issue['path']}: {issue['message']}")
        lines.append("")
        lines.append("Run `gapp manifest schema` for the full list of valid fields.")
        return "\n".join(lines)


def validate_manifest(data: dict[str, Any]) -> Manifest:
    """Validate a loaded gapp.yaml dict.

    Raises ManifestValidationError with a structured issues list on failure.
    """
    if not data:
        return Manifest()
    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        raise ManifestValidationError(_issues_from(e)) from None


def _issues_from(err: ValidationError) -> list[dict]:
    issues = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "<root>"
        typ = e["type"]
        if typ == "extra_forbidden":
            msg = "unknown field (check for typos)"
        elif typ == "missing":
            msg = "required field is missing"
        else:
            msg = e["msg"]
        issues.append({"path": loc, "message": msg, "type": typ})
    return issues


def get_schema() -> dict[str, Any]:
    """Return the live JSON Schema derived from the Pydantic model.

    Used by `gapp manifest schema` (CLI) and `gapp_schema` (MCP tool). No file
    is ever written — the schema is always generated fresh from the
    model in this module.
    """
    return Manifest.model_json_schema()
