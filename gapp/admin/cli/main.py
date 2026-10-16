"""gapp CLI — GCP App Deployer."""

import json as json_mod
import sys
import click

from gapp import __version__
from gapp.admin.sdk.core import (
    GappSDK, UNDEFINED_ENV_DISPLAY, GLOBAL_OWNER_DISPLAY,
)


@click.group()
@click.version_option(version=__version__)
@click.pass_context
def main(ctx):
    """GCP App Deployer — deploy Cloud Run services with Terraform."""
    ctx.obj = GappSDK()


# -- gapp config --

@main.group("config", invoke_without_command=True)
@click.pass_obj
def config_group(sdk: GappSDK):
    """View or set workstation configuration."""
    if click.get_current_context().invoked_subcommand is None:
        profile = sdk.get_active_profile()
        from gapp.admin.sdk.config import get_active_config
        cfg = get_active_config()
        click.echo(f"Active Profile: {profile}")
        click.echo("Settings:")
        click.echo(f"  gcloud account: {cfg.get('account', '(none)')}")
        click.echo(f"  app owner:      {cfg.get('owner', '(none)')}")
        click.echo(f"  discovery:      {cfg.get('discovery', 'on').upper()}")


@config_group.command("account")
@click.argument("email", required=False)
@click.pass_obj
def config_account(sdk: GappSDK, email):
    """View or set the global gcloud account email."""
    if email:
        try:
            sdk.set_account(email)
            click.echo(f"  gcloud account set to: {email}")
        except RuntimeError as e:
            click.echo(f"  Error: {e}", err=True)
            raise SystemExit(1)
    else:
        current = sdk.get_account()
        click.echo(current if current else "No gcloud account configured.")


@config_group.command("owner")
@click.argument("name", required=False)
@click.option("--unset", is_flag=True, help="Clear the current owner setting.")
@click.pass_obj
def config_owner(sdk: GappSDK, name, unset):
    """View or set the global app owner for project labels."""
    if unset:
        sdk.set_owner(None)
        click.echo("  App owner cleared.")
        return
    if name is not None:
        sdk.set_owner(name)
        click.echo(f"  App owner set to: {name}")
    else:
        current = sdk.get_owner()
        click.echo(current if current else "No app owner configured.")


@config_group.command("discovery")
@click.argument("state", type=click.Choice(["on", "off"]), required=False)
@click.pass_obj
def config_discovery(sdk: GappSDK, state):
    """Enable (on) or disable (off) GCP label discovery."""
    if state:
        sdk.set_discovery(state)
        click.echo(f"  Discovery turned {state.upper()}")
    else:
        click.echo("on" if sdk.is_discovery_on() else "off")


@config_group.command("profile")
@click.argument("name", required=False)
@click.option("--list", "list_profiles", is_flag=True, help="List all available profiles.")
@click.pass_obj
def config_profile(sdk: GappSDK, name, list_profiles):
    """View, switch, or list configuration profiles."""
    from gapp.admin.sdk.config import load_config
    if list_profiles:
        config = load_config()
        active = config.get("active", "default")
        click.echo("Profiles:")
        for p in sorted(config.get("profiles", {}).keys()):
            prefix = "* " if p == active else "  "
            click.echo(f"{prefix}{p}")
        return
    if name:
        sdk.set_active_profile(name)
        click.echo(f"  Switched to profile: {name}")
    else:
        click.echo(sdk.get_active_profile())


# -- gapp projects --

@main.group("projects")
def projects_group():
    """Manage GCP project env bindings."""
    pass


@projects_group.command("set-env")
@click.argument("project_id")
@click.argument("env")
@click.option("--force", is_flag=True, help="Overwrite an existing gapp-env value.")
@click.pass_obj
def projects_set_env(sdk: GappSDK, project_id, env, force):
    """Bind a GCP project to a named env (writes the gapp-env label)."""
    try:
        res = sdk.set_project_env(project_id, env=env, force=force)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if res["status"] == "exists":
        click.echo(f"  Project {project_id} is already bound to env='{res['env']}'.")
    elif res["status"] == "added":
        click.echo(f"  Project {project_id} bound to env='{res['env']}'.")
    else:
        click.echo(f"  Project {project_id} env changed: '{res.get('previous')}' → '{res['env']}'.")


