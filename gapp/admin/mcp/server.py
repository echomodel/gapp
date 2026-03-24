"""gapp admin MCP server — stdio-only, exposes admin SDK operations as tools."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gapp-admin")


@mcp.tool()
def gapp_init(
    entrypoint: str | None = None,
    mcp_path: str | None = None,
    auth: str | None = None,
    runtime: str | None = None,
    secrets: dict | None = None,
) -> dict:
    """Initialize or configure a gapp solution in the current repo.

    Idempotent. Creates gapp.yaml on first call. Also used to update
    gapp configuration settings later — e.g., enable auth, change
    entrypoint, add secrets. Only non-None parameters are written;
    omitted parameters leave existing values unchanged.

    Args:
        entrypoint: ASGI entrypoint (module:app).
        mcp_path: MCP endpoint path (e.g., /mcp).
        auth: Auth strategy — "bearer" or "google_oauth2". Absent means no auth.
        runtime: gapp git ref for the runtime wrapper version.
        secrets: Dict of secret name to description for prerequisites.
    """
    from gapp.admin.sdk.init import init_solution
    return init_solution(
        entrypoint=entrypoint,
        mcp_path=mcp_path,
        auth=auth,
        runtime=runtime,
        secrets=secrets,
    )


@mcp.tool()
def gapp_setup(project_id: str | None = None, solution: str | None = None) -> dict:
    """Set up GCP foundation for a gapp solution.

    Enables APIs, creates per-solution GCS bucket, and labels the project.

    Args:
        project_id: GCP project ID. Uses saved value if omitted.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.setup import setup_solution
    return setup_solution(project_id, solution=solution)


@mcp.tool()
def gapp_deploy(
    auto_approve: bool = True,
    ref: str | None = None,
    solution: str | None = None,
) -> dict:
    """Deploy a gapp solution to Cloud Run.

    Builds container via Cloud Build and deploys via Terraform.
    Requires a clean git tree unless ref is specified.

    Args:
        auto_approve: Skip Terraform confirmation prompt (default: True).
        ref: Git ref to deploy (commit, tag, branch). Skips dirty tree check.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.deploy import deploy_solution
    return deploy_solution(auto_approve=auto_approve, ref=ref, solution=solution)


@mcp.tool()
def gapp_secret_list(solution: str | None = None) -> dict:
    """List prerequisite secrets and their status in Secret Manager.

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import list_secrets
    return list_secrets(solution=solution)


