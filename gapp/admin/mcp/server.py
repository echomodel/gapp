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
def gapp_user(account: str | None = None, app_owner: str | None = None) -> dict:
    """View or set the global gcloud account and app owner.

    Args:
        account: Google account email for gcloud commands.
        app_owner: Global app owner name for project labels.
    """
    from gapp.admin.sdk.core import GappSDK
    sdk = GappSDK()
    if account is not None:
        sdk.set_account(account)
    if app_owner is not None:
        sdk.set_owner(app_owner)
    return {
        "account": sdk.get_account(),
        "app_owner": sdk.get_owner(),
    }


@_tool()
def gapp_init(
    entrypoint: str | None = None,
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
        secrets: Dict of secret name to description for prerequisites.
        domain: Custom domain to map to the service (e.g., mcp.example.com).
            Requires a CNAME record pointing to ghs.googlehosted.com.
            Pass empty string to remove an existing domain mapping.
    """
    from gapp.admin.sdk.init import init_solution
    return init_solution(
        entrypoint=entrypoint,
        secrets=secrets,
        domain=domain,
    )


@_tool()
def gapp_setup(
    project_id: str | None = None,
    solution: str | None = None,
    env: str | None = None,
    force: bool = False,
) -> dict:
    """Set up GCP foundation for a gapp solution.

    Enables APIs, creates per-solution GCS bucket, and writes the solution
    label (gapp_<owner>_<solution>=v-N) on the project. NEVER writes the
    project's gapp-env binding — use gapp_projects_set_env for that.

    First-time install requires project_id (no labels exist to discover from).
    Subsequent runs are idempotent — discovery finds the existing project.

    Args:
        project_id: GCP project ID. Required for first-time install.
        solution: Solution name. Defaults to current directory's solution.
        env: Optional verification — must match the target project's
             bound env (gapp-env label). Reserved values like "default"
             are rejected; omit to skip verification.
        force: Override the Layer-1 cross-owner check that refuses
               installation when a different owner already has a solution
               with the same name on the target project.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().setup(project_id, solution=solution, env=env, force=force)


@_tool()
def gapp_deploy(
    ref: str | None = None,
    solution: str | None = None,
    env: str | None = None,
    dry_run: bool = False,
    project_id: str | None = None,
) -> dict:
    """Deploy a gapp solution to Cloud Run (build + terraform apply).

    Prerequisites: gapp_init and gapp_setup must have been run first.

    Args:
        ref: Git ref to deploy (commit, tag, branch). Defaults to HEAD.
        solution: Solution name. Defaults to current directory's solution.
        env: Optional — disambiguates when multiple projects host this
             solution, and verifies the resolved project's bound env.
             Reserved values like "default" are rejected.
        dry_run: Preview only — do not build or apply.
        project_id: Explicit GCP project ID override. Discovered if omitted.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().deploy(
        ref=ref, solution=solution, env=env, dry_run=dry_run, project_id=project_id,
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
def gapp_status(solution: str | None = None, env: str | None = None) -> dict:
    """Infrastructure health check for a gapp solution.

    Returns initialized, deployment.project, deployment.pending,
    deployment.services, and next_step with the recommended action.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        env: Optional — used to disambiguate when multiple projects host
             this solution.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().status(name=solution, env=env).model_dump()


@_tool()
def gapp_projects_set_env(project_id: str, env: str, force: bool = False) -> dict:
    """Bind a GCP project to a named env (writes the gapp-env label).

    The gapp-env label is the only place a project's env lives. Setup
    and deploy never write it. Reserved values like "default" are rejected.

    Args:
        project_id: GCP project ID.
        env: Environment name (e.g. "prod", "dev"). Must be non-empty.
        force: Required to overwrite an existing gapp-env value. Refuses
               anyway if the rebind would create cross-project corruption.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().set_project_env(project_id, env=env, force=force)


@_tool()
def gapp_projects_clear_env(project_id: str) -> dict:
    """Remove the gapp-env label from a project. Project becomes undefined-env.

    Args:
        project_id: GCP project ID.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().clear_project_env(project_id)


@_tool()
def gapp_projects_list(wide: bool = False) -> dict:
    """List GCP projects that have a gapp-env binding.

    Args:
        wide: Reserved. There is no owner-scoping for gapp-env in v-3.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().list_target_projects(wide=wide)


@_tool()
def gapp_list(wide: bool = False, project_limit: int = 50) -> dict:
    """List deployed gapp solutions discovered via GCP project labels.

    Args:
        wide: Show all apps across all owner namespaces (not just the active owner).
        project_limit: Max projects to scan (default 50).
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().list_apps(wide=wide, project_limit=project_limit)


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
