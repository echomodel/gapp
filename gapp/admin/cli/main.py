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
def setup_cmd(project_id, solution, env):
    """GCP foundation: enable APIs, create solution bucket, label project."""
    from gapp.admin.sdk.setup import setup_solution

    try:
        result = setup_solution(project_id, solution=solution, env=env)
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
@click.option("--dry-run", is_flag=True, help="Preview labels and target project without deploying.")
def deploy(ref, solution, env, dry_run):
    """Build + terraform apply (requires setup + prerequisites)."""
    from gapp.admin.sdk.deploy import deploy_solution

    try:
        result = deploy_solution(auto_approve=True, ref=ref, solution=solution, env=env, dry_run=dry_run)
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
    click.echo(f"  {deploy_data.get('name', 'unknown')} deployed to {deploy_data.get('project_id', 'unknown')}")
    build_msg = "already exists, skipped build" if deploy_data.get("build_status") == "skipped" else "built"
    click.echo(f"    Image: {deploy_data.get('image', 'unknown')} ({build_msg})")
    if deploy_data.get("service_url"):
        click.echo(f"    URL:   {deploy_data['service_url']}")
    if deploy_data.get("custom_domain"):
        click.echo(f"    Domain: {deploy_data['custom_domain']}")
    click.echo()


@main.command("build")
@click.option("--solution", default=None, help="Solution name.")
def build_cmd(solution):
    """Submit async Cloud Build. Prints build_id."""
    from gapp.admin.sdk.deploy import start_build

    try:
        result = start_build(solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    if result["status"] == "skipped":
        click.echo(f"  Image already exists: {result['image']}")
        return

    click.echo(f"  Build submitted: {result['build_id']}")
    click.echo(f"  Image: {result['image']}")
    click.echo(f"  Status: {result['status']}")


@main.command()
def plan():
    """Terraform plan (preview changes)."""
    # ... placeholder for now ...
    click.echo("Plan not implemented yet.")


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


@main.command("list")
@click.option("--available", is_flag=True, help="Include remote solutions from GitHub.")
def list_cmd(available):
    """List registered and discovered solutions."""
    from gapp.admin.sdk.solutions import list_solutions
    solutions = list_solutions(include_remote=available)
    
    if not solutions:
        click.echo("No solutions found.")
        return

    click.echo("Solutions:")
    for sol in solutions:
        click.echo(f"  {sol['name']} ({sol['source']})")
        if sol.get("project_id"):
            click.echo(f"    Project: {sol['project_id']}")


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
