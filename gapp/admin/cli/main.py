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
    then `gapp deploy`. (CI deploys are the other fleet system: `gapp ci`.)
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
@click.option(
    "--replace-domain", is_flag=True,
    help="Allow this deploy to replace or remove the live custom domain mapping. "
         "Without it, a deploy whose domain differs from the mapped one is refused.",
)
@click.pass_obj
def deploy(sdk: GappSDK, ref, solution, env, project, dry_run, rebuild, replace_domain):
    """Build the image with Cloud Build and apply Terraform; prints the service URL.

    Needs a gcloud login, terraform on PATH, and a clean git tree: the
    build ships `git archive HEAD`, so uncommitted changes never deploy.
    An image already built for this commit is reused. To deploy without
    gcloud or terraform on this machine, use the CI system instead
    (`gapp ci setup` once, then `gapp ci deploy`); a solution is deployed
    by one system or the other.

    A custom domain is guarded: the deploy stops before Terraform when the
    domain is not verified by the deploying identity, or when it would
    replace or remove a live mapping and --replace-domain is not given.
    """
    try:
        result = sdk.deploy(
            ref=ref, solution=solution, env=env, dry_run=dry_run, project_id=project,
            rebuild=rebuild, replace_domain=replace_domain,
        )
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
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if not result.initialized:
        click.echo("App:      (not initialized here)")
    else:
        click.echo(f"App:      {result.name}")
    deployment = result.deployment
    if deployment and deployment.project:
        click.echo(f"Project:  {deployment.project}")
    if result.initialized and deployment:
        click.echo(f"Status:   {'READY' if not deployment.pending else 'PENDING'}")
        for s in deployment.services:
            click.echo(f"  Service: {s.name}")
            click.echo(f"  URL:     {s.url}")
            click.echo(f"  Healthy: {'✓' if s.healthy else 'X'}")
    if result.domain:
        click.echo(f"Domain:   {result.domain.name} ({result.domain.status})")
        if result.domain.status == "pending_dns":
            click.echo(f"  CNAME:   {result.domain.name} -> {result.domain.cname_target}")
        if result.domain.detail:
            click.echo(f"  {result.domain.detail}")
    if result.next_step:
        click.echo(f"\nNext Step: {result.next_step.action}")
        if result.next_step.hint:
            click.echo(f"  {result.next_step.hint}")


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


# -- gapp ci --

# Parity: the ci group mirrors the root group's verbs where both have the
# operation (CONTRIBUTING, "Rules of the CI system"); a test checks the names.
CI_PARITY_VERBS = ("setup", "deploy", "status", "list")


def _state(present):
    return "present" if present else "missing" if present is False else "not checked"


def _ci_finish(result, *ok_lines, verbose=False, details=()):
    """The deployment-first ending of every ci command.

    On success: the lines given (what happened), the fleet detail only
    with --verbose, then "Next: <exact command>" when there is one. On an
    outcome: its plain sentence with the fix, and exit 1.
    """
    if result.get("ok") is False:
        click.echo(result["next_step"]["hint"])
        raise SystemExit(1)
    for line in ok_lines:
        if line:
            click.echo(line)
    if verbose:
        for line in details:
            if line:
                click.echo(f"  {line}")
    if result.get("problem"):
        click.echo(result["problem"])
    if next_step := result.get("next_step"):
        click.echo(f"Next: {next_step['hint']}")


