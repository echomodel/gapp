"""gapp CLI — GCP App Deployer."""

import json as json_mod
import sys
import click

from gapp import __version__


@click.group()
@click.version_option(version=__version__)
def main():
    """GCP App Deployer — deploy Cloud Run services with Terraform."""


# -- gapp config --

@main.group("config", invoke_without_command=True)
def config_group():
    """View or set workstation configuration."""
    if click.get_current_context().invoked_subcommand is None:
        from gapp.admin.sdk.context import get_active_profile, get_active_config
        profile = get_active_profile()
        cfg = get_active_config()
        
        click.echo(f"Active Profile: {profile}")
        click.echo("Settings:")
        click.echo(f"  gcloud account: {cfg.get('account', '(none)')}")
        click.echo(f"  app owner:      {cfg.get('owner', '(none)')}")
        click.echo(f"  discovery:      {cfg.get('discovery', 'on').upper()}")


@config_group.command("account")
@click.argument("email", required=False)
def config_account(email):
    """View or set the global gcloud account email."""
    from gapp.admin.sdk.context import get_account, set_account
    if email:
        try:
            set_account(email)
            click.echo(f"  gcloud account set to: {email}")
        except RuntimeError as e:
            click.echo(f"  Error: {e}", err=True)
            raise SystemExit(1)
    else:
        current = get_account()
        if current:
            click.echo(current)
        else:
            click.echo("No gcloud account configured.")


@config_group.command("owner")
@click.argument("name", required=False)
def config_owner(name):
    """View or set the global app owner for project labels."""
    from gapp.admin.sdk.context import get_owner, set_owner
    if name is not None:
        set_owner(name)
        click.echo(f"  App owner set to: {name or '(none)'}")
    else:
        current = get_owner()
        if current:
            click.echo(current)
        else:
            click.echo("No app owner configured.")


@config_group.command("discovery")
@click.argument("state", type=click.Choice(["on", "off"]), required=False)
def config_discovery(state):
    """Enable (on) or disable (off) GCP label discovery."""
    from gapp.admin.sdk.context import is_discovery_on, set_discovery
    if state:
        set_discovery(state)
        click.echo(f"  Discovery turned {state.upper()}")
    else:
        current = "on" if is_discovery_on() else "off"
        click.echo(current)


@config_group.command("profile")
@click.argument("name", required=False)
@click.option("--list", "list_profiles", is_flag=True, help="List all available profiles.")
def config_profile(name, list_profiles):
    """View, switch, or list configuration profiles."""
    from gapp.admin.sdk.config import load_config
    from gapp.admin.sdk.context import get_active_profile, set_active_profile

    if list_profiles:
        config = load_config()
        active = config.get("active", "default")
        click.echo("Profiles:")
        for p in sorted(config.get("profiles", {}).keys()):
            prefix = "* " if p == active else "  "
            click.echo(f"{prefix}{p}")
        return

    if name:
        set_active_profile(name)
        click.echo(f"  Switched to profile: {name}")
    else:
        click.echo(get_active_profile())


# -- gapp init/setup/deploy --

@main.command()
def init():
    """Initialize current repo for gapp (local only)."""
    from gapp.admin.sdk.init import init_solution

    try:
        result = init_solution()
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  Initialized gapp solution: {result['name']}")
    click.echo(f"    gapp.yaml {result['manifest_status']} \u2713")
    click.echo(f"    GitHub topic 'gapp-solution' {result['topic_status']} \u2713")
    click.echo()

    click.echo("  No GCP project attached yet.")
    click.echo("  Next: gapp setup <project-id>")


@main.command("setup")
@click.argument("project_id", required=False)
@click.option("--solution", default=None, help="Solution name (default: current directory).")
@click.option("--env", default="default", help="Environment name for project labels.")
@click.option("--project", "project_arg", help="Explicit GCP Project ID.")
def setup_cmd(project_id, solution, env, project_arg):
    """GCP foundation: enable APIs, create solution bucket, label project."""
    from gapp.admin.sdk.setup import setup_solution

    pid = project_arg or project_id
    try:
        result = setup_solution(pid, solution=solution, env=env)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} (env: {result['env']}) → {result['project_id']}")
    click.echo()

    if result["apis"]:
        for api in result["apis"]:
            click.echo(f"    API {api} enabled \u2713")
    else:
        click.echo("    No APIs required")

    click.echo(f"    Bucket gs://{result['bucket']} {result['bucket_status']} \u2713")
    click.echo(f"    Project label {result['project_id']} {result['label_status']} \u2713")
    click.echo()


