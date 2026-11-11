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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class _StrictModel(BaseModel):
    """Reject unknown fields so typos surface as validation errors."""
    model_config = ConfigDict(extra="forbid")


class SecretSpec(_StrictModel):
    """A secret-backed env var configuration."""
    name: str = Field(description="Short secret name, stored as '<app name>-<this name>' in Secret Manager. Set its value with `gapp secrets set <this name>`.")
    generate: bool = Field(default=False, description="true: gapp generates a random value on the first deploy if none is set.")


class EnvEntry(_StrictModel):
    """A single environment variable declaration."""
    name: str = Field(description="The variable's name, as the app reads it.")
    value: str | None = Field(default=None, description="A plain value. {{SOLUTION_DATA_PATH}} is the data volume's mount path and {{SOLUTION_NAME}} the app's name. Not with 'secret'.")
    secret: SecretSpec | None = Field(
        default=None,
        description="Take the value from a secret: a mapping with 'name' (and optional 'generate'). Not with 'value'.",
    )

    @field_validator("secret", mode="before")
    @classmethod
    def _secret_must_be_mapping(cls, v):
        # A bare `secret: true` used to validate but was ignored by deploy
        # and by `gapp secrets list`, so a user could ship a service with
        # no secret and no error. Refuse it with the shape to use instead.
        if isinstance(v, bool):
            raise ValueError(
                "secret must be a mapping, e.g. secret: {name: <short-name>} "
                "(add generate: true to auto-create the value); bare true/false is not supported"
            )
        return v

    @model_validator(mode="after")
    def _value_xor_secret(self) -> "EnvEntry":
        if self.value is not None and self.secret is not None:
            raise ValueError("env entry cannot set both 'value' and 'secret'")
        return self


class ServiceSpec(_StrictModel):
    """Cloud Run service configuration."""
    memory: str | None = Field(default=None, description="Memory per instance (default 512Mi).")
    cpu: str | None = Field(default=None, description="CPUs per instance (default 1).")
    max_instances: int | None = Field(default=None, description="Most instances Cloud Run runs at once (default 1).")
    port: int | None = Field(default=None, description="Not used: the service always listens on $PORT, which Cloud Run sets to 8080.")
    env: dict[str, str] | None = Field(
        default=None,
        description="Legacy dict form of env vars. Prefer the top-level 'env' list.",
    )


class PrerequisiteSecret(_StrictModel):
    description: str | None = Field(default=None, description="Human-readable purpose of the secret.")


class Prerequisites(_StrictModel):
    apis: list[str] = Field(default_factory=list, description="GCP APIs `gapp setup` enables besides the ones gapp needs.")
    secrets: dict[str, PrerequisiteSecret] = Field(
        default_factory=dict,
        description="Named prerequisite secrets in Secret Manager.",
    )


class Manifest(_StrictModel):
    """Top-level gapp.yaml schema."""
    name: str | None = Field(default=None, description="The deployment's name. It names the Cloud Run service, the data bucket and the secrets, so changing it makes a new deployment. Unset: the origin repo's name, else the folder's.")
    paths: list[str] = Field(default_factory=list, description="Subfolders that are each their own service, for a repo with several.")
    domain: str | None = Field(default=None, description="Custom subdomain mapped at deploy (CNAME it to ghs.googlehosted.com). Unset: the .run.app URL only.")
    public: bool | None = Field(default=None, description="true: anyone can call the URL, and the app does its own auth. Unset or false: only callers with Cloud Run invoker access.")
    env: list[EnvEntry] = Field(default_factory=list, description="Environment variables the service gets.")
    service: ServiceSpec | None = Field(default=None, description="Cloud Run sizing for each instance.")
    prerequisites: Prerequisites | None = Field(default=None, description="Extra GCP APIs to enable at setup.")


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
