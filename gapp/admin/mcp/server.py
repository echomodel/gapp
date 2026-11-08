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
    rebuild: bool = False,
    replace_domain: bool = False,
) -> dict:
    """Deploy a gapp solution to Cloud Run (build + terraform apply).

    Prerequisites: gapp_init and gapp_setup must have been run first.

    Custom domains are guarded. The deploy stops before Terraform when
    the domain in gapp.yaml is not verified by the deploying identity
    (the error lists the verification steps), and when it would replace
    or remove a live domain mapping unless replace_domain is True. A
    redeploy that keeps the same domain is unaffected.

    Args:
        ref: Git ref to build from (commit, tag, branch). Defaults to HEAD.
        solution: Solution name. Defaults to current directory's solution.
        env: Optional — disambiguates when multiple projects host this
             solution, and verifies the resolved project's bound env.
             Reserved values like "default" are rejected.
        dry_run: Preview only — do not build or apply.
        project_id: Explicit GCP project ID override. Discovered if omitted.
        rebuild: Force docker build even if an image already exists in
                 Artifact Registry for the resolved SHA. Default False —
                 existing images are reused for speed.
        replace_domain: Allow the deploy to replace or remove the live
                 custom domain mapping. Terraform destroys the old mapping
                 before creating the new one, so the old domain stops
                 serving. Default False — such a deploy is refused and the
                 error names the live domain.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().deploy(
        ref=ref, solution=solution, env=env, dry_run=dry_run, project_id=project_id,
        rebuild=rebuild, replace_domain=replace_domain,
    )


@_tool()
def gapp_secret_get(name: str, plaintext: bool = False, solution: str | None = None) -> dict:
    """Get a secret from GCP Secret Manager.

    Use this to retrieve secrets that gapp manages for a deployed solution —
    any secret declared in gapp.yaml's env section, including auto-generated
    ones (`generate: true`) whose values you may need post-deploy.

    Pass the secret's short name as declared in gapp.yaml's secret.name
    field (e.g. "app-key"). gapp prefixes this with the solution name
    to produce the full Secret Manager ID automatically.

    By default returns a SHA-256 hash prefix and length — enough to confirm
    the secret exists and verify identity without exposing the value. Set
    plaintext=True to get the actual value (e.g. when you need to pass it
    to an admin CLI for user management).

    Default response:  {"name": "app-key", "secret_id": "my-app-app-key", "hash": "a1b2...", "length": 43}
    With plaintext:    {"name": "app-key", "secret_id": "my-app-app-key", "value": "the-actual-value"}

    IMPORTANT: Before deploying, use gapp_secret_list to confirm all
    non-generated secrets have values. Deploying with missing secrets
    will fail.

    Args:
        name: The secret's short name from gapp.yaml (e.g. "app-key").
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
    - status: one of "ready", "missing", "missing-generate" (will be
              created on deploy), "unattached" (a secret with this ID
              exists in GCP but lacks the gapp-solution label),
              "conflict" (the ID is owned by a different solution), or
              "no-project" (no GCP project resolved yet).

    The response also includes:
    - orphans: secret IDs labeled for this solution but not in gapp.yaml
    - hints: structured remediation entries for unattached / conflict /
             orphan scenarios — each with a message and concrete `gcloud`
             commands. See docs/SECRETS.md for the full recovery guide.

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.secrets import list_secrets
    return list_secrets(solution=solution)