@mcp.tool()
def gapp_secret_set(secret_name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in Secret Manager.

    Creates the secret if it doesn't exist, then adds a new version.

    Args:
        secret_name: Name of the secret (as declared in gapp.yaml).
        value: The secret value.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import set_secret
    return set_secret(secret_name, value, solution=solution)


@mcp.tool()
def gapp_ci_init(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo for GitHub Actions deployments.

    Args:
        repo: GitHub repo name or owner/name.
        local_only: Only write to local config, skip GitHub topic.
    """
    from gapp.admin.sdk.ci import init_ci
    return init_ci(repo, local_only=local_only)


@mcp.tool()
def gapp_ci_setup(solution: str | None = None) -> dict:
    """Wire a solution for CI/CD deployment.

    Creates Workload Identity Federation, service account, IAM bindings,
    and pushes the GitHub Actions workflow to the CI repo.

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.ci import setup_ci
    return setup_ci(solution=solution)


@mcp.tool()
def gapp_ci_trigger(
    solution: str | None = None,
    ref: str = "main",
    watch: bool = True,
) -> dict:
    """Trigger a CI deployment for a solution via GitHub Actions.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        ref: Git ref to deploy (default: main).
        watch: Block and stream status until completion (default: True).
    """
    from gapp.admin.sdk.ci import trigger_ci
    return trigger_ci(solution=solution, ref=ref, watch=watch)


@mcp.tool()
def gapp_status(solution: str | None = None) -> dict:
    """Infrastructure health check for a gapp solution.

    Returns deployment status, service URL, health, and guided next steps.
    """
    from gapp.admin.sdk.status import get_status
    return get_status(solution).model_dump()


@mcp.tool()
def gapp_deployments_list() -> dict:
    """List GCP projects with deployed gapp solutions.

    Returns all GCP projects that have gapp-* labels, with the solutions
    deployed to each. The default project (most solutions) is highlighted.
    Use this to discover available projects when setting up a new solution.
    """
    from gapp.admin.sdk.deployments import list_deployments
    return list_deployments()


@mcp.tool()
def gapp_list(available: bool = False) -> list[dict]:
    """List registered gapp solutions.

    Args:
        available: Include remote GitHub solutions.
    """
    from gapp.admin.sdk.solutions import list_solutions
    return list_solutions(include_remote=available)


@mcp.tool()
def gapp_mcp_status(solution: str | None = None) -> dict:
    """MCP health check with tool enumeration for a gapp solution.

    Returns MCP URL, health, auth status, and list of available tools.
    """
    from gapp.admin.sdk.mcp_status import mcp_status
    return mcp_status(solution).model_dump()


@mcp.tool()
def gapp_mcp_list() -> list[dict]:
    """List gapp solutions that have MCP endpoints configured."""
    from gapp.admin.sdk.mcp_status import mcp_list
    return [s.model_dump() for s in mcp_list()]


@mcp.tool()
def gapp_mcp_connect(solution: str | None = None, user: str | None = None) -> dict:
    """Generate MCP client connection info for a gapp solution.

    Shows connection details for Claude Code, Gemini CLI, and Claude.ai
    with registration status. If user email is specified, mints a real PAT.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        user: Email of registered user to mint a real PAT for.
    """
    from gapp.admin.sdk.mcp_status import mcp_connect
    return mcp_connect(solution, user=user).model_dump()


@mcp.tool()
def gapp_users_list(solution: str | None = None, limit: int = 10) -> dict:
    """List registered users for a gapp solution.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        limit: Maximum number of users to return.
    """
    from gapp.admin.sdk.users import list_users
    return list_users(limit=limit)


@mcp.tool()
def gapp_users_register(
    email: str,
    credential: str,
    strategy: str = "bearer",
) -> dict:
    """Register a user and store their upstream credential.

    Args:
        email: User's email address.
        credential: The upstream API token (e.g., Monarch session token).
        strategy: Credential strategy (default: bearer).
    """
    from gapp.admin.sdk.users import register_user
    return register_user(email, credential, strategy)


@mcp.tool()
def gapp_tokens_create(email: str, solution: str | None = None, duration_days: int = 3650) -> dict:
    """Create a signed PAT (JWT) for a registered user.

    Args:
        email: Email of the registered user.
        solution: Solution name. Defaults to current directory's solution.
        duration_days: Token validity in days (default: 3650 / ~10 years).
    """
    from gapp.admin.sdk.tokens import create_token
    return create_token(email, duration_days=duration_days, solution=solution)


@mcp.tool()
def gapp_tokens_revoke(email: str, solution: str | None = None) -> dict:
    """Invalidate all PATs for a user by setting revoke_before to now.

    Args:
        email: Email of the user whose tokens to revoke.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.tokens import revoke_tokens
    return revoke_tokens(email, solution=solution)


@mcp.tool()
def gapp_users_update(
    email: str,
    credential: str | None = None,
    revoke_before: str | None = None,
    solution: str | None = None,
) -> dict:
    """Update a user's credential or revocation timestamp.

    Args:
        email: User's email address.
        credential: New upstream credential value.
        revoke_before: ISO 8601 timestamp — reject tokens issued before this.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.users import update_user
    return update_user(email, credential=credential, revoke_before=revoke_before, solution=solution)


@mcp.tool()
def gapp_users_revoke(email: str, solution: str | None = None) -> dict:
    """Revoke a user by deleting their credential file from GCS.

    Args:
        email: User's email address.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.users import revoke_user
    return revoke_user(email, solution=solution)


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
