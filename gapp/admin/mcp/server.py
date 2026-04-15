"""gapp admin MCP server — stdio-only, exposes admin SDK operations as tools.

Every tool is wrapped with `_catch_manifest_errors` so schema validation
failures from gapp.yaml surface as structured JSON (identical payload
to the CLI's stderr output and the SDK's `ManifestValidationError.to_dict()`).
"""

from functools import wraps

from mcp.server.fastmcp import FastMCP

from gapp.admin.sdk.schema import ManifestValidationError

mcp = FastMCP("gapp-admin")


def _catch_manifest_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ManifestValidationError as e:
            return e.to_dict()
    return wrapper


def _tool():
    """Decorator stack: @_tool() applies manifest error catching then registers with MCP."""
    def decorator(fn):
        return mcp.tool()(_catch_manifest_errors(fn))
    return decorator


@_tool()
def gapp_schema() -> dict:
    """Return the live gapp.yaml JSON Schema (from the Pydantic model).

    Use this to see every valid field, its type, whether it's required,
    and its description. This is the single source of truth; all
    validation and error responses derive from the same model.
    """
    from gapp.admin.sdk.schema import get_schema
    return get_schema()


@_tool()
def gapp_init(
    entrypoint: str | None = None,
    mcp_path: str | None = None,
    secrets: dict | None = None,
    domain: str | None = None,
) -> dict:
    """Initialize or configure a gapp solution in the current repo.

    Idempotent. Creates gapp.yaml on first call. Also used to update
    gapp configuration settings later — e.g., change entrypoint,
    add secrets, set custom domain. Only non-None parameters are
    written; omitted parameters leave existing values unchanged.

    Args:
        entrypoint: ASGI entrypoint (module:app).
        mcp_path: MCP endpoint path (e.g., /mcp).
        secrets: Dict of secret name to description for prerequisites.
        domain: Custom domain to map to the service (e.g., mcp.example.com).
            Requires a CNAME record pointing to ghs.googlehosted.com.
            Pass empty string to remove an existing domain mapping.
    """
    from gapp.admin.sdk.init import init_solution
    return init_solution(
        entrypoint=entrypoint,
        mcp_path=mcp_path,
        secrets=secrets,
        domain=domain,
    )