@projects_group.command("clear-env")
@click.argument("project_id")
@click.pass_obj
def projects_clear_env(sdk: GappSDK, project_id):
    """Remove the gapp-env label from a project (becomes undefined)."""
    try:
        res = sdk.clear_project_env(project_id)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if res["status"] == "absent":
        click.echo(f"  Project {project_id} had no env binding.")
    else:
        click.echo(f"  Project {project_id} env binding removed (was '{res['previous']}').")


@projects_group.command("list")
@click.pass_obj
def projects_list(sdk: GappSDK):
    """List GCP projects with gapp-env bindings.

    The gapp-env label has no owner segment, so listing is owner-agnostic —
    every owner sees the same set. There is no --all flag here.
    """
    res = sdk.list_target_projects()
    owner_str = f"owner: {res['owner']}" if res["owner"] else GLOBAL_OWNER_DISPLAY
    click.echo(f"\nProject Inventory ({owner_str}):")
    if not res["projects"]:
        click.echo("  No projects with env bindings found.")
    else:
        for p in res["projects"]:
            click.echo(f"  {p['id']} (env={p['env']})")
    click.echo()


# -- gapp init/setup/deploy --

@main.command()
@click.pass_obj
def init(sdk: GappSDK):
    """Write gapp.yaml for this repo and register the solution locally.

    Needs no cloud login. Idempotent: re-run after editing gapp.yaml to
    validate it. Next: `gapp setup <project-id>`.
    """
    from gapp.admin.sdk.init import init_solution
    try:
        result = init_solution()
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo()
    click.echo(f"  Initialized gapp app: {result['name']}")
    click.echo(f"    gapp.yaml {result['manifest_status']} ✓")
    click.echo(f"    GitHub topic 'gapp-solution' {result['topic_status']} ✓")
    click.echo()
    click.echo("  No GCP project attached yet.")
    click.echo("  Next: gapp setup --project <project-id>")


@main.command("setup")
@click.argument("project_id", required=False)
@click.option("--solution", default=None, help="Solution name.")
@click.option("--env", default=None, help="Verify project's gapp-env matches.")
@click.option("--project", "project_arg", help="Explicit GCP Project ID.")
@click.option("--force", is_flag=True, help="Override Layer-1 cross-owner check.")
@click.pass_obj
def setup_cmd(sdk: GappSDK, project_id, solution, env, project_arg, force):
    """GCP foundation: enable APIs, create the solution bucket, label the project.

    Once per solution per project, idempotent. Needs a gcloud login with
    project-admin rights (enable services, create buckets, edit IAM). The
    project id is required the first time; later runs discover it from
    the project label. Next: `gapp secrets set` for any declared secrets,
    then `gapp deploy` or `gapp ci setup`.
    """
    pid = project_arg or project_id
    try:
        result = sdk.setup(pid, solution=solution, env=env, force=force)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    env_str = result["env"] or UNDEFINED_ENV_DISPLAY
    click.echo()
    click.echo(f"  {result['name']} (env: {env_str}) → {result['project_id']}")
    click.echo()
    if result["apis"]:
        for api in result["apis"]:
            click.echo(f"    API {api} enabled ✓")
    click.echo(f"    Bucket gs://{result['bucket']} {result['bucket_status']} ✓")
    click.echo(f"    Project label {result['project_id']} {result['label_status']} ✓")
    click.echo()


