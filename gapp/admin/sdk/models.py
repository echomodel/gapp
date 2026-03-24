"""Typed response models for gapp SDK operations."""

from pydantic import BaseModel, Field


class NextStep(BaseModel):
    """Guidance for what the user should do next."""
    action: str | None = Field(None, description="Action identifier: init, setup, deploy, etc.")
    hint: str | None = Field(None, description="Human-readable guidance message.")


class ServiceStatus(BaseModel):
    name: str
    url: str
    healthy: bool
    auth_enabled: bool = False
    mcp_path: str | None = None


class DeploymentInfo(BaseModel):
    project: str | None = Field(None, description="GCP project ID.")
    pending: bool = True
    services: list[ServiceStatus] = []


class StatusResult(BaseModel):
    initialized: bool = False
    name: str | None = None
    repo_path: str | None = None
    deployment: DeploymentInfo | None = None
    next_step: NextStep | None = None


class McpStatusResult(BaseModel):
    name: str
    project_id: str | None = None
    deployed: bool = False
    url: str | None = None
    mcp_url: str | None = None
    healthy: bool | None = None
    auth_enabled: bool = False
    tools: list[str] | None = None
    next_step: NextStep | None = None
    error: str | None = None


class McpSolution(BaseModel):
    name: str
    project_id: str | None = None
    mcp_path: str
    repo_path: str | None = None


class ClientScope(BaseModel):
    registered: bool = False
    command: str = ""


class ClientConfig(BaseModel):
    user: ClientScope | None = None
    project: ClientScope | None = None


class ClaudeAiConfig(BaseModel):
    url: str = ""


class ClientConfigs(BaseModel):
    claude_code: ClientConfig | None = None
    gemini_cli: ClientConfig | None = None
    claude_ai: ClaudeAiConfig | None = None


class ConnectResult(BaseModel):
    name: str
    project_id: str | None = None
    deployed: bool = False
    url: str | None = None
    mcp_url: str | None = None
    healthy: bool | None = None
    auth_enabled: bool = False
    tools: list[str] | None = None
    token: str | None = None
    token_masked: str | None = None
    clients: ClientConfigs = Field(default_factory=ClientConfigs)
    next_step: NextStep | None = None
    error: str | None = None
