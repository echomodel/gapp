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
def gapp_user(account: str | None = None) -> dict:
    """View or set the global gcloud account.

    Args:
        account: Google account email for gcloud commands.
    """
    from gapp.admin.sdk.core import GappSDK
    sdk = GappSDK()
    if account is not None:
        sdk.set_account(account)
    return {"account": sdk.get_account()}


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
) -> dict:
    """Set up GCP foundation for a gapp solution.

    Enables APIs, creates the per-solution GCS bucket and labels it
    (gapp-deployment, gapp-version), and marks the
    project gapp=hosted once (never from a CI run).

    First-time install requires project_id. Subsequent runs are idempotent:
    discovery finds the project among those marked gapp=hosted.

    Args:
        project_id: GCP project ID. Required for first-time install.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().setup(project_id)


@_tool()
def gapp_deploy(
    ref: str | None = None,
    dry_run: bool = False,
    project_id: str | None = None,
    rebuild: bool = False,
    replace_domain: bool = False,
) -> dict:
    """Deploy a gapp solution to Cloud Run (build + terraform apply).

    Prerequisites: gapp_init and gapp_setup must have been run first.
    Always deploys the app in the current directory.

    Custom domains are guarded. The deploy stops before Terraform when
    the domain in gapp.yaml is not verified by the deploying identity
    (the error lists the verification steps), and when it would replace
    or remove a live domain mapping unless replace_domain is True. A
    redeploy that keeps the same domain is unaffected.

    Args:
        ref: Git ref to build from (commit, tag, branch). Defaults to HEAD.
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
        ref=ref, dry_run=dry_run, project_id=project_id,
        rebuild=rebuild, replace_domain=replace_domain,
    )


@_tool()
def gapp_secret_get(name: str, plaintext: bool = False) -> dict:
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
    """
    from gapp.admin.sdk.secrets import get_secret
    return get_secret(name, plaintext=plaintext)


@_tool()
def gapp_secret_set(name: str, value: str) -> dict:
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
    """
    from gapp.admin.sdk.secrets import set_secret
    return set_secret(name, value)