def _fleet_details(result):
    """The fleet detail lines `--verbose` adds."""
    lines = [f"Fleet repo: {result.get('fleet_repo') or '(none)'}"]
    if result.get("id"):
        lines.append(f"Id: {result['id']} (what the GCP resources are keyed by)")
    if files := result.get("files"):
        parts = []
        for path, info in files.items():
            name = path.rsplit("/", 1)[-1]
            if isinstance(info, str):
                parts.append(f"{name} {info}")
            elif info.get("present") is None:
                parts.append(f"{name} not checked")
            elif not info["present"]:
                parts.append(f"{name} missing")
            else:
                parts.append(f"{name} present" + (", not current" if info.get("current") is False else ""))
        lines.append("Files: " + ", ".join(parts))
    if result.get("variables") is not None:
        if result.get("variables_note"):
            lines.append(f"Variables: {result['variables_note']}")
        else:
            lines.append("Variables: " + ", ".join(f"{n} {_state(ok)}" for n, ok in result["variables"].items()))
    if result.get("topic"):
        lines.append(f"Topic gapp-ci: {result['topic']}")
    if result.get("legacy_workflows"):
        lines.append("Per-solution workflows from gapp 3.x (still run from the Actions tab; delete once set up here): "
                     + ", ".join(result["legacy_workflows"]))
    return lines


def _verbose_option(fn):
    return click.option("--verbose", "-v", is_flag=True, help="Also show the fleet repo, its files, variables and ids.")(fn)


@main.group("ci")
def ci_group():
    """Deploy through GitHub Actions: the CI fleet system.

    gapp has two fleet systems that do not talk to each other. The root
    commands (setup, deploy, status, list) deploy the local checkout to
    GCP with gcloud. These `ci` commands manage your fleet through GitHub
    only: they never use gcloud (except `ci project setup`) and never read
    the working directory. The fleet is kept in a private fleet repo:
    gapp/deployments.yaml, one entry per deployment, plus gapp's two
    workflows. A deployment is one repo deployed to one project under a
    name you choose; the same repo deployed twice is two deployments.

    \b
    From nothing to first deploy:
      gapp ci init                              finds your fleet repo, or says how to create one
      gapp ci init <owner>/<repo> --create      creates a private fleet repo and sets it
      gapp ci sources add <owner>               allows the GitHub owner whose repos you deploy
      gapp ci project setup <project>           once per GCP project (gcloud as an Owner)
      gapp ci setup <name> --repo <owner>/<repo> --project <project>
      gapp ci deploy <name>
    Each step's output names the next. `gapp ci list` and `gapp ci status
    <name>` say where things stand; `--verbose` adds the fleet detail.
    """


@ci_group.command("init")
@click.argument("repo", required=False, metavar="[OWNER/REPO]")
@click.option("--owner", default=None, help="Search this GitHub owner's repos for the fleet repo (the gapp-ci topic).")
@click.option("--create", is_flag=True, help="Create OWNER/REPO as a new private fleet repo, with gapp's workflows, and set it.")
@_verbose_option
def ci_init_cmd(repo, owner, create, verbose):
    """Show, find, set or create your fleet repo (the only command that sets it).

    \b
      gapp ci init                     shows the saved fleet repo; if none is saved, finds the one
                                       repo under your account carrying the gapp-ci topic and saves it
      gapp ci init --owner <org>       the same search under that owner
      gapp ci init <owner>/<repo>      sets an existing repo as your fleet repo and installs or
                                       updates gapp's workflows and gapp/deployments.yaml in it
      gapp ci init <owner>/<repo> --create   creates it private, tagged and populated, and sets it

    Needs a `gh` login; installing gapp's files needs one that can write to the repo.
    """
    from gapp.admin.sdk.ci import init_fleet
    result = init_fleet(repo, owner=owner, create=create)
    lines = []
    if result.get("ok") is not False:
        files = result.get("files") or {}
        changed = any(s in ("created", "updated") for s in files.values())
        if result.get("created"):
            lines.append(f"Created your private fleet repo {result['fleet_repo']} with gapp's workflows and an empty gapp/deployments.yaml.")
            lines.append("gapp can now deploy your apps through GitHub Actions.")
        elif repo:
            lines.append("gapp can now deploy your apps through GitHub Actions.")
            if not changed:
                lines.append("Nothing changed.")
            elif files.get("gapp/deployments.yaml") == "created":
                lines.append(f"Installed gapp's workflows and gapp/deployments.yaml in {result['fleet_repo']}.")
            else:
                lines.append(f"Updated gapp's workflows in {result['fleet_repo']} to this release.")
            legacy = result.get("legacy_solutions") or {}
            if legacy and not legacy.get("kept"):
                lines.append("Removed the empty gapp/solutions.yaml left by gapp 3.9.")
        else:
            lines.append(f"Your fleet is kept in {result['fleet_repo']} (set by: {result.get('set_by') or 'unknown'}).")
        if repo or result.get("created"):
            lines.append(f"(Your fleet is kept in {result['fleet_repo']}.)")
    _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))


