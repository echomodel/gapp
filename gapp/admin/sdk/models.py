"""Typed response models for gapp SDK operations."""

from pydantic import BaseModel, Field


class DomainStatus(BaseModel):
    """Custom domain mapping status."""
    name: str = Field(description="The custom domain (e.g., mcp.example.com)")
    status: str = Field(description="pending_dns, pending_cert, active, or error")
    cname_target: str = Field(default="ghs.googlehosted.com", description="Required CNAME target")
    detail: str | None = Field(None, description="Additional info (e.g., current DNS resolution)")


class NextStep(BaseModel):
    """Guidance for what the user should do next."""
    action: str | None = Field(None, description="Action identifier: init, setup, deploy, etc.")
    hint: str | None = Field(None, description="Human-readable guidance message.")


class ServiceStatus(BaseModel):
    name: str
    url: str
    healthy: bool


class DeploymentInfo(BaseModel):
    project: str | None = Field(None, description="GCP project ID.")
    pending: bool = True
    services: list[ServiceStatus] = []


class StatusResult(BaseModel):
    initialized: bool = False
    name: str | None = None
    repo_path: str | None = None
    deployment: DeploymentInfo | None = None
    domain: DomainStatus | None = None
    next_step: NextStep | None = None