@_tool()
def gapp_ci_status(solution: str | None = None) -> dict:
    """The CI system's report: the operator's CI repo and the current solution.

    gapp has two fleet systems that do not talk to each other: the root
    tools (gapp_setup, gapp_deploy, gapp_status, secrets) are the direct
    system and need a Google Cloud identity; the gapp_ci_* tools are the
    CI system and need GitHub. This tool never touches GCP.

    Returns:
        ok: false when something stands in the way; then next_step holds
              {action, hint} with the exact step. Every gapp_ci_* tool
              returns ok and next_step this way instead of failing.
        repo: CI repo name (owner/name), or null if gapp_ci_init
              has not been run.
        files: for gapp's deploy workflow, register workflow and
              gapp/solutions.yaml: present, and (workflows) current
              against this gapp's templates.
        variables: whether GAPP_ALLOWED_SOURCES, HOSTING_GITHUB_APP_ID and
              HOSTING_GITHUB_APP_PRIVATE_KEY exist in the CI repo (null
              each when this machine cannot read the Actions API).
        solutions: every registered solution with repo, project, domain,
              gapp_ref and whether the project's Environment exists.
        environments: project id -> whether its Environment exists.
        legacy_workflows: solutions still deployed by a per-solution
              workflow file; register each, then delete the file.
        solution, registered: the current solution and whether it has an
              entry; solution_repo, domain, gapp_ref, environment and
              environment_record describe that entry and the project's
              Environment (GAPP_WIF_PROVIDER and GAPP_DEPLOY_SERVICE_ACCOUNT).
        next_step: {action, hint} naming the gap when a CI deploy would
              fail as things stand; null when gapp_ci_deploy can run.

    The CI setup sequence is:
    1. gapp_ci_init — designate the CI repo and install gapp's files (once per operator)
    2. gapp_ci_project_setup — trust the CI repo for a GCP project (once per project; gcloud)
    3. gapp_ci_sources(action="add", owner=...) — allow a source owner (once per owner)
    4. gapp_ci_setup — register a solution (per solution, idempotent)
    5. gapp_ci_deploy — deploy via GitHub Actions

    Args:
        solution: Solution name. Defaults to current directory's solution.
    """
    from gapp.admin.sdk.ci import get_ci_status
    return get_ci_status(solution=solution)


