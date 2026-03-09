"""gapp CLI — GCP App Deployer."""

import click

from gapp import __version__


@click.group()
@click.version_option(version=__version__)
def main():
    """GCP App Deployer — deploy Cloud Run services with Terraform."""


@main.command()
def init():
    """Initialize current repo for gapp (local only)."""
    from gapp.sdk.init import init_solution

    try:
        result = init_solution()
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  Initialized gapp solution: {result['name']}")
    click.echo(f"    deploy/manifest.yaml {result['manifest_status']} \u2713")
    click.echo(f"    GitHub topic 'gapp-solution' {result['topic_status']} \u2713")
    click.echo(f"    Registered in solutions.yaml \u2713")
    click.echo()

    click.echo("  No GCP project attached yet.")
    click.echo("  Next: gapp setup <project-id>")


@main.command("setup")
@click.argument("project_id", required=False)
def setup_cmd(project_id):
    """GCP foundation: enable APIs, create solution bucket, label project."""
    from gapp.sdk.setup import setup_solution

    try:
        result = setup_solution(project_id)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} \u2192 {result['project_id']}")
    click.echo()

    if result["apis"]:
        for api in result["apis"]:
            click.echo(f"    API {api} enabled \u2713")
    else:
        click.echo("    No APIs required")

    click.echo(f"    Bucket gs://{result['bucket']} {result['bucket_status']} \u2713")
    click.echo(f"    Project label gapp-{result['name']} {result['label_status']} \u2713")
    click.echo(f"    Saved to solutions.yaml \u2713")
    click.echo()

    click.echo("  Next: gapp secret list (check prerequisites)")
    click.echo("    or: gapp deploy (if no secrets needed)")


@main.command()
def deploy():
    """Build + terraform apply (requires setup + prerequisites)."""
    click.echo("  deploy is not yet implemented.")
    raise SystemExit(1)


@main.command()
def plan():
    """Terraform plan (preview changes)."""
    click.echo("  plan is not yet implemented.")
    raise SystemExit(1)


@main.command()
@click.argument("name", required=False)
def status(name):
    """Full health check across all phases."""
    from gapp.sdk.context import resolve_solution

    ctx = resolve_solution(name)
    if not ctx:
        click.echo("  Not inside a gapp solution. Specify a name or cd into a repo.")
        click.echo("  Run: gapp solutions list")
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {ctx['name']} \u2192 {ctx['project_id'] or '(no project attached)'}")
    click.echo()

    if not ctx["project_id"]:
        click.echo("  No GCP project attached.")
        click.echo("  Next: gapp setup <project-id>")
        return

    click.echo("  status details are not yet implemented.")


@main.group()
def solutions():
    """Solution listing and discovery."""


@solutions.command("list")
@click.option("--available", is_flag=True, help="Include remote GitHub solutions.")
def solutions_list(available):
    """List local (and optionally GitHub) solutions."""
    from gapp.sdk.solutions import list_solutions

    results = list_solutions(include_remote=available)

    if not results:
        click.echo("  No solutions registered.")
        click.echo("  Run: gapp init (from inside a repo)")
        return

    click.echo()
    click.echo("  SOLUTIONS")
    for s in results:
        project = s.get("project_id") or "\u2014"
        location = s.get("repo_path") or s.get("url", "")
        source_marker = "\u00b7 remote" if s["source"] == "github" else ""
        click.echo(f"    {s['name']:<20} {project:<24} {source_marker:<12} {location}")
    click.echo()


@solutions.command("restore")
@click.argument("name")
def solutions_restore(name):
    """Clone from GitHub + find GCP project."""
    click.echo("  restore is not yet implemented.")
    raise SystemExit(1)


@main.group()
def secret():
    """Secret management for the current solution."""


@secret.command("list")
def secret_list():
    """Show prerequisite secrets and status."""
    click.echo("  secret list is not yet implemented.")
    raise SystemExit(1)


@secret.command("set")
@click.argument("name")
def secret_set(name):
    """Guided secret value entry."""
    click.echo("  secret set is not yet implemented.")
    raise SystemExit(1)
