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
@click.option("--all", "wide", is_flag=True, help="Show all projects with env bindings.")
@click.pass_obj
def projects_list(sdk: GappSDK, wide):
    """List GCP projects with gapp-env bindings."""
    res = sdk.list_target_projects(wide=wide)
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
    """Initialize current repo for gapp (local only)."""
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
    """GCP foundation: enable APIs, create solution bucket, label project."""
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
@click.option("--ref", default=None, help="Git ref to deploy.")
@click.option("--solution", default=None, help="Solution name.")
@click.option("--env", default=None, help="Verify project's gapp-env matches.")
@click.option("--project", help="Explicit GCP Project ID override.")
@click.option("--dry-run", is_flag=True, help="Preview deployment.")
@click.pass_obj
def deploy(sdk: GappSDK, ref, solution, env, project, dry_run):
    """Build + terraform apply."""
    try:
        result = sdk.deploy(ref=ref, solution=solution, env=env, dry_run=dry_run, project_id=project)
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
@click.option("--env", default=None, help="Verify project's gapp-env matches.")
@click.pass_obj
def status(sdk: GappSDK, env):
    """Infrastructure health check."""
    try:
        result = sdk.status(env=env)
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
@click.option("--all", "wide", is_flag=True, help="Show all apps across all namespaces.")
@click.option("--project-limit", default=50, help="Max projects to scan.")
@click.pass_obj
def list_cmd(sdk: GappSDK, wide, project_limit):
    """List deployed apps from GCP labels."""
    res = sdk.list_apps(wide=wide, project_limit=project_limit)

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


def cli_entry():
    from gapp.admin.sdk.schema import ManifestValidationError
    try:
        main(standalone_mode=True)
    except ManifestValidationError as e:
        click.echo(json_mod.dumps(e.to_dict(), indent=2), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli_entry()
