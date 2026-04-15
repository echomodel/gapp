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
    project_display = dep.project or "(no project attached)"

    click.echo()
    click.echo(f"  {result.name} \u2192 {project_display}")
    click.echo()

    if result.next_step:
        click.echo(f"  {result.next_step.hint}")
        return

    for svc in dep.services:
        health = "\u2713 healthy" if svc.healthy else "\u2717 unhealthy"
        click.echo(f"  {svc.name}")
        click.echo(f"    URL:    {svc.url}")
        click.echo(f"    Health: {health}")
        if svc.mcp_path:
            click.echo(f"    MCP:    {svc.mcp_path} (run gapp mcp status for tools)")

    if result.domain:
        d = result.domain
        click.echo(f"  Domain: {d.name}")
        click.echo(f"    Status: {d.status}")
        if d.status != "active":
            click.echo(f"    CNAME:  {d.name} \u2192 {d.cname_target}")
        if d.detail:
            click.echo(f"    Detail: {d.detail}")

    click.echo()


# --- Deployments ---

@main.group()
def deployments():
    """Discover GCP projects with gapp solutions."""


@deployments.command("list")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def deployments_list_cmd(as_json):
    """List GCP projects with deployed gapp solutions."""
    from gapp.admin.sdk.deployments import list_deployments

    result = list_deployments()

    if as_json:
        click.echo(json_mod.dumps(result, indent=2))
        return

    if not result["projects"]:
        click.echo("  No gapp deployments found.")
        return

    click.echo()
    for proj in result["projects"]:
        default_marker = " (default)" if proj["id"] == result["default"] else ""
        click.echo(f"  {proj['id']}{default_marker}")
        for s in proj["solutions"]:
            click.echo(f"    {s['name']:<24} instance={s['instance']}")
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
    """Show secret-backed env vars and their status."""
    from gapp.admin.sdk.secrets import list_secrets

    try:
        result = list_secrets(solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['solution']} secrets")
    click.echo()

    if not result["secrets"]:
        click.echo("  No secrets declared.")
        return

    for s in result["secrets"]:
        marker = "\u2713" if s["status"] == "set" else "\u2717"
        gen = " (auto)" if s.get("generate") else ""
        click.echo(f"    {s['name']:<25} {s['secret_id']:<35} {s['status']:<12} {marker}{gen}")
    click.echo()


@secrets.command("get")
@click.argument("name")
@click.option("--plaintext", is_flag=True, help="Show the actual secret value (default: hash only).")
@click.option("--raw", is_flag=True, help="Output just the value, no formatting (implies --plaintext).")
@click.option("--solution", default=None, help="Solution name (default: current directory).")
def secrets_get_cmd(name, plaintext, raw, solution):
    """Get a secret from Secret Manager by its name.

    NAME is the secret's short name as declared in gapp.yaml
    (e.g. "signing-key"). By default shows a hash and length to
    confirm the secret exists without exposing the value. Use
    --plaintext to see the actual value, or --raw to output just
    the value for piping.
    """
    from gapp.admin.sdk.secrets import get_secret

    show_value = plaintext or raw

    try:
        result = get_secret(name, plaintext=show_value, solution=solution)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    if raw:
        click.echo(result["value"], nl=False)
    elif show_value:
        click.echo(f"  {result['name']} ({result['secret_id']}): {result['value']}")
    else:
        click.echo(f"  {result['name']} ({result['secret_id']})")
        click.echo(f"    length: {result['length']}, sha256: {result['hash']}")


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

    click.echo(f"  Secret {result['secret_id']} {result['secret_status']} \u2713")


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
@click.argument("name", required=False)
def ci_status_cmd(name):
    """Show CI readiness for the current solution."""
    from gapp.admin.sdk.ci import get_ci_status

    result = get_ci_status(solution=name)

    click.echo()
    if not result["repo"]:
        click.echo("  No CI repo configured.")
        click.echo("  Run: gapp ci init <repo-name>")
        click.echo()
        return

    repo_status = "\u2713" if result["repo"] else "\u2717"
    workflow_status = "\u2713" if result["workflow"] else "\u2717"
    click.echo(f"  CI repo:   {result['repo']} {repo_status}")
    click.echo(f"  Workflow:  {'found' if result['workflow'] else 'not found'} {workflow_status}")
    if not result["workflow"]:
        click.echo("  Run: gapp ci setup")
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


@main.group("manifest")
def manifest_group():
    """Inspect and verify gapp.yaml."""


@manifest_group.command("schema")
def manifest_schema_cmd():
    """Print the live gapp.yaml JSON Schema (from the Pydantic model)."""
    from gapp.admin.sdk.schema import get_schema
    click.echo(json_mod.dumps(get_schema(), indent=2))


@manifest_group.command("verify")
@click.option("--json", "as_json", is_flag=True, help="Emit the full schema + result as JSON.")
def manifest_verify_cmd(as_json):
    """Validate the gapp.yaml in the current directory against the schema.

    Does not touch GCP or run terraform. If no gapp.yaml exists, reports
    that and still prints the schema so you know what a valid one looks
    like. Exits 0 on valid, 1 on invalid or missing.
    """
    import sys
    from pathlib import Path
    from gapp.admin.sdk.schema import (
        ManifestValidationError,
        get_schema,
        validate_manifest,
    )
    import yaml

    cwd = Path.cwd()
    path = cwd / "gapp.yaml"

    if not path.exists():
        payload = {
            "status": "missing",
            "path": str(path),
            "message": "No gapp.yaml in current directory.",
            "hint": "Run `gapp init` to scaffold one, then re-run `gapp manifest verify`.",
            "schema": get_schema(),
        }
        if as_json:
            click.echo(json_mod.dumps(payload, indent=2))
        else:
            click.echo(f"  No gapp.yaml at {path}")
            click.echo("  Run `gapp init` to scaffold one, or `gapp manifest schema` to see valid fields.")
        raise SystemExit(1)

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    try:
        validate_manifest(data)
    except ManifestValidationError as e:
        if as_json:
            click.echo(json_mod.dumps({"status": "invalid", "path": str(path), **e.to_dict()}, indent=2))
        else:
            click.echo(f"  {path}: invalid", err=True)
            for issue in e.issues:
                click.echo(f"    {issue['path']}: {issue['message']}", err=True)
            click.echo("", err=True)
            click.echo("  Run `gapp manifest schema` for the full list of valid fields.", err=True)
        raise SystemExit(1)

    if as_json:
        click.echo(json_mod.dumps({"status": "valid", "path": str(path), "schema": get_schema()}, indent=2))
    else:
        click.echo(f"  {path}: valid \u2713")


def cli_entry():
    """Console-script wrapper. Surfaces schema errors as JSON to stderr
    so the CLI and MCP layers deliver identical payloads."""
    import sys
    from gapp.admin.sdk.schema import ManifestValidationError
    try:
        main(standalone_mode=True)
    except ManifestValidationError as e:
        click.echo(json_mod.dumps(e.to_dict(), indent=2), err=True)
        sys.exit(1)
