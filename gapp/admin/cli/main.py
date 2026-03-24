"""gapp CLI — GCP App Deployer."""

import json as json_mod

import click

from gapp import __version__


@click.group()
@click.version_option(version=__version__)
def main():
    """GCP App Deployer — deploy Cloud Run services with Terraform."""


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
    click.echo(f"    Registered in solutions.yaml \u2713")
    click.echo()

    click.echo("  No GCP project attached yet.")
    click.echo("  Next: gapp setup <project-id>")


@main.command("setup")
@click.argument("project_id", required=False)
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def setup_cmd(project_id, solution):
    """GCP foundation: enable APIs, create solution bucket, label project."""
    from gapp.admin.sdk.setup import setup_solution

    try:
        result = setup_solution(project_id, solution=solution)
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
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def deploy(ref, solution):
    """Build + terraform apply (requires setup + prerequisites)."""
    from gapp.admin.sdk.deploy import deploy_solution

    try:
        result = deploy_solution(auto_approve=True, ref=ref, solution=solution)
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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(name, as_json):
    """Infrastructure health check with guided next steps."""
    from gapp.admin.sdk.status import get_status

    result = get_status(name)

    if as_json:
        click.echo(json_mod.dumps(result.model_dump(), indent=2))
        return

    if not result.initialized:
        click.echo("  Not inside a gapp solution.")
        click.echo("  Run: gapp init")
        raise SystemExit(1)

    dep = result.deployment
    project_display = dep.project.id or "(no project attached)"

    click.echo()
    click.echo(f"  {result.name} \u2192 {project_display}")
    click.echo()

    if result.next_step:
        click.echo(f"  {result.next_step.hint}")
        if dep.project.suggestions:
            sug = dep.project.suggestions
            if sug.default:
                names = ", ".join(sug.default.solutions)
                label = f" (also used by {names})" if names else ""
                click.echo(f"    Suggested: {sug.default.id}{label}")
            for other in sug.others:
                names = ", ".join(other.solutions)
                click.echo(f"    Available: {other.id} (used by {names})")
        return

    for svc in dep.services:
        health = "\u2713 healthy" if svc.healthy else "\u2717 unhealthy"
        click.echo(f"  {svc.name}")
        click.echo(f"    URL:    {svc.url}")
        click.echo(f"    Health: {health}")
        if svc.auth_enabled:
            click.echo(f"    Auth:   enabled")
        if svc.mcp_path:
            click.echo(f"    MCP:    {svc.mcp_path} (run gapp mcp status for tools)")

    click.echo()


# --- Top-level commands (promoted from solutions subgroup) ---

@main.command("list")
@click.option("--available", is_flag=True, help="Include remote GitHub solutions.")
def list_cmd(available):
    """List registered solutions."""
    from gapp.admin.sdk.solutions import list_solutions

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


@main.command()
@click.argument("name")
def restore(name):
    """Clone from GitHub + find GCP project."""
    click.echo("  restore is not yet implemented.")
    raise SystemExit(1)


# --- Secrets ---

@main.group()
def secrets():
    """Secret management for the current solution."""


@secrets.command("list")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def secrets_list_cmd(solution):
    """Show prerequisite secrets and status."""
    from gapp.admin.sdk.secrets import list_secrets

    try:
        result = list_secrets(solution=solution)
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
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def secrets_set_cmd(name, value, solution):
    """Store a secret value in Secret Manager."""
    from gapp.admin.sdk.secrets import set_secret

    if not value:
        value = click.prompt(f"  Enter value for {name}", hide_input=True)

    try:
        result = set_secret(name, value, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} {result['secret_status']} \u2713")


@secrets.command("add")
@click.argument("name")
@click.argument("description")
@click.argument("value", required=False)
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def secrets_add_cmd(name, description, value, solution):
    """Declare a secret in gapp.yaml and optionally set its value."""
    from gapp.admin.sdk.secrets import add_secret

    try:
        result = add_secret(name, description, value, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} {result['manifest_status']} in gapp.yaml \u2713")
    if result["value_status"]:
        click.echo(f"  Value {result['value_status']} \u2713")