@main.command()
@click.option("--ref", default=None, help="Git ref (commit, tag, branch) to deploy. Skips dirty tree check.")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
@click.option("--env", default="default", help="Target environment name.")
@click.option("--project", help="Explicit GCP Project ID override.")
@click.option("--dry-run", is_flag=True, help="Preview labels and target project without deploying.")
def deploy(ref, solution, env, project, dry_run):
    """Build + terraform apply (requires setup + prerequisites)."""
    from gapp.admin.sdk.deploy import deploy_solution

    try:
        result = deploy_solution(
            auto_approve=True, 
            ref=ref, 
            solution=solution, 
            env=env, 
            dry_run=dry_run,
            project_id=project
        )
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    if dry_run:
        click.echo()
        click.echo("  DRY RUN: Project Deployment Preview")
        click.echo(f"    Solution:    {result['name']}")
        if result["owner"]:
            click.echo(f"    Owner:       {result['owner']} (as solution namespace)")
        else:
            click.echo("    Owner:       <none> (global solution namespace)")
        click.echo(f"    GCP Label:   {result['label']}")
        
        env_label = f"{result['env']} (singular target)" if result['env'] == "default" else result['env']
        click.echo(f"    Environment: {env_label}")
        
        if result["project_id"]:
            click.echo(f"    Project:     {result['project_id']}")
        else:
            click.echo("    Project:     (none resolved)")
        
        if result["bucket"]:
            click.echo(f"    GCS Bucket:  gs://{result['bucket']}/")
        
        if result["repo_path"]:
            from gapp.admin.sdk.solutions import _display_path
            click.echo(f"    Source:      {_display_path(result['repo_path'])}")

        click.echo(f"    Status:      {result['status'].upper()}")

        if result.get("services"):
            click.echo()
            click.echo("  Services to deploy:")
            for svc in result["services"]:
                click.echo(f"    + {svc['name']} (from ./{svc['path']})")
        
        click.echo()
        return

    deploy_data = result.get("deploy", result)
    click.echo()
    if isinstance(result.get("services"), list):
        click.echo(f"  Workspace deployed to {project or 'unknown'}")
        for svc in result["services"]:
            click.echo(f"    + {svc['name']} ({svc['terraform_status']})")
            if svc.get("service_url"):
                click.echo(f"      URL: {svc['service_url']}")
    else:
        click.echo(f"  {deploy_data.get('name', 'unknown')} deployed to {deploy_data.get('project_id', 'unknown')}")
        build_msg = "already exists, skipped build" if deploy_data.get("build_status") == "skipped" else "built"
        click.echo(f"    Image: {deploy_data.get('image', 'unknown')} ({build_msg})")
        if deploy_data.get("service_url"):
            click.echo(f"    URL:   {deploy_data['service_url']}")
    click.echo()


@main.command("list")
@click.option("--available", is_flag=True, help="Include remote solutions from GitHub.")
@click.option("--all", "wide", is_flag=True, help="Show all solutions across all owner namespaces.")
@click.option("--project-limit", default=50, help="Max number of GCP projects to scan (default: 50).")
def list_cmd(available, wide, project_limit):
    """List registered and discovered solutions from GCP labels."""
    from gapp.admin.sdk.solutions import list_solutions
    
    # Pass wide and project_limit to SDK
    results = list_solutions(include_remote=available, wide=wide, project_limit=project_limit)
    
    solutions = results["solutions"]
    filter_mode = results["filter_mode"].upper()
    
    click.echo()
    click.echo(f"Solutions (Filter: {filter_mode}, Limit: {project_limit}):")
    
    if not solutions:
        click.echo("  No solutions found matching criteria.")
    else:
        for sol in solutions:
            click.echo(f"  {sol['name']} (gcp)")
            click.echo(f"    Project: {sol['project_id']}")
            if wide:
                click.echo(f"    Label:   {sol['label']}")

    click.echo()
    click.echo(f"Total: {results['total_solutions']} solutions across {results['total_projects']} projects.")
    
    if results["limit_reached"]:
        click.echo(f"WARNING: Project limit ({project_limit}) reached. Some projects may have been skipped.")
        click.echo("Use --project-limit to increase the scan range.")
    click.echo()


@main.command()
def status():
    """Infrastructure health check."""
    from gapp.admin.sdk.status import get_status
    try:
        result = get_status()
        click.echo(f"Solution: {result.name}")
        click.echo(f"Project:  {result.deployment.project}")
        click.echo(f"Status:   {'READY' if not result.deployment.pending else 'PENDING'}")
        if result.deployment.services:
            for s in result.deployment.services:
                click.echo(f"  Service: {s.name}")
                click.echo(f"  URL:     {s.url}")
                click.echo(f"  Healthy: {'\u2713' if s.healthy else 'X'}")
    except Exception as e:
        click.echo(f"  Error: {e}", err=True)


def cli_entry():
    """Entry point for the console script."""
    from gapp.admin.sdk.schema import ManifestValidationError
    try:
        main(standalone_mode=True)
    except ManifestValidationError as e:
        click.echo(json_mod.dumps(e.to_dict(), indent=2), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli_entry()
