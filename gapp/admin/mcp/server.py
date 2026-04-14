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
    domain: str | None = None,
) -> dict:
    """Initialize or configure a gapp solution in the current repo.

    Idempotent. Creates gapp.yaml on first call. Also used to update
    gapp configuration settings later — e.g., enable auth, change
    entrypoint, add secrets, set custom domain. Only non-None
    parameters are written; omitted parameters leave existing values
    unchanged.

    Args:
        entrypoint: ASGI entrypoint (module:app).
        mcp_path: MCP endpoint path (e.g., /mcp).
        auth: Auth strategy — "bearer" or "google_oauth2". Absent means no auth.
        runtime: gapp git ref for the runtime wrapper version.
        secrets: Dict of secret name to description for prerequisites.
        domain: Custom domain to map to the service (e.g., mcp.example.com).
            Requires a CNAME record pointing to ghs.googlehosted.com.
            Pass empty string to remove an existing domain mapping.
    """
    from gapp.admin.sdk.init import init_solution
    return init_solution(
        entrypoint=entrypoint,
        mcp_path=mcp_path,
        auth=auth,
        runtime=runtime,
        secrets=secrets,
        domain=domain,
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
def gapp_build(solution: str | None = None) -> dict:
    """Submit a Cloud Build for a gapp solution (always async).

    Returns immediately with a build_id. Use gapp_deploy with
    build_ref=<build_id> to poll for completion and run terraform.

    Flow: gapp_build → gapp_deploy(build_ref=...) → done.

    Prerequisites: gapp_init and gapp_setup must have been run first.

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.deploy import start_build
    return start_build(solution=solution)


@mcp.tool()
def gapp_deploy(
    auto_approve: bool = True,
    ref: str | None = None,
    solution: str | None = None,
    build_ref: str | None = None,
    build_check_timeout: int = 10,
) -> dict:
    """Deploy a gapp solution to Cloud Run.

    Without build_ref: full blocking deploy (build + terraform).
    With build_ref: poll for build completion, then run terraform.
    If the build is still running when the timeout expires, returns
    a "running" status — call again with the same build_ref to retry.

    Prerequisites: gapp_init and gapp_setup must have been run first.

    Args:
        auto_approve: Skip Terraform confirmation prompt (default: True).
        ref: Git ref to deploy (commit, tag, branch). Skips dirty tree check.
        solution: Solution name. Defaults to current directory's solution.
        build_ref: Cloud Build ID from a prior gapp_build call.
        build_check_timeout: Max seconds to poll (default/minimum: 10).
    """
    from gapp.admin.sdk.deploy import deploy_solution
    return deploy_solution(
        auto_approve=auto_approve, ref=ref, solution=solution,
        build_ref=build_ref, build_check_timeout=build_check_timeout,
    )


@mcp.tool()
def gapp_secret_get(env_var_name: str, plaintext: bool = False, solution: str | None = None) -> dict:
    """Get a secret from GCP Secret Manager by its env var name.

    Use this to retrieve secrets that gapp manages for a deployed solution —
    for example, the SIGNING_KEY needed to configure an mcp-app admin client
    after deploy, or any other secret-backed env var declared in gapp.yaml.

    Pass the env var name exactly as it appears in gapp.yaml's env section
    (e.g. "SIGNING_KEY"). gapp resolves this to the actual Secret Manager
    ID automatically using the solution name as a prefix.

    By default returns a SHA-256 hash prefix and length — enough to confirm
    the secret exists and verify identity without exposing the value. Set
    plaintext=True to get the actual value (e.g. when you need to pass it
    to mcp-app's admin CLI for user management).

    Default response:  {"name": "SIGNING_KEY", "secret_id": "...", "hash": "a1b2...", "length": 43}
    With plaintext:    {"name": "SIGNING_KEY", "secret_id": "...", "value": "the-actual-value"}

    IMPORTANT: Before deploying, use gapp_secret_list to confirm all
    non-generated secrets have values. Deploying with missing secrets
    will fail.

    Args:
        env_var_name: The env var name from gapp.yaml (e.g. "SIGNING_KEY").
        plaintext: If True, return the actual secret value. Default False.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import get_secret
    return get_secret(env_var_name, plaintext=plaintext, solution=solution)


@mcp.tool()
def gapp_secret_set(env_var_name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in GCP Secret Manager for a deployed solution.

    Use this to populate secrets that the solution needs at runtime but
    that gapp does not auto-generate — for example, an upstream API key
    or third-party credential. The secret must be declared in gapp.yaml's
    env section with a secret.name field.

    For secrets with generate: true (like SIGNING_KEY), gapp creates the
    value automatically during deploy — you don't need this tool for those.

    The env_var_name is the name as it appears in gapp.yaml's env section.
    gapp resolves this to the actual Secret Manager ID automatically.

    Args:
        env_var_name: The env var name from gapp.yaml (e.g. "API_KEY").
        value: The secret value to store.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import set_secret
    return set_secret(env_var_name, value, solution=solution)


@mcp.tool()
def gapp_secret_list(solution: str | None = None) -> dict:
    """List all secret-backed env vars and whether they are ready for deploy.

    Shows each secret's env var name, resolved Secret Manager ID, whether
    it has a value set, and whether gapp auto-generates it on deploy.

    IMPORTANT: Call this BEFORE gapp_deploy. Secrets with generate: true
    are created automatically by gapp during deploy. All other secrets
    must be populated in advance with gapp_secret_set — deploying with
    missing secrets will fail.

    Each secret in the response has:
    - env_var: the name in gapp.yaml (what the app reads at runtime)
    - secret_id: the resolved Secret Manager ID
    - generate: true if gapp auto-creates this on deploy
    - status: "set", "empty", "not created"

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import list_secrets
    return list_secrets(solution=solution)


@mcp.tool()
def gapp_ci_status(solution: str | None = None) -> dict:
    """Check CI/CD readiness for the current solution.

    Returns:
        repo: CI repo name (owner/name), or null if gapp_ci_init
              has not been run.
        workflow: true if this solution has a GitHub Actions workflow
                  in the CI repo (gapp_ci_setup was run for it),
                  false otherwise.

    Both must be true before gapp_ci_trigger will work.

    The CI/CD setup sequence is:
    1. gapp_ci_init — designate the CI repo (once)
    2. gapp_ci_setup — wire a solution (per solution, idempotent)
    3. gapp_ci_trigger — deploy via GitHub Actions

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.ci import get_ci_status
    return get_ci_status(solution=solution)


@mcp.tool()
def gapp_ci_init(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo for GitHub Actions deployments.

    This is the first step in CI/CD setup. Must be called before
    gapp_ci_setup. The CI repo is a private GitHub repo that holds
    deployment workflows — it is NOT the solution repo.

    Check current state with gapp_ci_status before calling.

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

    Prerequisites: gapp_ci_init must have been called first to designate
    the CI repo. The solution must also have been initialized (gapp_init)
    and set up (gapp_setup) with a GCP project. Check readiness with
    gapp_ci_status.

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

    Prerequisites: gapp_ci_init and gapp_ci_setup must have been
    completed first. Check readiness with gapp_ci_status. This
    dispatches the workflow created by gapp_ci_setup. Does not
    require terraform locally.

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

    Returns initialized, deployment.project, deployment.pending,
    deployment.services, and next_step with the recommended action.

    If terraform or gcloud is unavailable, returns pending=true with
    a hint explaining why deployment state couldn't be checked. Use
    gapp_deployments_list to discover deployments via GCP labels
    without needing terraform.

    Args:
        solution: Solution name. Defaults to current directory's solution.
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