@main.command()
@click.option("--ref", default=None, help="Git ref (commit/tag/branch) to build from. Defaults to HEAD.")
@click.option("--solution", default=None, help="Solution name.")
@click.option("--env", default=None, help="Verify project's gapp-env matches.")
@click.option("--project", help="Explicit GCP Project ID override.")
@click.option("--dry-run", is_flag=True, help="Preview deployment.")
@click.option("--rebuild", is_flag=True, help="Force docker build even if an image already exists for the resolved SHA.")
@click.pass_obj
def deploy(sdk: GappSDK, ref, solution, env, project, dry_run, rebuild):
    """Build the image with Cloud Build and apply Terraform; prints the service URL.

    Needs a gcloud login, terraform on PATH, and a clean git tree: the
    build ships `git archive HEAD`, so uncommitted changes never deploy.
    An image already built for this commit is reused. To deploy without
    gcloud or terraform on this machine, wire CI once (`gapp ci setup`)
    and use `gapp ci trigger` or GitHub's "Run workflow" button.
    """
    try:
        result = sdk.deploy(ref=ref, solution=solution, env=env, dry_run=dry_run, project_id=project, rebuild=rebuild)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    if dry_run:
        env_str = result["env"] or UNDEFINED_ENV_DISPLAY
        click.echo()
        click.echo("  DRY RUN: Project Deployment Preview")
        click.echo(f"    Solution:    {result['name']}")
        if result["owner"]:
            click.echo(f"    Owner:       {result['owner']}")
        else:
            click.echo(f"    Owner:       {GLOBAL_OWNER_DISPLAY}")
        click.echo(f"    GCP Label:   {result['label']}")
        click.echo(f"    Environment: {env_str}")
        if result["project_id"]:
            click.echo(f"    Project:     {result['project_id']}")
        if result["bucket"]:
            click.echo(f"    GCS Bucket:  gs://{result['bucket']}/")
        click.echo(f"    Status:      {result['status'].upper()}")
        if result.get("services"):
            click.echo("\n  Services to deploy:")
            for svc in result["services"]:
                click.echo(f"    + {svc['name']} (from ./{svc['path']})")
        click.echo()
        return

    click.echo("  Deployed successfully.")


@main.command()
@click.option(
    "--solution",
    "-s",
    default=None,
    help="Solution name. Bypasses local gapp.yaml discovery and validation; "
         "lets status run from any directory against any deployed solution.",
)
@click.option("--env", default=None, help="Verify project's gapp-env matches.")
@click.pass_obj
def status(sdk: GappSDK, solution, env):
    """Infrastructure health check.

    With no --solution flag, status discovers the solution from the current
    repo's gapp.yaml. Schema validation is lenient — a stale or unsupported
    gapp.yaml does not block cloud reads.
    """
    try:
        result = sdk.status(name=solution, env=env)
        click.echo(f"App:      {result.name}")
        if result.deployment.project:
            click.echo(f"Project:  {result.deployment.project}")
        click.echo(f"Status:   {'READY' if not result.deployment.pending else 'PENDING'}")
        if result.deployment.services:
            for s in result.deployment.services:
                click.echo(f"  Service: {s.name}")
                click.echo(f"  URL:     {s.url}")
                click.echo(f"  Healthy: {'✓' if s.healthy else 'X'}")
        if result.next_step:
            click.echo(f"\nNext Step: {result.next_step.action}")
            if result.next_step.hint:
                click.echo(f"  {result.next_step.hint}")
    except Exception as e:
        click.echo(f"  Error: {e}", err=True)


@main.command("list")
@click.option(
    "--all",
    "all_owners",
    is_flag=True,
    help="Include apps from every owner namespace, not just the active owner.",
)
@click.option("--project-limit", default=50, help="Max projects to scan.")
@click.pass_obj
def list_cmd(sdk: GappSDK, all_owners, project_limit):
    """List deployed apps from GCP labels.

    Without --all, listing is scoped to the active owner namespace (or to
    global apps when no owner is configured). --all overrides that scope
    and shows apps across every owner namespace.
    """
    res = sdk.list_apps(all_owners=all_owners, project_limit=project_limit)

    for msg in res["messages"]:
        click.echo(msg)

    if not res["apps"]:
        click.echo("\n  No apps found.")
    else:
        header = f"\n  {'App':<20} {'Project':<20} {'Owner':<15} {'Env':<14} {'Contract':<10}"
        click.echo(header)
        click.echo("  " + "-" * (len(header) - 2))
        for app in res["apps"]:
            contract = "legacy" if app["is_legacy"] else f"v-{app['contract_major']}"
            env_disp = app["env"] or UNDEFINED_ENV_DISPLAY
            owner_disp = app["owner"] if app["owner"] != "global" else GLOBAL_OWNER_DISPLAY
            marker = "  ⚠" if app.get("duplicate") else ""
            click.echo(
                f"  {app['name']:<20} {app['project']:<20} "
                f"{owner_disp:<15} {env_disp:<14} {contract:<10}{marker}"
            )

    click.echo(
        f"\nSummary: {res['metadata']['apps']['count']} apps across "
        f"{res['metadata']['projects']['count']} projects "
        f"(this build: v-{res['metadata']['contract_major']})."
    )
    for warn in res["warnings"]:
        click.echo(click.style(f"WARNING: {warn}", fg="yellow"))


