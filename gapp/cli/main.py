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
    click.echo(f"    gapp.yaml {result['manifest_status']} \u2713")
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
@click.option("--ref", default=None, help="Git ref (commit, tag, branch) to deploy. Skips dirty tree check.")
def deploy(ref):
    """Build + terraform apply (requires setup + prerequisites)."""
    from gapp.sdk.deploy import deploy_solution

    try:
        result = deploy_solution(auto_approve=True, ref=ref)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} deployed to {result['project_id']}")
    build_msg = "already exists, skipped build" if result.get("build_status") == "skipped" else "built"
    click.echo(f"    Image: {result['image']} ({build_msg})")
    if result.get("service_url"):
        click.echo(f"    URL:   {result['service_url']}")
    click.echo()


@main.command()
def plan():
    """Terraform plan (preview changes)."""
    click.echo("  plan is not yet implemented.")
    raise SystemExit(1)


@main.command()
@click.argument("name", required=False)
def status(name):
    """Full health check across all phases."""
    from gapp.sdk.status import get_status

    result = get_status(name)

    if result.get("error") == "not_found":
        click.echo("  Not inside a gapp solution. Specify a name or cd into a repo.")
        click.echo("  Run: gapp solutions list")
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} \u2192 {result['project_id'] or '(no project attached)'}")
    click.echo()

    if not result["project_id"]:
        click.echo("  No GCP project attached.")
        click.echo("  Next: gapp setup <project-id>")
        return

    if not result["deployed"]:
        click.echo("  Not deployed (no Terraform state found).")
        click.echo("  Next: gapp deploy")
        return

    for svc in result["services"]:
        health = "\u2713 healthy" if svc["healthy"] else "\u2717 unhealthy"
        click.echo(f"  {svc['name']}")
        click.echo(f"    URL:    {svc['url']}")
        click.echo(f"    Health: {health}")
        if svc.get("auth_enabled"):
            click.echo(f"    Auth:   enabled")

        tools = svc.get("tools")
        if tools is not None:
            click.echo(f"    MCP:    {svc.get('mcp_path', '/mcp')} ({len(tools)} tools)")
            for tool_name in sorted(tools):
                click.echo(f"      \u2022 {tool_name}")
        elif svc.get("mcp_path"):
            click.echo(f"    MCP:    {svc['mcp_path']} (could not enumerate tools)")

    click.echo()


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
def secrets():
    """Secret management for the current solution."""


@secrets.command("list")
def secrets_list_cmd():
    """Show prerequisite secrets and status."""
    from gapp.sdk.secrets import list_secrets

    try:
        result = list_secrets()
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} secrets")
    click.echo()

    if not result["secrets"]:
        click.echo("  No secrets required.")
        return

    for s in result["secrets"]:
        marker = "\u2713" if s["status"] == "set" else "\u2717"
        click.echo(f"    {s['name']:<30} {s['status']:<12} {marker}  {s['description']}")
    click.echo()


@secrets.command("set")
@click.argument("name")
@click.argument("value", required=False)
def secrets_set_cmd(name, value):
    """Store a secret value in Secret Manager."""
    from gapp.sdk.secrets import set_secret

    if not value:
        value = click.prompt(f"  Enter value for {name}", hide_input=True)

    try:
        result = set_secret(name, value)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} {result['secret_status']} \u2713")


@secrets.command("add")
@click.argument("name")
@click.argument("description")
@click.argument("value", required=False)
def secrets_add_cmd(name, description, value):
    """Declare a secret in gapp.yaml and optionally set its value."""
    from gapp.sdk.secrets import add_secret

    try:
        result = add_secret(name, description, value)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} {result['manifest_status']} in gapp.yaml \u2713")
    if result["value_status"]:
        click.echo(f"  Value {result['value_status']} \u2713")


@secrets.command("remove")
@click.argument("name")
def secrets_remove_cmd(name):
    """Remove a secret declaration from gapp.yaml."""
    from gapp.sdk.secrets import remove_secret

    try:
        result = remove_secret(name)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} removed from gapp.yaml \u2713")


@main.group()
def users():
    """Manage upstream credentials — the real API tokens the solution uses."""


@users.command("register")
@click.argument("email")
@click.argument("credential")
@click.option("--strategy", default="bearer", help="Credential strategy (default: bearer).")
def users_register_cmd(email, credential, strategy):
    """Register a user and store their upstream credential (e.g., API token)."""
    from gapp.sdk.users import register_user

    try:
        result = register_user(email, credential, strategy)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  Registered {result['email']}")
    click.echo(f"    Strategy: {result['strategy']}")
    click.echo(f"    Hash:     {result['email_hash'][:12]}...")
    click.echo()


@users.command("list")
@click.option("--limit", default=10, help="Maximum number of users to show.")
@click.option("--start-index", default=0, help="Offset into the user list.")
def users_list_cmd(limit, start_index):
    """List registered users."""
    from gapp.sdk.users import list_users

    try:
        result = list_users(limit=limit, start_index=start_index)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} users ({result['total']} total)")
    click.echo()

    if not result["users"]:
        click.echo("  No users registered.")
        click.echo()
        return

    for u in result["users"]:
        click.echo(f"    {u['sub']:<30} {u['strategy']:<16} {u['created']}")
    click.echo()

    shown = result["start_index"] + len(result["users"])
    if shown < result["total"]:
        click.echo(f"  Showing {result['start_index'] + 1}-{shown} of {result['total']}.")
        click.echo(f"  Use --start-index={shown} to see more.")
        click.echo()


@users.command("update")
@click.argument("email")
@click.option("--credential", default=None, help="New upstream credential value.")
@click.option("--revoke-before", default=None, help="ISO 8601 timestamp — reject tokens issued before this time.")
def users_update_cmd(email, credential, revoke_before):
    """Update a user's upstream credential or set revoke_before timestamp."""
    from gapp.sdk.users import update_user

    try:
        result = update_user(email, credential=credential, revoke_before=revoke_before)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Updated {result['email']}: {', '.join(result['changes'])} \u2713")


@users.command("revoke")
@click.argument("email")
def users_revoke_cmd(email):
    """Revoke a user by deleting their credential file."""
    from gapp.sdk.users import revoke_user

    try:
        result = revoke_user(email)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  User {result['email']} revoked \u2713")


@main.group()
def tokens():
    """Manage PATs (personal access tokens) — what clients send to authenticate."""


@tokens.command("create")
@click.argument("email")
@click.option("--duration", default=3650, type=int, help="Token duration in days (default: 3650 / ~10 years).")
def tokens_create_cmd(email, duration):
    """Create a signed PAT (JWT) that a client uses to call the solution."""
    from gapp.sdk.tokens import create_token

    try:
        result = create_token(email, duration_days=duration)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  Token created for {result['email']}")
    click.echo(f"    Solution: {result['solution']}")
    click.echo(f"    Expires:  {result['expires_at']}")
    click.echo()
    click.echo(f"  {result['token']}")
    click.echo()


@tokens.command("revoke")
@click.argument("email")
def tokens_revoke_cmd(email):
    """Invalidate all PATs for a user (sets revoke_before to now)."""
    from gapp.sdk.tokens import revoke_tokens

    try:
        result = revoke_tokens(email)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  All tokens for {result['email']} revoked \u2713")
    click.echo(f"    revoke_before: {result['revoke_before']}")