@_tool()
def gapp_secret_list(name: str | None = None) -> dict:
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
        name: Deployment name. Defaults to the current directory's app.
    """
    from gapp.admin.sdk.secrets import list_secrets
    return list_secrets(name=name)


@_tool()
def gapp_ci_init(repo: str | None = None, owner: str | None = None, create: bool = False) -> dict:
    """Show, find, set or create the fleet repo for this machine: the only tool that sets it.

    gapp has two fleet systems that do not talk to each other: the root
    tools (gapp_setup, gapp_deploy, gapp_status, gapp_list) deploy the
    local checkout to GCP with gcloud; the gapp_ci_* tools manage the fleet
    through GitHub only, never gcloud (except gapp_ci_project_setup) and
    never the working directory. The fleet repo is a private GitHub repo
    holding gapp/deployments.yaml (one entry per deployment) and gapp's three
    workflows; it is never the deployed app's repo.

    Without `repo`: the saved fleet repo and how it was set (fleet_repo,
    set_by); when none is saved or `owner` is given, one search of that
    owner's repos (the gh user's by default) for the gapp-ci topic: one
    match is saved, several are listed in `found` with ok false, none gives
    the create step. With `repo` (owner/name): sets it when it exists and
    installs or updates gapp's files in it (`files`); with `create`, makes
    it private, tagged and populated first. A repo that does not exist, or
    exists when `create` is asked, comes back as ok false with the exact
    command. Removes an empty gapp/solutions.yaml left by gapp 3.9 and
    reports a non-empty one (`legacy_solutions`).

    Every gapp_ci_* tool returns ok and, when false, next_step {action,
    hint}: a plain sentence with the exact fix. Anything that could not be
    read says so in GitHub's words ("Couldn't check ..."); nothing is
    called missing unless GitHub confirmed it.

    Args:
        repo: The fleet repo as owner/name.
        owner: GitHub owner to search for the gapp-ci topic (default: the gh user).
        create: Create `repo` as a new private fleet repo.
    """
    from gapp.admin.sdk.ci import init_fleet
    return init_fleet(repo, owner=owner, create=create)


@_tool()
def gapp_ci_project_setup(project: str) -> dict:
    """Make the fleet repo trusted to deploy to a GCP project (once per project, idempotent).

    The only gapp_ci_* tool that touches GCP. Creates or confirms the
    Workload Identity pool and provider and the gapp-deploy service
    account in the project, binds the pool to the fleet repo, and records
    the deploy identity in the fleet repo as the GitHub Environment named
    after the project (GAPP_WIF_PROVIDER, GAPP_DEPLOY_SERVICE_ACCOUNT).

    Requires gcloud signed in as an Owner of the project and gh signed in
    as an admin of the fleet repo (its owner, for a personal repo). A
    machine without them gets ok false and a next_step naming the
    requirement; run it where they are, then gapp_ci_setup works from
    anywhere. `changed` says whether anything was created or recorded.

    Args:
        project: GCP project id.
    """
    from gapp.admin.sdk.ci import setup_ci_project
    return setup_ci_project(project)


@_tool()
def gapp_ci_sources(action: str, owner: str | None = None) -> dict:
    """List, add to, or remove from the GitHub owners whose repos may be deployed from the fleet.

    The list is the fleet repo's repository variable GAPP_ALLOWED_SOURCES;
    the register run refuses a repo whose owner is not on it, and
    gapp_ci_setup then returns the one-time step. Needs gh with write
    access to the fleet repo (read access for "list").

    Args:
        action: "add", "remove" or "list".
        owner: The GitHub owner (user or organization) for add and remove.

    Returns ok, owners (the list after the change), changed, and next_step
    when gh cannot read or write the variable.
    """
    from gapp.admin.sdk.ci import manage_sources
    return manage_sources(action, owner)


@_tool()
def gapp_ci_setup(
    name: str | None = None,
    repo: str | None = None,
    project: str | None = None,
    domain: str | None = None,
    copy_of: str | None = None,
    update: bool = False,
    watch: bool = True,
) -> dict:
    """Add or update a deployment of the fleet: `name` deploys `repo` to `project`. Never uses gcloud.

    Mirrors gapp_setup for the fleet. The name defaults to the repo's name,
    derived only from `repo` (never from a checkout). The same name again
    updates the deployment's custom domain: `domain` sets it ("" removes
    it); every other setting comes from the app repo's own gapp.yaml, and
    the same call drops any key gapp.yaml does not have. A repo already deployed under another name is
    refused: update that deployment, or add another with `copy_of`, which
    copies the source's repo and gapp.yaml keys but never its id, domain,
    deploy history or secret values, and defaults `project` to the
    source's.

    The change is written by the fleet repo's register run with its own
    token, so this machine needs only a gh that can dispatch workflows.
    Two one-time steps come back as ok false with the exact message: the
    project not yet trusted (gapp_ci_project_setup, which needs gcloud as
    an Owner) and the owner not yet allowed (gapp_ci_sources add). Other
    refusals (name taken by another repo, repo already deployed, id longer
    than 27 characters) are plain sentences too.

    Args:
        name: Deployment name (default: the repo's name). Required with copy_of.
        repo: The repo to deploy, owner/name. Never the fleet repo.
        project: GCP project id. Required unless copy_of (then the source's by default).
        domain: Custom domain the deploy maps; "" removes it; None keeps it.
        copy_of: An existing deployment to copy.
        update: Apply a change to an existing entry. Without it, a change comes back as ok false with what would change (`changes_shown`).
        watch: Wait for the register run (default True); False returns after the dispatch.

    `lines` and `lines_after` carry the same text the CLI prints.
    The result's action is added, updated or unchanged; dispatched, run_url,
    result_code and messages describe the run; next_step what to do when it
    did not succeed.
    """
    from gapp.admin.sdk.ci import setup_deployment
    return setup_deployment(name, repo=repo, project=project, domain=domain, update=update,
                            copy_of=copy_of, watch=watch)


@_tool()
def gapp_ci_rename(old: str, new: str, watch: bool = True) -> dict:
    """Rename a deployment. Its id and every GCP resource stay as they are.

    Args:
        old: The deployment's current name.
        new: Its new name (unique in the fleet).
        watch: Wait for the register run (default True).
    """
    from gapp.admin.sdk.ci import rename_deployment
    return rename_deployment(old, new, watch=watch)


@_tool()
def gapp_ci_deploy(name: str, ref: str = "main", watch: bool = True) -> dict:
    """Deploy one deployment through the fleet repo's deploy workflow. Mirrors gapp_deploy for the fleet.

    Dispatches gapp-deploy.yml with the deployment's name; the run reads
    its entry from gapp/deployments.yaml, merges the entry's gapp.yaml keys
    over the repo's file and deploys through the project's GitHub
    Environment. Needs only a gh login that can dispatch workflows; no
    gcloud or terraform. A name that is not a deployment comes back as ok
    false naming gapp_ci_setup; a failed run with its link.

    Args:
        name: The deployment.
        ref: Git ref of the deployed repo (default: main).
        watch: Stream the run to completion (default True).
    """
    from gapp.admin.sdk.ci import deploy_deployment
    return deploy_deployment(name, ref=ref, watch=watch)


@_tool()
def gapp_ci_trigger(name: str, ref: str = "main", watch: bool = True) -> dict:
    """Former name of gapp_ci_deploy; use that. Kept for one release.

    Args:
        name: The deployment.
        ref: Git ref to deploy (default: main).
        watch: Stream the run to completion (default True).
    """
    from gapp.admin.sdk.ci import deploy_deployment
    return deploy_deployment(name, ref=ref, watch=watch)


@_tool()
def gapp_ci_status(name: str, live: bool = False) -> dict:
    """One deployment's state. Mirrors gapp_status for the fleet.

    Fast by default and GitHub only: registered, id (what the GCP resources
    are keyed by), repo, project, domain, overlay (the gapp.yaml keys the
    entry sets), last_deploy (the newest run named "Deploy <name> (<ref>)",
    or that runs could not be read), problem (an entry a deploy would
    refuse) and next_step.

    With live=True it dispatches the fleet repo's read-only status
    workflow, which runs gapp status for the deployment's id on a runner
    authenticated as the project's existing deploy identity, and returns
    that result as `live` (the same dict gapp_status returns: deployment
    services and URLs, domain state, next_step). Takes about as long as a
    runner start. Nothing is stored in the fleet repo.

    Args:
        name: The deployment.
        live: Ask the project for the live service state (slow).
    """
    from gapp.admin.sdk.ci import status_deployment, status_live
    return status_live(name) if live else status_deployment(name)


@_tool()
def gapp_ci_list(repo: str | None = None, verbose: bool = False) -> dict:
    """The fleet's deployments, optionally one repo's. Mirrors gapp_list for the fleet.

    Never touches GCP. Each deployment has name, id, repo, project, domain
    and the gapp.yaml keys it sets. With verbose, also the fleet repo's
    four files (present, current, or not checked), its variables, and the
    per-solution workflows left from gapp 3.x.

    Args:
        repo: Only the deployments of this repo (owner/name).
        verbose: Add the fleet repo's files, variables and legacy workflows.
    """
    from gapp.admin.sdk.ci import list_deployments
    return list_deployments(repo, verbose=verbose)


@_tool()
def gapp_status(name: str | None = None, project: str | None = None) -> dict:
    """Infrastructure health check for a gapp solution (the direct system).

    Reads deployment labels and Terraform state; never GitHub, never the CI
    system (use gapp_ci_status for that). Returns initialized,
    deployment.project, deployment.pending, deployment.services, domain
    (the live custom domain mapping's state: pending_dns, pending_cert,
    active, or error, with the CNAME target to set), and next_step with
    the recommended action. It never fails for a missing or unusable
    Google Cloud identity: that comes back as next_step "sign in" with
    the login command.

    Args:
        name: Deployment name. Defaults to the current directory's app.
        project: Optional explicit GCP project id, skipping the hosted-project search.
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().status(name=name, project=project).model_dump()


@_tool()
def gapp_list(project_limit: int = 50) -> dict:
    """List deployments found on projects marked gapp=hosted.

    Args:
        project_limit: Max hosted projects to scan (default 50).
    """
    from gapp.admin.sdk.core import GappSDK
    return GappSDK().list_apps(project_limit=project_limit)


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