@ci_group.group("project")
def ci_project_group():
    """Per-project trust between the fleet repo and a GCP project."""


@ci_project_group.command("setup")
@click.argument("project")
@_verbose_option
def ci_project_setup_cmd(project, verbose):
    """Make your fleet trusted to deploy to PROJECT (once per project, idempotent).

    The only `gapp ci` command that touches GCP. Creates or confirms the
    Workload Identity pool and provider and the gapp-deploy service
    account in the project, binds the pool to the fleet repo, and records
    the deploy identity in the fleet repo as the GitHub Environment named
    PROJECT (GAPP_WIF_PROVIDER, GAPP_DEPLOY_SERVICE_ACCOUNT).

    Requires gcloud signed in as an Owner of PROJECT, and `gh` signed in
    as an admin of the fleet repo (its owner, for a personal repo). Every
    failure says which requirement is not met.
    """
    from gapp.admin.sdk.ci import setup_ci_project
    result = setup_ci_project(project)
    if result.get("ok") is not False:
        if result.get("changed"):
            result["next_step"] = {"action": "ci setup", "hint": f"gapp ci setup <name> --repo <owner>/<repo> --project {result['project_id']}"}
            line = f"{result['project_id']} now trusts your fleet to deploy to it."
        else:
            result["next_step"] = {"action": "ci setup", "hint": f"gapp ci setup <name> --repo <owner>/<repo> --project {result['project_id']}"}
            line = f"{result['project_id']} already trusts your fleet to deploy to it. Nothing changed."
    else:
        line = None
    details = _fleet_details(result) + [
        f"{label}: {result[key]}" for key, label in (("wif_pool", "WIF pool"), ("wif_provider", "WIF provider"),
                                                      ("service_account", "Service account"), ("binding", "Binding"),
                                                      ("environment_record", f"Environment {result.get('environment')}"))
        if result.get(key)]
    _ci_finish(result, line, verbose=verbose, details=details)


@ci_group.group("sources")
def ci_sources_group():
    """The GitHub owners whose repos may be deployed from your fleet.

    The list is the fleet repo's repository variable GAPP_ALLOWED_SOURCES;
    the register run refuses a repo whose owner is not on it. Needs `gh`
    with write access to the fleet repo (read for `list`).
    """


@ci_sources_group.command("add")
@click.argument("owner")
@_verbose_option
def ci_sources_add_cmd(owner, verbose):
    """Allow apps owned by OWNER in your fleet."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("add", owner)
    line = None
    if result.get("ok") is not False:
        line = (f"Apps owned by {owner} are now allowed in your fleet." if result.get("changed")
                else f"Apps owned by {owner} are already allowed in your fleet. Nothing changed.")
        result["next_step"] = {"action": "ci project setup", "hint": "gapp ci project setup <project>"}
    _ci_finish(result, line, verbose=verbose, details=_fleet_details(result))


@ci_sources_group.command("remove")
@click.argument("owner")
@_verbose_option
def ci_sources_remove_cmd(owner, verbose):
    """Stop allowing apps owned by OWNER."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("remove", owner)
    line = None
    if result.get("ok") is not False:
        line = (f"Apps owned by {owner} are no longer allowed in your fleet." if result.get("changed")
                else f"Apps owned by {owner} were not allowed in your fleet. Nothing changed.")
    _ci_finish(result, line, verbose=verbose, details=_fleet_details(result))