@_tool()
def gapp_ci_init(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo and install gapp's files in it.

    This is the first step in CI setup. The CI repo is a private GitHub
    repo that holds gapp's generic deploy workflow, register workflow and
    the data file gapp/solutions.yaml — it is NOT the solution repo.

    Needs a GitHub API write: a gh login that can write to the CI repo.
    Idempotent. Unless local_only, it installs or updates the two
    workflows and creates the data file (once, never overwritten) and
    reports whether the CI repo holds GAPP_ALLOWED_SOURCES (managed by
    gapp_ci_sources), HOSTING_GITHUB_APP_ID and
    HOSTING_GITHUB_APP_PRIVATE_KEY (private solution repos only), with
    the commands for the missing ones as next_step. It never sets them.
    Where the GitHub API refuses writes, ok is false and next_step says
    which machine to run it from.

    Args:
        repo: GitHub repo name or owner/name.
        local_only: Only write to local config, skip everything on GitHub.
    """
    from gapp.admin.sdk.ci import init_ci
    return init_ci(repo, local_only=local_only)


@_tool()
def gapp_ci_project_setup(project: str) -> dict:
    """Make the CI repo trusted to deploy to a GCP project (once per project, idempotent).

    The only gapp_ci_* tool that touches GCP. Creates or confirms the
    Workload Identity pool and provider and the gapp-deploy service
    account in the project, binds the pool to the CI repo, and records
    the deploy identity in the CI repo as the GitHub Environment named
    after the project (GAPP_WIF_PROVIDER, GAPP_DEPLOY_SERVICE_ACCOUNT).

    Requires gcloud signed in as an Owner of the project and gh signed in
    as an admin of the CI repo (its owner, for a personal repo). A machine
    without them gets ok false and a next_step naming the requirement;
    run it where they are, then gapp_ci_setup works from anywhere.

    Args:
        project: GCP project id.
    """
    from gapp.admin.sdk.ci import setup_ci_project
    return setup_ci_project(project)


@_tool()
def gapp_ci_sources(action: str, owner: str | None = None) -> dict:
    """List, add to, or remove from the GitHub owners allowed to deploy through the CI repo.

    The list is the CI repo's repository variable GAPP_ALLOWED_SOURCES;
    the register workflow refuses a solution repo whose owner is not on
    it, and gapp_ci_setup then returns the one-time step. Needs gh with
    write access to the CI repo (read access for "list").

    Args:
        action: "add", "remove" or "list".
        owner: The GitHub owner (user or organization) for add and remove.

    Returns ok, owners (the list after the change), changed, note, and
    next_step when gh cannot read or write the variable.
    """
    from gapp.admin.sdk.ci import manage_sources
    return manage_sources(action, owner)


@_tool()
def gapp_ci_setup(
    solution: str | None = None,
    solution_repo: str | None = None,
    project: str | None = None,
    domain: str | None = None,
    watch: bool = True,
) -> dict:
    """Register a solution for CI deploys. Never uses gcloud.

    Dispatches the register workflow in the CI repo with the solution's
    name, repo, project and domain, waits for the run and reports its
    result. The run validates (the project's Environment exists, the
    repo's owner is in GAPP_ALLOWED_SOURCES, the repo is reachable, no
    other project already holds the solution) and writes the entry in
    gapp/solutions.yaml with its own token, so this machine needs only a
    gh login that can dispatch workflows. Idempotent; re-running keeps
    the entry's domain unless `domain` is passed.

    Two one-time steps come back as ok false with the exact message:
    the project not yet trusted (run gapp_ci_project_setup, which needs
    gcloud as an Owner), and the source owner not yet allowed (run
    gapp_ci_sources add). Other refusals of the register run come back
    verbatim with the run link.

    Args:
        solution: Solution name. Defaults to the current directory's solution.
        solution_repo: The GitHub repo the pipeline deploys, as
            ``owner/name``, a bare name, or a github.com URL. Pass this
            (together with ``solution``) to register from anywhere — no
            local checkout required. Omit it only when running from
            inside the solution repo, where it is read from the ``origin``
            remote.
        project: GCP project id. Required: the CI system never looks a
            project up in GCP. A solution already registered on another
            project is refused.
        domain: Custom domain the CI deploy maps; it overrides gapp.yaml
            at deploy time, so a public solution repo can keep a
            placeholder there. None keeps the entry's existing domain;
            "" removes it.
        watch: Wait for the register run and report its conclusion and
            messages (default True). False returns after the dispatch
            with run_id and run_url set.

    The result's dispatched says whether the run was started, registered
    whether it succeeded, result_code the run's structured outcome,
    messages what it reported, and next_step what to do when it did not.
    """
    from gapp.admin.sdk.ci import setup_ci
    return setup_ci(
        solution=solution, solution_repo=solution_repo, project=project, domain=domain,
        watch=watch,
        command=f"gapp ci setup --project {project}"
        + (f" --solution {solution}" if solution else "")
        + (f" --solution-repo {solution_repo}" if solution_repo else "")
        + (f" --domain {domain}" if domain else ""),
    )


@_tool()
def gapp_ci_deploy(
    solution: str | None = None,
    ref: str = "main",
    watch: bool = True,
) -> dict:
    """Deploy a registered solution via GitHub Actions.

    Dispatches the CI repo's generic deploy workflow with the solution's
    name; the run reads the solution's entry from gapp/solutions.yaml and
    deploys through the project's GitHub Environment. Needs only a gh
    login that can dispatch workflows; no gcloud or terraform. A solution
    that is not registered is not deployed: ok is false and next_step
    names gapp_ci_setup. A failed run comes back with its link; its first
    steps name a missing credential or Environment variable in words.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        ref: Git ref to deploy (default: main).
        watch: Block and stream status until completion (default: True).
    """
    from gapp.admin.sdk.ci import deploy_ci
    return deploy_ci(solution=solution, ref=ref, watch=watch)


@_tool()
def gapp_ci_trigger(
    solution: str | None = None,
    ref: str = "main",
    watch: bool = True,
) -> dict:
    """Former name of gapp_ci_deploy; use that. Kept for one release.

    Args:
        solution: Solution name. Defaults to current directory's solution.
        ref: Git ref to deploy (default: main).
        watch: Block and stream status until completion (default: True).
    """
    from gapp.admin.sdk.ci import deploy_ci
    return deploy_ci(solution=solution, ref=ref, watch=watch)


@_tool()
def gapp_status(solution: str | None = None, env: str | None = None) -> dict:
    """Infrastructure health check for a gapp solution (the direct system).

    Reads GCP labels and Terraform state; never GitHub, never the CI
    system (use gapp_ci_status for that). Returns initialized,
    deployment.project, deployment.pending, deployment.services, domain
    (the live custom domain mapping's state: pending_dns, pending_cert,
    active, or error, with the CNAME target to set), and next_step with
    the recommended action. It never fails for a missing or unusable
    Google Cloud identity: that comes back as next_step "sign in" with
    the login command.

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
def gapp_projects_list() -> dict:
    """List GCP projects that have a gapp-env binding.

    No owner scoping — gapp-env is a single project-wide label with no owner
    segment, so the same set is returned regardless of the active owner.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().list_target_projects()


@_tool()
def gapp_list(all_owners: bool = False, project_limit: int = 50) -> dict:
    """List deployed gapp solutions discovered via GCP project labels.

    Args:
        all_owners: When False (default), scope listing to the active owner
            namespace (or global apps if no owner is configured). When True,
            return apps across every owner namespace. Maps to the CLI flag
            `--all` on `gapp list`.
        project_limit: Max projects to scan (default 50).
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().list_apps(all_owners=all_owners, project_limit=project_limit)


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