@secrets.command("remove")
@click.argument("name")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def secrets_remove_cmd(name, solution):
    """Remove a secret declaration from gapp.yaml."""
    from gapp.admin.sdk.secrets import remove_secret

    try:
        result = remove_secret(name, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Secret {result['name']} removed from gapp.yaml \u2713")


# --- Users ---

@main.group()
def users():
    """Manage upstream credentials — the real API tokens the solution uses."""


@users.command("register")
@click.argument("email")
@click.argument("credential")
@click.option("--strategy", default="bearer", help="Credential strategy (default: bearer).")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def users_register_cmd(email, credential, strategy, solution):
    """Register a user and store their upstream credential (e.g., API token)."""
    from gapp.admin.sdk.users import register_user

    try:
        result = register_user(email, credential, strategy, solution=solution)
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
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def users_list_cmd(limit, start_index, solution):
    """List registered users."""
    from gapp.admin.sdk.users import list_users

    try:
        result = list_users(limit=limit, start_index=start_index, solution=solution)
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
        created = u.get("created", "")[:10]
        updated = u.get("updated", "")[:10]
        click.echo(f"    {u['sub']:<30} {u['strategy']:<10} created {created}  updated {updated}")
    click.echo()

    shown = result["start_index"] + len(result["users"])
    if shown < result["total"]:
        click.echo(f"  Showing {result['start_index'] + 1}-{shown} of {result['total']}.")
        click.echo(f"  Use --start-index={shown} to see more.")
        click.echo()


@users.command("get")
@click.argument("identifier")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def users_get_cmd(identifier, solution):
    """Get full user details by email or hash."""
    from gapp.admin.sdk.users import get_user

    try:
        result = get_user(identifier, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['sub']}")
    click.echo(f"    Hash:           {result['email_hash']}")
    click.echo(f"    Strategy:       {result['strategy']}")
    click.echo(f"    Created:        {result['created']}")
    if result.get("revoke_before"):
        click.echo(f"    Revoke before:  {result['revoke_before']}")
    click.echo()


@users.command("update")
@click.argument("email")
@click.option("--credential", default=None, help="New upstream credential value.")
@click.option("--revoke-before", default=None, help="ISO 8601 timestamp — reject tokens issued before this time.")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def users_update_cmd(email, credential, revoke_before, solution):
    """Update a user's upstream credential or set revoke_before timestamp."""
    from gapp.admin.sdk.users import update_user

    try:
        result = update_user(email, credential=credential, revoke_before=revoke_before, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  Updated {result['email']}: {', '.join(result['changes'])} \u2713")


@users.command("revoke")
@click.argument("email")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def users_revoke_cmd(email, solution):
    """Revoke a user by deleting their credential file."""
    from gapp.admin.sdk.users import revoke_user

    try:
        result = revoke_user(email, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  User {result['email']} revoked \u2713")


# --- Tokens ---

@main.group()
def tokens():
    """Manage PATs (personal access tokens) — what clients send to authenticate."""


@tokens.command("create")
@click.argument("email")
@click.option("--duration", default=3650, type=int, help="Token duration in days (default: 3650 / ~10 years).")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def tokens_create_cmd(email, duration, solution):
    """Create a signed PAT (JWT) that a client uses to call the solution."""
    from gapp.admin.sdk.tokens import create_token

    try:
        result = create_token(email, duration_days=duration, solution=solution)
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
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def tokens_revoke_cmd(email, solution):
    """Invalidate all PATs for a user (sets revoke_before to now)."""
    from gapp.admin.sdk.tokens import revoke_tokens

    try:
        result = revoke_tokens(email, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo(f"  All tokens for {result['email']} revoked \u2713")
    click.echo(f"    revoke_before: {result['revoke_before']}")


# --- Admin (self-management) ---

@main.group()
def admin():
    """Manage gapp itself — install admin MCP server into agent clients."""


@admin.command("install")
@click.argument("client", type=click.Choice(["claude", "gemini"]))
@click.option("--scope", default="user", type=click.Choice(["user", "project"]), help="Registration scope (default: user).")
def admin_install_cmd(client, scope):
    """Register the gapp-admin MCP server with an agent client."""
    from gapp.admin.sdk.self_install import install_admin_mcp

    result = install_admin_mcp(client, scope)

    if result["success"]:
        click.echo(f"  gapp-admin registered with {result['client']} ({result['scope']}) \u2713")
    else:
        click.echo(f"  Failed to register: {result['output']}", err=True)
        raise SystemExit(1)


# --- CI/CD ---

@main.group()
def ci():
    """CI/CD automation — deploy solutions via GitHub Actions."""


@ci.command("init")
@click.argument("repo")
@click.option("--local-only", is_flag=True, help="Only write to local config, skip GitHub topic.")
def ci_init_cmd(repo, local_only):
    """Designate the operator's CI repo (repo name or owner/name)."""
    from gapp.admin.sdk.ci import init_ci

    try:
        result = init_ci(repo, local_only=local_only)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  CI repo: {result['repo']}")
    click.echo(f"    Config: {result['config_status']} \u2713")
    click.echo(f"    Topic:  {result['topic_status']} \u2713")
    if result.get("repo_created"):
        click.echo(f"    Repo created (private) \u2713")
    click.echo()
    click.echo("  Next: gapp ci setup <solution-repo>")


@ci.command("setup")
@click.argument("name", required=False)
def ci_setup_cmd(name):
    """Wire a solution for CI/CD deployment."""
    from gapp.admin.sdk.ci import setup_ci

    try:
        result = setup_ci(solution=name)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['solution']} \u2192 CI/CD")
    click.echo(f"    Solution repo:     {result['solution_repo']}")
    click.echo(f"    GCP project:       {result['project_id']}")
    click.echo(f"    CI repo:           {result['ci_repo']}")
    click.echo()
    click.echo(f"    WIF pool:          {result['wif_pool']} \u2713")
    click.echo(f"    WIF provider:      {result['wif_provider']} \u2713")
    click.echo(f"    Service account:   {result['service_account']} \u2713")
    click.echo(f"    IAM binding:       {result['binding']} \u2713")
    click.echo(f"    Workflow:          {result['workflow']} \u2713")
    click.echo()
    click.echo("  Next:")
    click.echo("    \u2022 gapp ci trigger <solution-name>")
    click.echo("    \u2022 GitHub UI: Actions \u2192 Deploy \u2192 Run workflow")
    click.echo("    \u2022 gh workflow run <solution>.yml --repo <ci-repo>")


@ci.command("status")
def ci_status_cmd():
    """Show CI configuration state."""
    from gapp.admin.sdk.ci import get_ci_status

    result = get_ci_status()

    click.echo()
    if not result["repo"]:
        click.echo("  No CI repo configured.")
        click.echo("  Run: gapp ci init <repo-name>")
        click.echo()
        return

    click.echo(f"  CI repo: {result['repo']} (source: {result['source']})")
    click.echo(f"    Local config:  {'set' if result['local_config'] else 'not set'}")
    click.echo(f"    Remote topic:  {result['remote_config'] or 'not set'}")
    click.echo()


@ci.command("trigger")
@click.argument("name", required=False)
@click.option("--ref", default="main", help="Git ref to deploy (default: main).")
@click.option("--no-wait", is_flag=True, help="Return immediately without watching.")
def ci_trigger_cmd(name, ref, no_wait):
    """Trigger a CI deployment for a solution."""
    from gapp.admin.sdk.ci import trigger_ci

    try:
        watch = not no_wait
        result = trigger_ci(solution=name, ref=ref, watch=watch)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  Triggered {result['solution']} deploy")
    click.echo(f"    CI repo:   {result['ci_repo']}")
    click.echo(f"    Workflow:  {result['workflow']}")
    click.echo(f"    Ref:       {result['ref']}")
    if result.get("run_url"):
        click.echo(f"    Run:       {result['run_url']}")

    if result.get("watched"):
        conclusion = result.get("conclusion", "unknown")
        if conclusion == "success":
            click.echo(f"\n  \u2713 Deploy succeeded")
        else:
            click.echo(f"\n  \u2717 Deploy {conclusion}")
            raise SystemExit(1)
    elif result.get("run_id"):
        click.echo()
        click.echo(f"  Watch: gapp ci watch {result['run_id']}")

    click.echo()


@ci.command("watch")
@click.argument("run_id")
def ci_watch_cmd(run_id):
    """Watch a CI run to completion."""
    from gapp.admin.sdk.ci import watch_ci

    result = watch_ci(run_id)

    click.echo()
    conclusion = result.get("conclusion", "unknown")
    if conclusion == "success":
        click.echo(f"  \u2713 Run {result['run_id']} succeeded")
    else:
        click.echo(f"  \u2717 Run {result['run_id']} {conclusion}")
        raise SystemExit(1)
    click.echo()


# --- MCP (deployed solutions) ---

@main.group()
def mcp():
    """MCP service management — status, tools, and client configuration."""


@mcp.command("status")
@click.argument("name", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def mcp_status_cmd(name, as_json):
    """MCP health check with tool enumeration."""
    from gapp.admin.sdk.mcp_status import mcp_status

    result = mcp_status(name)

    if as_json:
        click.echo(json_mod.dumps(result.model_dump(), indent=2))
        return

    if result.error:
        click.echo(f"  {result.next_step.hint}")
        raise SystemExit(1)

    if result.next_step:
        click.echo()
        click.echo(f"  {result.name} \u2192 {result.project_id or '(no project attached)'}")
        click.echo(f"  {result.next_step.hint}")
        click.echo()
        return

    click.echo()
    click.echo(f"  {result.name} \u2192 {result.project_id}")
    click.echo()
    click.echo(f"  URL:    {result.mcp_url}")

    health = "\u2713 healthy" if result.healthy else "\u2717 unhealthy"
    click.echo(f"  Health: {health}")
    if result.auth_enabled:
        click.echo(f"  Auth:   enabled")

    if result.tools is not None:
        click.echo(f"  Tools:  {len(result.tools)}")
        for tool_name in sorted(result.tools):
            click.echo(f"    \u2022 {tool_name}")
    else:
        click.echo(f"  Tools:  could not enumerate")

    click.echo()


@mcp.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def mcp_list_cmd(as_json):
    """List solutions with MCP endpoints configured."""
    from gapp.admin.sdk.mcp_status import mcp_list

    results = mcp_list()

    if as_json:
        click.echo(json_mod.dumps([r.model_dump() for r in results], indent=2))
        return

    if not results:
        click.echo("  No MCP-enabled solutions found.")
        return

    click.echo()
    click.echo("  MCP SOLUTIONS")
    for s in results:
        project = s.project_id or "\u2014"
        click.echo(f"    {s.name:<20} {project:<24} {s.mcp_path}")
    click.echo()


@mcp.command("connect")
@click.argument("name", required=False)
@click.option("--user", default=None, help="Email of registered user — mints a real PAT.")
@click.option("--claude", default=None, metavar="SCOPE", help="Show Claude Code config for scope (user/project).")
@click.option("--gemini", default=None, metavar="SCOPE", help="Show Gemini CLI config for scope (user/project).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def mcp_connect_cmd(name, user, claude, gemini, as_json):
    """Show MCP client connection info and registration status."""
    from gapp.admin.sdk.mcp_status import mcp_connect

    result = mcp_connect(name, user=user)

    if as_json:
        click.echo(json_mod.dumps(result.model_dump(), indent=2))
        return

    if result.error:
        click.echo(f"  {result.next_step.hint}")
        raise SystemExit(1)

    if result.next_step:
        click.echo(f"  {result.next_step.hint}")
        return

    health = "\u2713 healthy" if result.healthy else "\u2717 unhealthy"
    click.echo()
    click.echo(f"  {result.name} ({health})")
    click.echo(f"  MCP URL: {result.mcp_url}")

    if result.tools is not None:
        click.echo(f"  Tools:   {len(result.tools)}")
    click.echo()

    clients = result.clients
    show_claude = claude is not None or (claude is None and gemini is None)
    show_gemini = gemini is not None or (claude is None and gemini is None)

    if show_claude and clients.claude_code:
        click.echo("  Claude Code")
        cc = clients.claude_code
        scopes = [claude] if claude else ["user", "project"]
        for scope in scopes:
            entry = getattr(cc, scope, None)
            if entry:
                reg = "\u2713 registered" if entry.registered else "\u2717 not registered"
                click.echo(f"    {scope}: {reg}")
                click.echo(f"      {entry.command}")
        click.echo()

    if show_gemini and clients.gemini_cli:
        click.echo("  Gemini CLI")
        gc = clients.gemini_cli
        scopes = [gemini] if gemini else ["user", "project"]
        for scope in scopes:
            entry = getattr(gc, scope, None)
            if entry:
                reg = "\u2713 registered" if entry.registered else "\u2717 not registered"
                click.echo(f"    {scope}: {reg}")
                click.echo(f"      {entry.command}")
        click.echo()

    if clients.claude_ai and claude is None and gemini is None:
        click.echo("  Claude.ai (manual)")
        click.echo(f"    URL: {clients.claude_ai.url}")
        click.echo()