@ci_sources_group.command("list")
@_verbose_option
def ci_sources_list_cmd(verbose):
    """Show the allowed owners."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("list")
    line = None
    if result.get("ok") is not False:
        owners = result.get("owners") or []
        line = ("Allowed owners: " + ", ".join(owners)) if owners else "No owners are allowed in your fleet yet."
        if not owners:
            result["next_step"] = {"action": "ci sources add", "hint": "gapp ci sources add <owner>"}
    _ci_finish(result, line, verbose=verbose, details=_fleet_details(result))


@ci_group.command("setup")
@click.argument("name", required=False)
@click.option("--repo", default=None, metavar="OWNER/REPO", help="The repo to deploy (never the fleet repo). The name defaults to its repo name.")
@click.option("--project", default=None, metavar="PROJECT", help="GCP project id the deployment deploys to.")
@click.option("--domain", default=None, help="Custom domain the deploy maps (a gapp.yaml key). Omit to keep the entry's; pass \"\" to remove it.")
@click.option("--config", "config_path", default=None, type=click.Path(exists=True, dir_okay=False),
              help="YAML file of gapp.yaml keys to set on the entry (merged over the repo's gapp.yaml at deploy time).")
@click.option("--copy-of", default=None, metavar="EXISTING", help="Start NAME as a copy of an existing deployment (same repo and keys; its own id; no domain).")
@click.option("--no-watch", is_flag=True, help="Return after dispatching the register run instead of waiting for it.")
@_verbose_option
def ci_setup_cmd(name, repo, project, domain, config_path, copy_of, no_watch, verbose):
    """Add or update a deployment: NAME deploys --repo to --project. Mirrors `gapp setup` for the fleet.

    Never uses gcloud and never reads the working directory: the repo is
    named by --repo and the name defaults to the repo's name. Same name
    again updates its gapp.yaml keys (--domain, --config). A repo already
    deployed under another name is refused: update that one, or add
    another deployment of it with --copy-of. Writes go through the fleet
    repo's register run, so this needs only a `gh` that can dispatch
    workflows; a project your fleet is not yet trusted for, or an owner
    not yet allowed, stops with the one-time step.

    \b
      gapp ci setup --repo me/my-app --project my-project
      gapp ci setup my-app-staging --copy-of my-app --project my-staging-project
    """
    from gapp.admin.sdk.ci import setup_deployment
    config = None
    if config_path:
        import yaml as _yaml
        with open(config_path) as f:
            config = _yaml.safe_load(f) or {}
        if not isinstance(config, dict):
            click.echo(f"{config_path} is not a mapping of gapp.yaml keys.")
            raise SystemExit(1)
    command = "gapp " + " ".join(sys.argv[1:]) if sys.argv and sys.argv[0] else None
    result = setup_deployment(name, repo=repo, project=project, domain=domain, config=config,
                              copy_of=copy_of, watch=not no_watch, command=command)
    if result.get("ok") is False:
        action = result["next_step"]["action"]
        repo_shown = result.get("repo") or repo or "the repo"
        if action == "ci sources add":
            click.echo(f"{repo_shown} can't be set up yet: apps owned by {repo_shown.split('/')[0]} aren't allowed in your fleet.")
        elif action == "ci project setup":
            click.echo(f"{repo_shown} can't be set up yet: your fleet isn't trusted to deploy to {result.get('project')}.")
    lines = []
    if result.get("ok") is not False:
        nm, rp, pj = result.get("name"), result.get("repo"), result.get("project")
        if result.get("action") == "unchanged":
            lines.append(f"{nm} is already set up to deploy {rp} to {pj}. Nothing changed.")
        elif result.get("dispatched") and result.get("action") is None:
            lines.append(f"Setting up {nm} ({rp} to {pj}): {result.get('run_url')}")
            result["next_step"] = {"action": "ci status", "hint": f"gapp ci status {nm}"}
        elif copy_of:
            lines.append(f"{nm} is set up as a copy of {copy_of}: {rp} deploys to {pj}"
                         + (" (the same project as the source)." if result.get("project_from_source") else "."))
            if result.get("copied_env"):
                lines.append("Env copied (check them for this environment): " + ", ".join(result["copied_env"]))
            if result.get("secrets"):
                lines.append(f"Secrets {nm} needs set on {pj} before its first deploy: " + ", ".join(result["secrets"]))
            lines.append("The repo's own gapp.yaml secrets, if any, must be set on that project too.")
        else:
            lines.append(f"{rp} is set up to deploy to {pj} as {nm}.")
        if result.get("domain"):
            lines.append(f"Domain: {result['domain']}")
        if result.get("action") in ("added", "updated", "unchanged"):
            result["next_step"] = {"action": "ci deploy", "hint": f"gapp ci deploy {nm}"}
            if result.get("next_step") and result["next_step"]["action"] == "ci deploy":
                lines.append(f"Deploy it: gapp ci deploy {nm}")
                result["next_step"] = None
    _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))


@ci_group.command("rename")
@click.argument("old")
@click.argument("new")
@click.option("--no-watch", is_flag=True, help="Return after dispatching the register run instead of waiting for it.")
@_verbose_option
def ci_rename_cmd(old, new, no_watch, verbose):
    """Rename a deployment. Its id and every GCP resource stay as they are."""
    from gapp.admin.sdk.ci import rename_deployment
    result = rename_deployment(old, new, watch=not no_watch)
    line = None
    if result.get("ok") is not False:
        if result.get("action") == "renamed":
            line = f"{old} is now {new}. Its id and GCP resources are unchanged."
            result["next_step"] = {"action": "ci deploy", "hint": f"gapp ci deploy {new}"}
        elif result.get("dispatched"):
            line = f"Renaming {old} to {new}: {result.get('run_url')}"
            result["next_step"] = {"action": "ci status", "hint": f"gapp ci status {new}"}
    _ci_finish(result, line, verbose=verbose, details=_fleet_details(result))


def _print_ci_deploy(result, verbose):
    lines = []
    if result.get("ok") is not False:
        if result.get("watched"):
            lines.append(f"{result['name']} deployed ({result.get('conclusion')}): {result.get('run_url')}")
            result["next_step"] = {"action": "ci status", "hint": f"gapp ci status {result['name']}"}
        else:
            lines.append(f"{result['name']} deploy started (ref {result.get('ref')}): {result.get('run_url')}")
            result["next_step"] = {"action": "ci status", "hint": f"gapp ci status {result['name']}"}
    _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))


@ci_group.command("deploy")
@click.argument("name")
@click.option("--ref", default="main", show_default=True, help="Git ref of the deployed repo to deploy.")
@click.option("--no-watch", is_flag=True, help="Return after dispatch instead of streaming the run.")
@_verbose_option
def ci_deploy_cmd(name, ref, no_watch, verbose):
    """Deploy NAME through your fleet's deploy workflow. Mirrors `gapp deploy` for the fleet.

    Equivalent to "Run workflow" on the fleet repo's Actions tab with the
    deployment's name. Streams the run until it finishes unless --no-watch
    is given. Needs only a `gh` that can dispatch workflows in the fleet
    repo; no gcloud or terraform on this machine.
    """
    from gapp.admin.sdk.ci import deploy_deployment
    click.echo(f"Deploying {name} (ref {ref}).")
    _print_ci_deploy(deploy_deployment(name, ref=ref, watch=not no_watch), verbose)


@ci_group.command("trigger", hidden=True)
@click.argument("name")
@click.option("--ref", default="main")
@click.option("--no-watch", is_flag=True)
def ci_trigger_cmd(name, ref, no_watch):
    """Former name of `gapp ci deploy`."""
    from gapp.admin.sdk.ci import deploy_deployment
    click.echo("`gapp ci trigger` is now `gapp ci deploy`; running it. This alias goes away in the next release.")
    _print_ci_deploy(deploy_deployment(name, ref=ref, watch=not no_watch), False)


@ci_group.command("status")
@click.argument("name")
@_verbose_option
def ci_status_cmd(name, verbose):
    """One deployment: its repo, project, domain and last deploy. Mirrors `gapp status` for the fleet.

    Never touches GCP. Names the step when a deploy cannot run as things
    stand. `gapp ci list` shows the whole fleet.
    """
    from gapp.admin.sdk.ci import status_deployment
    result = status_deployment(name)
    lines = []
    if result.get("ok") is not False:
        head = f"{name}: {result.get('repo')} deploys to {result.get('project')}"
        if result.get("domain"):
            head += f", domain {result['domain']}"
        lines.append(head + ".")
        last = result.get("last_deploy") or {}
        if last.get("checked") is False:
            lines.append(f"Last deploy: {last.get('note')}")
        elif last.get("run"):
            run = last["run"]
            outcome = run.get("conclusion") or run.get("status") or "unknown"
            lines.append(f"Last deploy: {outcome}, {run.get('created_at')} (ref {run.get('ref')}): {run.get('url')}")
        else:
            lines.append("No deploys yet.")
        record = result.get("environment_record") or {}
        if record.get("exists") is None and record.get("note"):
            lines.append(f"Project trust: {record['note']}")
        if result.get("next_step") is None:
            lines.append(f"Deploy it: gapp ci deploy {name}")
    details = _fleet_details(result)
    if verbose and result.get("overlay"):
        details.append("gapp.yaml keys set by the fleet: " + ", ".join(sorted(result["overlay"])))
    _ci_finish(result, *lines, verbose=verbose, details=details)


@ci_group.command("list")
@click.option("--repo", default=None, metavar="OWNER/REPO", help="Only the deployments of this repo.")
@_verbose_option
def ci_list_cmd(repo, verbose):
    """The fleet's deployments, optionally one repo's. Mirrors `gapp list` for the fleet.

    Never touches GCP. --verbose adds the fleet repo's files and variables
    and notes per-solution workflows left from gapp 3.x.
    """
    from gapp.admin.sdk.ci import list_deployments
    result = list_deployments(repo, verbose=verbose)
    lines = []
    if result.get("ok") is not False and not result.get("deployments"):
        lines.append(f"No deployment of {repo} in {result['fleet_repo']}." if repo
                     else f"No deployments in {result['fleet_repo']} yet.")
    if result.get("ok") is not False and result.get("deployments"):
        n = len(result["deployments"])
        lines.append(f"{n} deployment{'s' if n != 1 else ''} in {result['fleet_repo']}"
                     + (f" of {repo}" if repo else "") + ":")
        width = max(len(d["name"]) for d in result["deployments"])
        for d in result["deployments"]:
            extra = f", domain {d['domain']}" if d.get("domain") else ""
            lines.append(f"  {d['name']:<{width}}  {d['repo']} -> {d['project']}{extra}")
    _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))


@ci_group.group("runner", hidden=True)
def ci_runner_group():
    """Commands the fleet repo's workflows run on the runner (not for people)."""


@ci_runner_group.command("register")
def ci_runner_register_cmd():
    """Validate and write one change to gapp/deployments.yaml; inputs come from the environment."""
    from gapp.admin.sdk.ci import runner_register
    raise SystemExit(runner_register())


@ci_runner_group.command("overlay")
@click.argument("path", default="gapp.yaml")
def ci_runner_overlay_cmd(path):
    """Merge the deployment's gapp.yaml keys (OVERLAY, a JSON object; DOMAIN) over PATH."""
    import os
    from gapp.admin.sdk.ci import runner_overlay
    merged = runner_overlay(path, os.environ.get("OVERLAY"), os.environ.get("DOMAIN"))
    click.echo(f"gapp-deploy: gapp.yaml for '{merged.get('name')}' with the fleet's keys applied.")


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