# -- gapp secrets --

# -- gapp ci --

@main.group("ci")
def ci_group():
    """Deploy through GitHub Actions instead of this machine.

    One-time wiring, then deploys from anywhere with GitHub access (the
    GitHub UI, a phone, a CI runner, an agent session):

    \b
      gapp ci init <owner/repo>   once per operator: the private repo for workflows
      gapp ci setup               once per solution: WIF pool, service account, workflow
      gapp ci trigger             any time: dispatch the workflow at a ref
      gapp ci status              where you are in the above

    Scope: the WIF pool, provider, and gapp-deploy service account are per
    project and shared by every solution on it; the workflow file is per
    solution. The runner authenticates with Workload Identity Federation,
    so no key is stored anywhere. `ci init` and `ci setup` need a gcloud
    login with project-admin rights and `gh` write access to the CI repo;
    `ci trigger` needs only `gh`.
    """


@ci_group.command("status")
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
def ci_status_cmd(solution):
    """Show the CI repo and whether this solution has a deploy workflow.

    Both must be present before `gapp ci trigger` works; the output names
    the next step.
    """
    from gapp.admin.sdk.ci import get_ci_status
    try:
        result = get_ci_status(solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"CI repo:   {result['repo'] or '(none — run gapp ci init <repo>)'}")
    click.echo(f"Workflow:  {'present' if result['workflow'] else 'missing'}")
    if not result["repo"]:
        click.echo("\nNext Step: ci init")
    elif not result["workflow"]:
        click.echo("\nNext Step: ci setup")
    else:
        click.echo("\nReady: gapp ci trigger")


@ci_group.command("init")
@click.argument("repo")
@click.option("--local-only", is_flag=True, help="Write local config only; skip the GitHub topic.")
def ci_init_cmd(repo, local_only):
    """Designate the private CI repo that holds deploy workflows (once per operator).

    REPO is owner/name or a bare name under your GitHub account; it is
    created private if missing and tagged so other machines can find it.
    This is not the solution repo.
    """
    from gapp.admin.sdk.ci import init_ci
    try:
        result = init_ci(repo, local_only=local_only)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"\n  CI repo: {result['repo']}")
    click.echo(f"    config {result['config_status']} ✓")
    if result.get("repo_created"):
        click.echo("    repo created (private) ✓")
    click.echo(f"    topic {result['topic_status']} ✓")
    click.echo("\nNext Step: gapp ci setup\n")


@ci_group.command("setup")
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
@click.option(
    "--solution-repo", default=None,
    help="GitHub repo the pipeline deploys (owner/name or URL). Defaults to this checkout's origin.",
)
def ci_setup_cmd(solution, solution_repo):
    """Wire one solution for CI deploys (once per solution, idempotent).

    Creates the project's Workload Identity pool and provider and the
    gapp-deploy service account if missing (shared by every solution on
    the project), binds the pool to the CI repo, and pushes this
    solution's workflow file into the CI repo. Requires `gapp ci init`
    and `gapp setup` first.
    """
    from gapp.admin.sdk.ci import setup_ci
    try:
        result = setup_ci(solution=solution, solution_repo=solution_repo)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo()
    click.echo(f"  {result['solution']} ({result['solution_repo']}) → {result['project_id']} via {result['ci_repo']}")
    click.echo()
    click.echo(f"    WIF pool {result['wif_pool']} ✓")
    click.echo(f"    WIF provider {result['wif_provider']} ✓")
    click.echo(f"    Service account {result['service_account']} ✓")
    click.echo(f"    Binding {result['binding']} ✓")
    click.echo(f"    Workflow {result['workflow']} ✓")
    click.echo("\nNext Step: gapp ci trigger\n")


@ci_group.command("trigger")
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
@click.option("--ref", default="main", show_default=True, help="Git ref to deploy.")
@click.option("--no-watch", is_flag=True, help="Return after dispatch instead of streaming the run.")
def ci_trigger_cmd(solution, ref, no_watch):
    """Dispatch the solution's deploy workflow in the CI repo.

    Equivalent to pressing "Run workflow" on the CI repo's Actions tab.
    Streams the run until it finishes unless --no-watch is given. Needs
    only `gh` logged in; no gcloud or terraform on this machine.
    """
    from gapp.admin.sdk.ci import trigger_ci
    try:
        result = trigger_ci(solution=solution, ref=ref, watch=not no_watch)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"\n  Dispatched {result['workflow']} in {result['ci_repo']} (ref {result['ref']})")
    if result["run_url"]:
        click.echo(f"    Run: {result['run_url']}")
    if result["watched"]:
        click.echo(f"    Conclusion: {result['conclusion'] or 'unknown'}")
    click.echo()