@_tool()
def gapp_setup(project_id: str | None = None, solution: str | None = None) -> dict:
    """Set up GCP foundation for a gapp solution.

    Enables APIs, creates per-solution GCS bucket, and labels the project.

    Args:
        project_id: GCP project ID. Uses saved value if omitted.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.setup import setup_solution
    return setup_solution(project_id, solution=solution)


@_tool()
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


@_tool()
def gapp_deploy(
    auto_approve: bool = True,
    ref: str | None = None,
    solution: str | None = None,
    build_ref: str | None = None,
    build_check_timeout: int = 10,
) -> dict:
    """Deploy a gapp solution to Cloud Run (terraform apply after a build).

    Canonical flow: call gapp_build first, get a build_id back, then
    call gapp_deploy with build_ref=<build_id>. gapp_deploy polls the
    build and runs terraform once the image is ready. If the build is
    still running when the timeout expires, returns a "running" status
    — call again with the same build_ref to retry.

    build_ref is required unless the package-bundled feature flag
    ``allow_one_step_deploy_tool`` is enabled (off by default — the
    one-step path times out on non-trivial builds).

    Prerequisites: gapp_init and gapp_setup must have been run first.

    Args:
        auto_approve: Skip Terraform confirmation prompt (default: True).
        ref: Git ref to deploy (commit, tag, branch). Skips dirty tree check.
        solution: Solution name. Defaults to current directory's solution.
        build_ref: Cloud Build ID from a prior gapp_build call.
        build_check_timeout: Max seconds to poll (default/minimum: 10).
    """
    from gapp.admin.sdk.features import is_enabled
    if build_ref is None and not is_enabled("allow_one_step_deploy_tool"):
        return {
            "error": "one_step_deploy_disabled",
            "message": (
                "gapp_deploy requires a build_ref from a prior gapp_build call. "
                "Run gapp_build first, then call gapp_deploy(build_ref=<build_id>). "
                "The one-step build+deploy path is disabled because it times out "
                "on any non-trivial build."
            ),
        }

    from gapp.admin.sdk.deploy import deploy_solution
    return deploy_solution(
        auto_approve=auto_approve, ref=ref, solution=solution,
        build_ref=build_ref, build_check_timeout=build_check_timeout,
    )


@_tool()
def gapp_secret_get(name: str, plaintext: bool = False, solution: str | None = None) -> dict:
    """Get a secret from GCP Secret Manager.

    Use this to retrieve secrets that gapp manages for a deployed solution —
    for example, the signing key needed to configure an admin client after
    deploy, or any other secret declared in gapp.yaml.

    Pass the secret's short name as declared in gapp.yaml's secret.name
    field (e.g. "signing-key"). gapp prefixes this with the solution name
    to produce the full Secret Manager ID automatically.

    By default returns a SHA-256 hash prefix and length — enough to confirm
    the secret exists and verify identity without exposing the value. Set
    plaintext=True to get the actual value (e.g. when you need to pass it
    to an admin CLI for user management).

    Default response:  {"name": "signing-key", "secret_id": "my-app-signing-key", "hash": "a1b2...", "length": 43}
    With plaintext:    {"name": "signing-key", "secret_id": "my-app-signing-key", "value": "the-actual-value"}

    IMPORTANT: Before deploying, use gapp_secret_list to confirm all
    non-generated secrets have values. Deploying with missing secrets
    will fail.

    Args:
        name: The secret's short name from gapp.yaml (e.g. "signing-key").
        plaintext: If True, return the actual secret value. Default False.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import get_secret
    return get_secret(name, plaintext=plaintext, solution=solution)


@_tool()
def gapp_secret_set(name: str, value: str, solution: str | None = None) -> dict:
    """Store a secret value in GCP Secret Manager for a deployed solution.

    Use this to populate secrets that the solution needs at runtime but
    that gapp does not auto-generate — for example, an upstream API key
    or third-party credential. The secret must be declared in gapp.yaml's
    env section with a secret.name field.

    For secrets with generate: true, gapp creates the value automatically
    during deploy — you don't need this tool for those.

    Pass the secret's short name as declared in gapp.yaml's secret.name
    field (e.g. "api-key"). gapp prefixes this with the solution name
    to produce the full Secret Manager ID automatically.

    Args:
        name: The secret's short name from gapp.yaml (e.g. "api-key").
        value: The secret value to store.
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import set_secret
    return set_secret(name, value, solution=solution)


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
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


@_tool()
def gapp_deployments_list() -> dict:
    """List GCP projects with deployed gapp solutions.

    Returns all GCP projects that have gapp-* labels, with the solutions
    deployed to each. The default project (most solutions) is highlighted.
    Use this to discover available projects when setting up a new solution.
    """
    from gapp.admin.sdk.deployments import list_deployments
    return list_deployments()


@_tool()
def gapp_list(available: bool = False) -> list[dict]:
    """List registered gapp solutions.

    Args:
        available: Include remote GitHub solutions.
    """
    from gapp.admin.sdk.solutions import list_solutions
    return list_solutions(include_remote=available)


@_tool()
def gapp_mcp_status(solution: str | None = None) -> dict:
    """MCP health check with tool enumeration for a gapp solution.

    Returns MCP URL, health, auth status, and list of available tools.
    """
    from gapp.admin.sdk.mcp_status import mcp_status
    return mcp_status(solution).model_dump()


@_tool()
def gapp_mcp_list() -> list[dict]:
    """List gapp solutions that have MCP endpoints configured."""
    from gapp.admin.sdk.mcp_status import mcp_list
    return [s.model_dump() for s in mcp_list()]


@_tool()
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


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
