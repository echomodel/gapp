"""Typed response models for gapp SDK operations."""

from pydantic import BaseModel, Field


class DomainStatus(BaseModel):
    """Custom domain mapping status."""
    name: str = Field(description="The custom domain (e.g., mcp.example.com)")
    status: str = Field(description="pending_dns, pending_cert, active, or error")
    cname_target: str = Field(default="ghs.googlehosted.com", description="Required CNAME target")
    detail: str | None = Field(None, description="Additional info (e.g., current DNS resolution)")