# -- gapp manifest --

@main.group("manifest")
def manifest_group():
    """Inspect the gapp.yaml manifest contract."""


@manifest_group.command("schema")
def manifest_schema_cmd():
    """Print the live JSON Schema for gapp.yaml.

    Generated from the Pydantic model on every call; nothing is read from
    disk, so this is always the schema this build validates against.
    """
    from gapp.admin.sdk.schema import get_schema
    click.echo(json_mod.dumps(get_schema(), indent=2))


@main.group("secrets")
def secrets_group():
    """List, get, and set secret values for the current solution."""


@secrets_group.command("list")
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
def secrets_list_cmd(solution):
    """Show declared secrets, deploy-readiness, and remediation hints."""
    from gapp.admin.sdk.secrets import list_secrets
    try:
        result = list_secrets(solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"App:     {result['solution']}")
    if result.get("project_id"):
        click.echo(f"Project: {result['project_id']}")
    else:
        click.echo("Project: (none — run `gapp setup` first)")

    if result["secrets"]:
        click.echo()
        click.echo(f"  {'Secret':<20} {'Env Var':<25} {'Status':<18} Generate")
        click.echo("  " + "-" * 70)
        for s in result["secrets"]:
            click.echo(
                f"  {s['name']:<20} {s['env_var']:<25} "
                f"{s['status']:<18} {'yes' if s['generate'] else 'no'}"
            )
    else:
        click.echo("\n  No secrets declared in gapp.yaml.")

    if result["orphans"]:
        click.echo("\n  Orphans (labeled in GCP but not declared in gapp.yaml):")
        for orphan_id in result["orphans"]:
            click.echo(f"    - {orphan_id}")

    if result["hints"]:
        click.echo("\n" + "=" * 72)
        click.echo("Resolution options")
        click.echo("=" * 72)
        for i, hint in enumerate(result["hints"], start=1):
            click.echo(f"\n[{i}] {hint['secret_id']} — {hint['issue']}")
            click.echo(f"    {hint['message']}")
            for opt in hint["options"]:
                click.echo(f"\n    Option: {opt['label']}")
                click.echo(f"      $ {opt['command']}")


@secrets_group.command("get")
@click.argument("name")
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
@click.option("--plaintext", is_flag=True, help="Print the actual secret value (default: hash + length only).")
def secrets_get_cmd(name, solution, plaintext):
    """Fetch a secret. Default output is a hash + length; --plaintext prints the value."""
    from gapp.admin.sdk.secrets import get_secret
    try:
        result = get_secret(name, plaintext=plaintext, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    if plaintext:
        click.echo(result["value"])
    else:
        click.echo(f"  Name:      {result['name']}")
        click.echo(f"  Secret ID: {result['secret_id']}")
        click.echo(f"  Hash:      {result['hash']}")
        click.echo(f"  Length:    {result['length']}")


@secrets_group.command("set")
@click.argument("name")
@click.argument("value", required=False)
@click.option("--solution", "-s", default=None, help="Solution name. Defaults to current directory.")
@click.option("--from-stdin", is_flag=True, help="Read value from stdin (use to avoid shell history).")
def secrets_set_cmd(name, value, solution, from_stdin):
    """Store a secret value. Without VALUE, prompts (or pass --from-stdin to pipe)."""
    from gapp.admin.sdk.secrets import set_secret
    if from_stdin:
        value = sys.stdin.read().rstrip("\n")
    elif value is None:
        value = click.prompt("Value", hide_input=True, confirmation_prompt=True)
    try:
        result = set_secret(name, value, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo(f"  {result.get('status', 'set')}: {result.get('secret_id', name)}")


def cli_entry():
    from gapp.admin.sdk.schema import ManifestValidationError
    try:
        main(standalone_mode=True)
    except ManifestValidationError as e:
        click.echo(json_mod.dumps(e.to_dict(), indent=2), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli_entry()
