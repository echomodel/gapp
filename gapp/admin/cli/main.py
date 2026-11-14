"""gapp CLI — GCP App Deployer."""

import json as json_mod
from pathlib import Path
import sys
import click

from gapp import __version__
from gapp.admin.sdk.core import (
    GappSDK,
)


@click.group()
@click.version_option(version=__version__)
@click.pass_context
def main(ctx):
    """GCP App Deployer — deploy Cloud Run services with Terraform.

    \b
    Deploy from this machine, in the app's repo:
      gapp init                          write gapp.yaml; reports how the app starts
      gapp manifest show                 its settings; gapp manifest set/env set to change them
      gapp setup --project <project-id>  once per project: APIs, data bucket
      gapp secrets set <secret>          each secret the app declares without --generate
      gapp deploy                        build and deploy; prints the URL
      gapp status                        is it up, its URL, the domain's state, the next step

    Or deploy through GitHub Actions, with no gcloud or terraform here:
    `gapp ci --help`. Each command's output names the next step.
    """
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


@config_group.command("discovery")
@click.argument("state", type=click.Choice(["on", "off"]), required=False)
@click.pass_obj
def config_discovery(sdk: GappSDK, state):
    """Enable (on) or disable (off) discovery of hosted projects (gapp=hosted)."""
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


# -- gapp init/setup/deploy --

@main.command()
@click.option("--fresh", is_flag=True,
              help="Start over: keep the current gapp.yaml as gapp.yaml.bak and write a new one with only the name. "
                   "Works on a file gapp can't read or that fails the schema.")
@click.pass_obj
def init(sdk: GappSDK, fresh):
    """Write gapp.yaml for this repo and register the solution locally.

    Needs no cloud login. Idempotent: re-run it to check gapp.yaml and the
    app's start method (a Procfile with a web: line, or a Dockerfile).

    The deployment's name is the name in gapp.yaml if it has one, else the
    origin repo's name, else the folder's; init writes it to gapp.yaml so it
    stays the same wherever the repo is cloned. The name keys the service,
    data bucket and secrets, so a different name is a different deployment.
    Everything else (public access, domain, environment variables, secrets,
    sizing) is set with `gapp manifest set` and `gapp manifest env set`; `gapp manifest show` shows what is
    set now.

    A gapp.yaml gapp can't read, or one with settings it doesn't accept, is
    never changed: init says what's wrong. To start over instead of fixing
    it, `gapp init --fresh` moves it to gapp.yaml.bak and writes a new one
    with the deployment's name (the old file's if it can be read). Nothing
    is deleted. Next: gapp setup --project <project-id>.
    """
    from gapp.admin.sdk.init import init_solution
    try:
        result = init_solution(fresh=fresh)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    click.echo()
    click.echo(f"  Initialized gapp app: {result['name']}")
    if fr := result.get("fresh_result"):
        click.echo(f"    Kept the old file as {fr['backup']}.")
    click.echo(f"    gapp.yaml {result['manifest_status']} ✓")
    source = {"gapp.yaml": "from the old file" if fr else "from gapp.yaml",
              "repo": "the origin repo's name, now written to gapp.yaml",
              "folder": "the folder's name (no origin remote), now written to gapp.yaml"}[result["name_source"]]
    if fr and fr["old_name_unknown"]:
        source += (f"; the old file's name couldn't be read. If {fr['backup']} names the deployment "
                   "differently, put that name back with: gapp manifest set name <name> --new-deployment "
                   "(the flag is needed because this file's name changes; it points the file back at that "
                   "existing deployment)")
    click.echo(f"    Name: {result['name']} ({source})")
    click.echo("      The name keys the service, data bucket and secrets; changing it later makes a new deployment.")
    topic = {"already_set": "already set"}.get(result["topic_status"], result["topic_status"])
    click.echo(f"    GitHub topic 'gapp-solution' {topic} ✓")
    from gapp.admin.sdk.start import describe
    if result["start"]["method"]:
        click.echo(f"    Starts with: {describe(result['start'])}")
    click.echo("    Settings (public access, domain, environment variables, secrets): gapp manifest show")
    click.echo()
    if result.get("next_step"):
        first, _, rest = result["next_step"]["hint"].partition("\n")
        click.echo(f"  Not deployable yet: {first}")
        for line in rest.splitlines():
            click.echo(f"  {line}")
        return
    click.echo("  No GCP project attached yet.")
    click.echo("  Next: gapp setup --project <project-id>")


@main.command("setup")
@click.option("--project", help="Explicit GCP Project ID.")
@click.pass_obj
def setup_cmd(sdk: GappSDK, project):
    """GCP foundation: enable APIs, create and label the solution bucket, mark the project hosted.

    Once per solution per project, idempotent. Needs a gcloud login with
    project-admin rights (enable services, create buckets, edit IAM).
    Runs in the app's checkout. --project is required the first time;
    later runs find it among the projects marked gapp=hosted. Next: `gapp secrets set` for any declared secrets,
    then `gapp deploy`. (CI deploys are the other fleet system: `gapp ci`.)
    """
    try:
        result = sdk.setup(project)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)

    click.echo()
    click.echo(f"  {result['name']} → {result['project_id']}")
    click.echo()
    if result["apis"]:
        for api in result["apis"]:
            click.echo(f"    API {api} enabled ✓")
    click.echo(f"    Bucket gs://{result['bucket']} {result['bucket_status']} ✓")
    if result["label_status"] == "skipped":
        click.echo(f"    Project {result['project_id']} not marked hosted: a CI run never writes project labels "
                   f"(`gapp ci project setup {result['project_id']}` marks it)")
    else:
        click.echo(f"    Project {result['project_id']} marked hosted (gapp=hosted) {result['label_status']} ✓")
    click.echo()
    if result.get("start_problem"):
        first, _, rest = result["start_problem"].partition("\n")
        click.echo(f"  Next: {first}")
        for line in rest.splitlines():
            click.echo(f"  {line}")
        click.echo()


@main.command("deploy")
@click.option("--deployment", "-d", "deployment", default=None, metavar="NAME",
              help="The deployment. Left out inside a clone: the one this repo has.")
@click.option("--dry-run", is_flag=True, help="Say what a deploy would do, and build and change nothing.")
@click.option("--rebuild", is_flag=True, help="Build the image even if one exists for this commit.")
@click.option("--replace-domain", is_flag=True,
              help="Allow the deploy to replace or remove the live custom domain. The old domain stops serving.")
@click.option("--upgrade", is_flag=True,
              help="Apply a pending gapp upgrade. Required when one is pending, refused when none is.")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
@click.pass_obj
def deploy(sdk: GappSDK, deployment, dry_run, rebuild, replace_domain, upgrade, json_out):
    """Build a deployment's ref from this clone and deploy it. Prints the URL.

    \b
    Run it in a clone of the deployment's repo, checked out at what its
    definition builds. What it deploys comes from the definition:
      gapp show         the definition and its settings
      gapp set ref ...  what it builds
    Once per project first: gapp project setup <project>
    """
    from gapp.admin.sdk.definitions import Refused
    try:
        result = sdk.deploy_definition(deployment, dry_run=dry_run, rebuild=rebuild,
                                       replace_domain=replace_domain, upgrade=upgrade)
    except Refused as e:
        if json_out:
            click.echo(json_mod.dumps({"refused": True, "message": e.message}, indent=2))
        else:
            click.echo(e.message, err=True)
        raise SystemExit(2)
    except (RuntimeError, ValueError) as e:
        first, _, rest = str(e).partition("\n")
        click.echo(f"  Error: {first}", err=True)
        for line in rest.splitlines():
            click.echo(f"  {line}", err=True)
        raise SystemExit(1)
    if json_out:
        click.echo(json_mod.dumps(result, indent=2, default=str))
        return
    builds = f"{result['ref'] or 'the default branch'} at {result['commit']}"
    if dry_run:
        click.echo(f"{result['deployment']} would deploy to {result['project']}, building {builds}, with gapp {result['gapp']}.")
        if result["first_deploy"]:
            click.echo("It has not been deployed before: this would be its first deploy.")
        elif result["upgrade_pending"]:
            click.echo(f"It was last deployed by gapp {result['last_deployed_by']}: this deploy applies an upgrade "
                       f"and needs --upgrade.")
        else:
            click.echo("No upgrade is pending.")
        click.echo("Nothing was built or changed.")
        return
    click.echo(f"{result['deployment']} is deployed to {result['project']}, built from {builds}.")
    if result.get("upgraded_from"):
        click.echo(f"Upgraded from gapp {result['upgraded_from']} to {result['gapp']}.")
    if result.get("service_url"):
        click.echo(f"URL: {result['service_url']}")


def _render_status(data: dict) -> list[str]:
    """The lines of `gapp status` from its result dict (also what `gapp ci status --live` prints)."""
    lines = []
    deployment = data.get("deployment") or {}
    lines.append("App:      (not initialized here)" if not data.get("initialized") else f"App:      {data.get('name')}")
    if deployment.get("project"):
        lines.append(f"Project:  {deployment['project']}")
    if data.get("initialized") and deployment:
        lines.append(f"Status:   {'READY' if not deployment.get('pending', True) else 'PENDING'}")
        for s in deployment.get("services") or []:
            lines.append(f"  Service: {s.get('name')}")
            lines.append(f"  URL:     {s.get('url')}")
            lines.append(f"  Healthy: {'✓' if s.get('healthy') else 'X'}")
    if domain := data.get("domain"):
        lines.append(f"Domain:   {domain.get('name')} ({domain.get('status')})")
        if domain.get("status") == "pending_dns":
            lines.append(f"  CNAME:   {domain.get('name')} -> {domain.get('cname_target')}")
        if domain.get("detail"):
            lines.append(f"  {domain['detail']}")
    for warning in data.get("warnings") or []:
        lines.append(warning)
    if next_step := data.get("next_step"):
        lines.append(f"\nNext Step: {next_step.get('action')}")
        if next_step.get("hint"):
            lines.append(f"  {next_step['hint']}")
    return lines


@main.command("status")
@click.option("--deployment", "-d", "deployment", default=None, metavar="NAME",
              help="The deployment. Left out inside a clone: the one this repo has.")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
@click.pass_obj
def status(sdk: GappSDK, deployment, json_out):
    """What is live for a deployment: its URL and health, the domain's state, and the next step."""
    from gapp.admin.sdk.definitions import Refused
    try:
        result = sdk.status_definition(deployment)
    except Refused as e:
        if json_out:
            click.echo(json_mod.dumps({"refused": True, "message": e.message}, indent=2))
        else:
            click.echo(e.message, err=True)
        raise SystemExit(2)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if json_out:
        click.echo(json_mod.dumps(result, indent=2, default=str))
        return
    click.echo(f"{result['deployment']}, on {result['project']}")
    if result["deployed"]:
        click.echo(f"  URL      {result['url']}")
        click.echo(f"  Healthy  {'yes' if result['healthy'] else 'no'}")
        click.echo(f"  Deployed by gapp {result['last_deployed_by'] or 'unknown'}")
    if domain := result["domain"]:
        click.echo(f"  Domain   {domain.get('name')} ({domain.get('status')})")
        if domain.get("status") == "pending_dns":
            click.echo(f"    CNAME  {domain.get('name')} -> {domain.get('cname_target')}")
        if domain.get("detail"):
            click.echo(f"    {domain['detail']}")
    if result["next_step"]:
        click.echo(result["next_step"])


@main.command("list")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
@click.pass_obj
def list_cmd(sdk: GappSDK, json_out):
    """Every deployment in your fleet: its repo, project, what it builds, and its gapp version."""
    from gapp.admin.sdk.definitions import Refused
    try:
        result = sdk.list_definitions()
    except Refused as e:
        click.echo(e.message, err=True)
        raise SystemExit(2)
    if json_out:
        click.echo(json_mod.dumps(result, indent=2, default=str))
        return
    click.echo(f"Your fleet is kept in {result['fleet']}.")
    rows = result["deployments"]
    if not rows:
        click.echo("It holds no deployments yet. Define one: gapp add <name> --project <project>")
        return
    width = max(len(r["deployment"]) for r in rows)
    for r in rows:
        note = f"  ({'upgrade available' if r['upgrade'] == 'available' else r['upgrade']})" if r["upgrade"] else ""
        click.echo(f"  {r['deployment']:<{width}}  {r['repo'] or 'no repo'}  on {r['project']}  "
                   f"builds {r['ref'] or 'the default branch'}  gapp {r['gapp']}{note}")


@main.group("project")
def project_group():
    """Google Cloud projects that host deployments."""


@project_group.command("setup")
@click.argument("project")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
@click.pass_obj
def project_setup_cmd(sdk: GappSDK, project, json_out):
    """Make a project ready for gapp deployments. Once per project.

    Turns on the Google Cloud APIs every deployment needs, gives Cloud
    Build its permissions, and marks the project. Needs gcloud signed in as
    an Owner of the project. It sets up nothing about CI.
    """
    from gapp.admin.sdk.definitions import Refused
    try:
        result = sdk.project_setup(project)
    except Refused as e:
        click.echo(e.message, err=True)
        raise SystemExit(2)
    except (RuntimeError, ValueError) as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if json_out:
        click.echo(json_mod.dumps(result, indent=2, default=str))
        return
    click.echo(f"{result['project']} is ready for gapp deployments."
               + (" It already was; anything missing was added." if result["was_set_up"] else ""))
    click.echo(f"Next: gapp add <name> --project {result['project']}")


# -- gapp ci --

# Parity: the ci group mirrors the root group's verbs where both have the
# operation (CONTRIBUTING, "Rules of the CI system"); a test checks the names.
CI_PARITY_VERBS = ("setup", "deploy", "status", "list")


def _state(present):
    return "present" if present else "missing" if present is False else "not checked"


def _ci_finish(result, *ok_lines, verbose=False, details=(), after=()):
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
    for line in after:
        if line:
            click.echo(line)


def _ci_print(result, *, verbose=False, details=()):
    """Print a result whose text the SDK built (`lines`, `lines_after`); exit 1 on an outcome."""
    for line in result.get("lines") or []:
        click.echo(line)
    if result.get("ok") is False:
        click.echo(result["next_step"]["hint"])
        raise SystemExit(1)
    if verbose:
        for line in details:
            if line:
                click.echo(f"  {line}")
    for line in result.get("lines_after") or []:
        click.echo(line)


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


def _ci_options(fn):
    """--verbose and --json, on every ci command."""
    fn = click.option("--json", "json_out", is_flag=True,
                      help="Print the command's result as JSON (the same dict the MCP tool returns) and nothing else.")(fn)
    return click.option("--verbose", "-v", is_flag=True, help="Also show the fleet repo, its files, variables and ids.")(fn)


def _emit_json(result):
    """The --json ending: the SDK result exactly, exit 1 on an outcome."""
    click.echo(json_mod.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") is not False else 1)


@main.group("ci")
def ci_group():
    """Deploy through GitHub Actions: the CI fleet system.

    gapp has two fleet systems that do not talk to each other. The root
    commands (setup, deploy, status, list) deploy the local checkout to
    GCP with gcloud. These `ci` commands manage your fleet through GitHub
    only: they never use gcloud (except `ci project setup`) and never read
    the working directory. The fleet is kept in a private fleet repo:
    gapp/deployments.yaml, one entry per deployment, plus gapp's three
    workflows (deploy, register, status). A deployment is one repo deployed to one project under a
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
    <name>` say where things stand; `gapp ci status <name> --live` asks the
    project for the live service state; `--verbose` adds the fleet detail;
    `--json` prints the result dict and nothing else.

    A deployment's settings come from the app repo's gapp.yaml with the
    fleet's manifest for it merged over: `gapp ci manifest <name>` prints
    it, `gapp ci setup <name> ... --manifest FILE --update` replaces it.
    That is how a repo you can't change (even one with no gapp.yaml or
    Procfile; set `start` in the manifest) deploys without a fork.
    """


@ci_group.command("init")
@click.argument("repo", required=False, metavar="[OWNER/REPO]")
@click.option("--owner", default=None, help="Search this GitHub owner's repos for the fleet repo (the gapp-ci topic).")
@click.option("--create", is_flag=True, help="Create OWNER/REPO as a new private fleet repo, with gapp's workflows, and set it.")
@_ci_options
def ci_init_cmd(repo, owner, create, verbose, json_out):
    """Show, find, set or create your fleet repo (the only command that sets it).

    \b
      gapp ci init                     shows the saved fleet repo; if none is saved, finds the one
                                       repo under your account carrying the gapp-ci topic and saves it
      gapp ci init --owner <org>       the same search under that owner
      gapp ci init <owner>/<repo>      sets an existing repo as your fleet repo and installs or
                                       updates gapp's workflows and gapp/deployments.yaml in it
      gapp ci init <owner>/<repo> --create   creates it private, tagged and populated, and sets it

    Needs a `gh` login; installing gapp's files needs one that can write to the repo.

    \b
    Public app repos need nothing more. To deploy private ones, give the fleet
    a GitHub App that can read them, once: on the GitHub organization that
    holds them, create a GitHub App (Settings > Developer settings > GitHub
    Apps) with Contents: Read-only and no webhook, install it there (never on
    all repos of a personal account), generate a private key, and store the
    two in the fleet repo under these fixed names:
      gh variable set HOSTING_GITHUB_APP_ID --repo <fleet-repo> --body <app-id>
      gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo <fleet-repo> < <key>.pem
    then delete the key file. --verbose shows whether both are set.
    """
    from gapp.admin.sdk.ci import init_fleet
    result = init_fleet(repo, owner=owner, create=create)
    if json_out:
        _emit_json(result)
    _ci_finish(result, *(result.get("lines") or []), verbose=verbose, details=_fleet_details(result))


@ci_group.group("project")
def ci_project_group():
    """Per-project trust between the fleet repo and a GCP project."""


@ci_project_group.command("setup")
@click.argument("project")
@_ci_options
def ci_project_setup_cmd(project, verbose, json_out):
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
    if json_out:
        _emit_json(result)
    lines = []
    if result.get("ok") is not False:
        result["next_step"] = {"action": "ci setup", "hint": f"gapp ci setup <name> --repo <owner>/<repo> --project {result['project_id']}"}
        marked = result.get("hosted") == "set"
        mark_only = marked and not any(result.get(k) == "created" for k in ("wif_pool", "wif_provider", "service_account")) \
            and result.get("environment_record") in (None, "unchanged")
        if result.get("changed") and not mark_only:
            lines.append(f"{result['project_id']} now trusts your fleet to deploy to it.")
        elif marked:
            lines.append(f"{result['project_id']} already trusts your fleet to deploy to it; marked it gapp=hosted, "
                         f"so `gapp list` and `gapp status <name>` can find its deployments.")
        else:
            lines.append(f"{result['project_id']} already trusts your fleet to deploy to it. Nothing changed.")
        if marked:
            lines.append("It can take a few minutes before gapp sees the new mark when searching for deployments "
                         "(`gapp list`, and commands run without `--project`).")
    details = _fleet_details(result) + [
        f"{label}: {result[key]}" for key, label in (("wif_pool", "WIF pool"), ("wif_provider", "WIF provider"),
                                                      ("service_account", "Service account"), ("binding", "Binding"),
                                                      ("hosted", "Hosted mark (gapp=hosted)"),
                                                      ("environment_record", f"Environment {result.get('environment')}"))
        if result.get(key)]
    _ci_finish(result, *lines, verbose=verbose, details=details)


@ci_group.group("sources")
def ci_sources_group():
    """The GitHub owners whose repos may be deployed from your fleet.

    The list is the fleet repo's repository variable GAPP_ALLOWED_SOURCES;
    the register run refuses a repo whose owner is not on it. Needs `gh`
    with write access to the fleet repo (read for `list`).
    """


@ci_sources_group.command("add")
@click.argument("owner")
@_ci_options
def ci_sources_add_cmd(owner, verbose, json_out):
    """Allow apps owned by OWNER in your fleet."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("add", owner)
    if json_out:
        _emit_json(result)
    _ci_finish(result, *(result.get("lines") or []), verbose=verbose, details=_fleet_details(result))


@ci_sources_group.command("remove")
@click.argument("owner")
@_ci_options
def ci_sources_remove_cmd(owner, verbose, json_out):
    """Stop allowing apps owned by OWNER."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("remove", owner)
    if json_out:
        _emit_json(result)
    _ci_finish(result, *(result.get("lines") or []), verbose=verbose, details=_fleet_details(result))


@ci_sources_group.command("list")
@_ci_options
def ci_sources_list_cmd(verbose, json_out):
    """Show the allowed owners."""
    from gapp.admin.sdk.ci import manage_sources
    result = manage_sources("list")
    if json_out:
        _emit_json(result)
    _ci_finish(result, *(result.get("lines") or []), verbose=verbose, details=_fleet_details(result))


@ci_group.command("setup")
@click.argument("name", required=False)
@click.option("--repo", default=None, metavar="OWNER/REPO", help="The repo to deploy (never the fleet repo). The name defaults to its repo name.")
@click.option("--project", default=None, metavar="PROJECT", help="GCP project id the deployment deploys to.")
@click.option("--ref", default=None, metavar="REF",
              help="The branch, tag or commit of the repo a deploy builds. Omit to keep the deployment's; "
                   "pass \"\" to remove it (the repo's default branch).")
@click.option("--domain", default=None, help="Custom domain the deploy maps (a gapp.yaml key). Omit to keep the entry's; pass \"\" to remove it.")
@click.option("--copy-of", default=None, metavar="EXISTING", help="Start NAME as a copy of an existing deployment (same repo and keys; its own id; no domain).")
@click.option("--manifest", "manifest_path", default=None, metavar="FILE",
              help="The deployment's whole fleet-side manifest, a gapp.yaml-shaped file. It replaces the entry's "
                   "settings (a key the file lacks is removed) and is merged over the app repo's gapp.yaml at deploy.")
@click.option("--update", is_flag=True, help="Apply a change to an existing deployment's entry. Without it, setup shows the change and makes none.")
@click.option("--no-watch", is_flag=True, help="Return after dispatching the register run instead of waiting for it.")
@_ci_options
def ci_setup_cmd(name, repo, project, ref, domain, copy_of, manifest_path, update, no_watch, verbose, json_out):
    """Add or update a deployment: NAME deploys --repo to --project. Mirrors `gapp setup` for the fleet.

    Never uses gcloud and never reads the working directory: the repo is
    named by --repo and the name defaults to the repo's name. Same name
    again changes its entry only with --update; without it, setup shows
    what would change. A repo already
    deployed under another name is refused: update that one, or add
    another deployment of it with --copy-of. Writes go through the fleet
    repo's register run, so this needs only a `gh` that can dispatch
    workflows; a project your fleet is not yet trusted for, or an owner
    not yet allowed, stops with the one-time step.

    A deployment's settings come from the app repo's gapp.yaml, with the
    fleet's manifest for the deployment merged over it at deploy (keys
    replaced, env variables by name). --manifest FILE sets that fleet-side
    manifest, so a repo you can't change, even one with no gapp.yaml, can
    be deployed, and one repo's deployments can differ. Get the current one
    with `gapp ci manifest NAME`, edit it with `gapp manifest --file FILE
    set ...`, and upload it with --manifest FILE --update. For a repo with
    no Dockerfile or Procfile, set `start` in it.

    What a deploy builds is the deployment's ref (--ref): a branch (its
    tip at each deploy), a tag or a commit. With none, it is the repo's
    default branch. A release, a rollback and trying a branch are changes
    to the ref; a copy keeps its source's unless --ref is given.

    \b
      gapp ci setup --repo me/my-app --project my-project
      gapp ci setup my-app-staging --copy-of my-app --project my-staging-project
      gapp ci setup my-app --repo me/my-app --project my-project --manifest my-app.yaml --update
      gapp ci setup my-app --repo me/my-app --project my-project --ref v1.4.0 --update
      gapp ci setup my-app-try --copy-of my-app --ref my-feature-branch
    """
    from gapp.admin.sdk.ci import setup_deployment
    manifest = None
    if manifest_path is not None:
        from gapp.admin.sdk.manifest import parse_manifest_text
        from gapp.admin.sdk.schema import ManifestUnreadable
        try:
            manifest = parse_manifest_text(Path(manifest_path).read_text())
        except OSError as e:
            click.echo(f"  Error: can't read {manifest_path}: {e.strerror}.", err=True)
            raise SystemExit(1)
        except ManifestUnreadable as e:
            click.echo(f"  Error: {manifest_path}: {str(e).splitlines()[0]}", err=True)
            raise SystemExit(1)
    command = "gapp " + " ".join(sys.argv[1:]) if sys.argv and sys.argv[0] else None
    result = setup_deployment(name, repo=repo, project=project, domain=domain, update=update, ref=ref,
                              copy_of=copy_of, manifest=manifest, watch=not no_watch, command=command)
    if json_out:
        _emit_json(result)
    _ci_print(result, verbose=verbose, details=_fleet_details(result))


@ci_group.command("manifest")
@click.argument("name")
@click.option("--merged", is_flag=True,
              help="Print what a deploy would use instead: this manifest merged over the app repo's gapp.yaml. "
                   "Reads the app repo with your own GitHub login.")
@click.option("--ref", default=None,
              help="With --merged (implies it): the app repo's commit, tag or branch, as for `gapp ci deploy --ref`. "
                   "Default: its default branch.")
@click.option("--json", "json_out", is_flag=True,
              help="Print the command's result as JSON (the same dict the MCP tool returns) and nothing else.")
def ci_manifest_cmd(name, merged, ref, json_out):
    """Print NAME's fleet-side manifest as YAML, and nothing else.

    These are the gapp.yaml keys the fleet sets for the deployment; the
    deploy run merges them over the app repo's gapp.yaml. Save it, edit it
    with `gapp manifest --file FILE ...`, and upload it with `gapp ci setup
    NAME ... --manifest FILE --update`. One read of the fleet repo; the app
    repo is never read.

    With --merged, print what a deploy at --ref would use: the merge done
    the way the deploy run does it, with the app repo's gapp.yaml, top-level
    files and Procfile read through GitHub with your own login. It also says
    how the app starts, and what the deploy would refuse (on stderr, exit
    1). Nothing is saved. Two limits: the deploy run reads the app repo with
    the fleet's own access, so a repo you can't read here can still deploy;
    and it runs the gapp version the fleet's workflows are pinned to, which
    is noted when it differs from this one.

    \b
      gapp ci manifest my-app > my-app.yaml
      gapp ci manifest my-app --merged
      gapp ci manifest my-app --ref v1.4.0
    """
    from gapp.admin.sdk.manifest import dump_manifest
    if merged or ref:
        from gapp.admin.sdk.ci import merged_manifest
        from gapp.admin.sdk.start import describe
        result = merged_manifest(name, ref=ref)
        if json_out:
            _emit_json(result)
        if result.get("merged") is None:
            click.echo(result["next_step"]["hint"], err=True)
            raise SystemExit(1)
        at = result["ref"] + (f" ({result['commit'][:12]})" if result.get("commit") else "")
        source = (f"the fleet-side manifest merged over {result['repo']}'s gapp.yaml" if result["repo_has_gapp_yaml"]
                  else f"the fleet-side manifest alone ({result['repo']} has no gapp.yaml)")
        click.echo(f"# What a deploy of {result['name']} at {at} uses: {source}.")
        if result["start"]["method"]:
            click.echo(f"# Starts with: {describe(result['start'])}")
        for note in result["notes"]:
            click.echo(f"# {note}")
        click.echo(dump_manifest(result["merged"]), nl=False)
        for problem in result["problems"]:
            click.echo(problem, err=True)
        if result["problems"]:
            raise SystemExit(1)
        return
    from gapp.admin.sdk.ci import deployment_manifest
    result = deployment_manifest(name)
    if json_out:
        _emit_json(result)
    if result.get("ok") is False:
        click.echo(result["next_step"]["hint"], err=True)
        raise SystemExit(1)
    click.echo(f"# Fleet-side manifest for {result['name']}, merged over {result['repo']}'s gapp.yaml at deploy.")
    click.echo(dump_manifest(result["manifest"] or {}), nl=False)


@ci_group.command("rename")
@click.argument("old")
@click.argument("new")
@click.option("--no-watch", is_flag=True, help="Return after dispatching the register run instead of waiting for it.")
@_ci_options
def ci_rename_cmd(old, new, no_watch, verbose, json_out):
    """Rename a deployment. Its id and every GCP resource stay as they are."""
    from gapp.admin.sdk.ci import rename_deployment
    result = rename_deployment(old, new, watch=not no_watch)
    if json_out:
        _emit_json(result)
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
            from gapp.admin.sdk.ci import ref_label
            lines.append(f"{result['name']} deploy started ({ref_label(result.get('ref'))}): {result.get('run_url')}")
            result["next_step"] = {"action": "ci status", "hint": f"gapp ci status {result['name']}"}
    _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))


@ci_group.command("deploy")
@click.argument("name")
@click.option("--ref", default=None, hidden=True)
@click.option("--no-watch", is_flag=True, help="Return after dispatch instead of streaming the run.")
@_ci_options
def ci_deploy_cmd(name, ref, no_watch, verbose, json_out):
    """Deploy NAME through your fleet's deploy workflow. Mirrors `gapp deploy` for the fleet.

    Builds the deployment's ref: the branch, tag or commit in its
    definition, or the app repo's default branch when it has none. A
    deploy takes no ref of its own. To build something else, change the
    definition first:

    \b
      gapp ci setup NAME --repo OWNER/REPO --project PROJECT --ref v1.4.0 --update
      gapp ci deploy NAME

    Streams the run until it finishes unless --no-watch is given. Needs
    only a `gh` that can dispatch workflows in the fleet repo; no gcloud
    or terraform on this machine.
    """
    from gapp.admin.sdk.ci import deploy_deployment
    if ref:
        click.echo(f"  Error: `gapp ci deploy` takes no --ref. A deploy builds the deployment's ref, which is part "
                   f"of its definition. Set it, then deploy:\n"
                   f"    gapp ci setup {name} --repo <owner>/<repo> --project <project> --ref {ref} --update\n"
                   f"    gapp ci deploy {name}\n"
                   f"  `gapp ci status {name}` shows its repo, project and current ref.", err=True)
        raise SystemExit(2)
    result = deploy_deployment(name, watch=not no_watch, progress=None if json_out else click.echo)
    if json_out:
        _emit_json(result)
    _print_ci_deploy(result, verbose)


@ci_group.command("trigger", hidden=True)
@click.argument("name")
@click.option("--no-watch", is_flag=True)
def ci_trigger_cmd(name, no_watch):
    """Former name of `gapp ci deploy`."""
    from gapp.admin.sdk.ci import deploy_deployment
    click.echo("`gapp ci trigger` is now `gapp ci deploy`; running it. This alias goes away in the next release.")
    _print_ci_deploy(deploy_deployment(name, watch=not no_watch, progress=click.echo), False)


@ci_group.command("status")
@click.argument("name")
@click.option("--live", is_flag=True, help="Ask the project for the live service state through the fleet's status workflow (takes a runner start).")
@_ci_options
def ci_status_cmd(name, live, verbose, json_out):
    """One deployment: its repo, project, domain and last deploy. Mirrors `gapp status` for the fleet.

    Fast by default and GitHub only; names the step when a deploy cannot
    run as things stand. --live dispatches the fleet repo's read-only
    status workflow, which runs `gapp status` for the deployment on a
    runner with the project's existing deploy identity and returns the
    result: service up or not, URL, domain state. Nothing is stored in the
    fleet repo. `gapp ci list` shows the whole fleet.
    """
    from gapp.admin.sdk.ci import status_deployment, status_live
    if live:
        result = status_live(name, progress=None if json_out else click.echo)
        if json_out:
            _emit_json(result)
        lines = []
        if result.get("ok") is not False:
            lines.append(f"{name}: live state from {result.get('project')} ({result.get('run_url')})")
            lines.extend(_render_status(result.get("live") or {}))
        _ci_finish(result, *lines, verbose=verbose, details=_fleet_details(result))
        return
    result = status_deployment(name)
    if json_out:
        _emit_json(result)
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
        if result.get("next_step") is None:
            lines.append(f"Deploy it: gapp ci deploy {name}")
    details = _fleet_details(result)
    if verbose and result.get("overlay"):
        details.append("gapp.yaml keys set by the fleet: " + ", ".join(sorted(result["overlay"])))
    _ci_finish(result, *lines, verbose=verbose, details=details, after=[result.get("live_hint")])


@ci_group.command("list")
@click.option("--repo", default=None, metavar="OWNER/REPO", help="Only the deployments of this repo.")
@_ci_options
def ci_list_cmd(repo, verbose, json_out):
    """The fleet's deployments, optionally one repo's. Mirrors `gapp list` for the fleet.

    Never touches GCP. --verbose adds the fleet repo's files and variables
    and notes per-solution workflows left from gapp 3.x.
    """
    from gapp.admin.sdk.ci import list_deployments
    result = list_deployments(repo, verbose=verbose)
    if json_out:
        _emit_json(result)
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


@ci_runner_group.command("deploy")
@click.argument("deployment")
@click.option("--upgrade", is_flag=True, help="Apply a pending gapp upgrade.")
def ci_runner_deploy_cmd(deployment, upgrade):
    """Deploy DEPLOYMENT from the app checkout the run stands in; the fleet file is named by GAPP_FLEET_FILE."""
    from gapp.admin.sdk.ci_runner import runner_deploy
    raise SystemExit(runner_deploy(deployment, upgrade=upgrade))


@ci_runner_group.command("status")
@click.argument("deployment")
def ci_runner_status_cmd(deployment):
    """Print what is live for DEPLOYMENT as one line for `gapp ci status --live`."""
    from gapp.admin.sdk.ci_runner import runner_status
    raise SystemExit(runner_status(deployment))


# -- gapp manifest --

def _show_manifest(r):
    if r.get("file"):
        click.echo(f"  {r['file']}" + ("" if r["exists"] else " (not created yet: set something to create it)"))
    elif not r["exists"]:
        click.echo("  No gapp.yaml in this repo. Run `gapp init` to write one.")
        return
    else:
        click.echo(f"  gapp.yaml in {r['repo']}")
    for row in r["settings"]:
        value = row["value"]
        if isinstance(value, bool):
            value = str(value).lower()
        elif isinstance(value, list):
            value = ", ".join(value)
        elif value is None:
            value = "not set"
        click.echo(f"    {row['key']:<15} {str(value):<24} {row['meaning']}")
    if r["env"]:
        click.echo("    env")
        for e in r["env"]:
            click.echo(f"      {e['name']:<24} {e['meaning']}" + (f": {e['value']}" if e["kind"] == "value" else ""))
    else:
        click.echo(f"    {'env':<15} {'none':<24} add one: gapp manifest env set NAME VALUE")
    from gapp.admin.sdk.start import describe as describe_start, missing
    if r["start"] is None:
        pass
    elif r["start"]["method"]:
        click.echo(f"    {'starts':<15} {describe_start(r['start'])}")
    else:
        click.echo(f"    {'starts':<15} {missing(r['name'], r['start']).splitlines()[0]}")
    if r["issues"]:
        click.echo("  gapp.yaml has settings gapp doesn't accept:")
        for issue in r["issues"]:
            click.echo(f"    {issue['path']}: {issue['message']}")
        click.echo("  Fix those lines in gapp.yaml, or start over with `gapp init --fresh` (keeps it as gapp.yaml.bak).")


def _echo_lines(text, err=True):
    for line in str(text).splitlines():
        click.echo(f"  {line}", err=err)


def _manifest_file():
    return click.get_current_context().meta.get("manifest_file")


def _manifest_write(fn, json_out, *args, **kwargs):
    kwargs["file"] = _manifest_file()
    from gapp.admin.sdk.manifest_edit import ManifestChangeRefused
    from gapp.admin.sdk.schema import ManifestUnreadable, ManifestValidationError
    try:
        result = fn(*args, **kwargs)
    except (ManifestChangeRefused, RuntimeError) as e:
        if json_out:
            _emit_json({"ok": False, "error": str(e)})
        _echo_lines(f"Not changed: {e}")
        raise SystemExit(1)
    except (ManifestValidationError, ManifestUnreadable) as e:
        if json_out:
            _emit_json({"ok": False, **e.to_dict()})
        _echo_lines(f"Not changed: {e}")
        raise SystemExit(1)
    if json_out:
        _emit_json(result)
    click.echo(f"  {'Changed' if result['changed'] else 'Unchanged'}: {result['change']}")
    if result.get("new_deployment"):
        nd = result["new_deployment"]
        click.echo(f"  The next setup and deploy make a new deployment, '{nd['new_name']}'; "
                   f"'{nd['old_name']}' keeps running until you remove it.")
    click.echo()
    _show_manifest(result)


_json_option = click.option("--json", "json_out", is_flag=True,
                            help="Print the result as JSON (the same dict the MCP tool returns) and nothing else.")
_new_deployment_option = click.option(
    "--new-deployment", is_flag=True,
    help="Allow a change to the deployment's name. The name keys its service, data bucket and secrets, "
         "so the new name is a new, empty deployment.")


@main.group("fleet", invoke_without_command=True)
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
@click.pass_context
def fleet_group(ctx, json_out):
    """Where your fleet lives.

    \b
    Your fleet is the record of your deployments: for each, its name, the
    repo it runs, its project and its settings. It lives in one place: a
    file on this machine, or a repo once you choose one with
    `gapp fleet use`. There is never a second copy.
    """
    if ctx.invoked_subcommand is not None:
        return
    from gapp.admin.sdk.fleetstore import describe_fleet
    result = describe_fleet()
    if json_out:
        _emit_json(result)
        return
    click.echo(f"Your fleet is kept in {result['where']}.")
    if result["problem"]:
        click.echo(f"  Error: {result['problem']}", err=True)
        raise SystemExit(1)
    count = result["deployments"]
    click.echo("It holds no deployments yet." if not count
               else f"It holds {count} deployment{'' if count == 1 else 's'}.")
    if result["backup"]:
        click.echo(f"The copy from before its last change: {result['backup']}")


@fleet_group.command("use")
@click.argument("target")
@click.option("--create", is_flag=True, help="Make the repo on GitHub first, private, when it is not there.")
@click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")
def fleet_use_cmd(target, create, json_out):
    """Move your fleet to a repo, or back to this machine.

    \b
    gapp fleet use owner/my-fleet     keep it in that repo, as gapp.yaml at its root
    gapp fleet use local              keep it in a file on this machine
    TARGET is owner/repo on GitHub, host/path on another git host, or a git
    address. The fleet is carried across when the place it is going holds
    none. Each later change is one commit to that file. Nothing else is
    put in the repo, and no CI is set up.
    """
    from gapp.admin.sdk import fleetstore
    from gapp.admin.sdk.fleet import FleetRefusal
    try:
        result = fleetstore.use(target, create=create)
    except FleetRefusal as e:
        if json_out:
            click.echo(json_mod.dumps({"refused": True, "message": e.message}, indent=2))
        else:
            click.echo(e.message, err=True)
        raise SystemExit(2)
    except fleetstore.FleetUnavailable as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if json_out:
        click.echo(json_mod.dumps(result, indent=2, default=str))
        return
    if not result["moved"]:
        click.echo(f"Your fleet is already kept in {result['where']}.")
        return
    if result["created"]:
        click.echo("The repo was created, private.")
    count = result["deployments"]
    click.echo(f"Your fleet is now kept in {result['where']}.")
    click.echo("It holds no deployments yet." if not count
               else f"It holds {count} deployment{'' if count == 1 else 's'}"
                    + (", carried across." if result.get("carried") else "."))
    if result.get("backup"):
        click.echo(f"The file it was in is kept as {result['backup']}.")


# --- definitions and their settings (the fleet; no cloud) ---

_DEPLOYMENT = click.option("--deployment", "-d", "deployment", default=None, metavar="NAME",
                           help="The deployment. Left out inside a clone: the one this repo has.")
_JSON = click.option("--json", "json_out", is_flag=True, help="Machine-readable output.")


def _json(result):
    click.echo(json_mod.dumps(result, indent=2, default=str))


def _refused(e, json_out):
    """A refusal: nothing was changed. Exit code 2."""
    if json_out:
        _json({"refused": True, "message": e.message})
    else:
        click.echo(e.message, err=True)
    raise SystemExit(2)


def _say_changes(result, json_out, nothing):
    if json_out:
        _json(result)
        return
    if not result["changed"]:
        click.echo(nothing)
        return
    for change in result["changes"]:
        click.echo(f"{result['deployment']}: {change['key']}: {change['before']} -> {change['after']}")
    if result.get("next"):
        click.echo(f"Set its value: {result['next']}")


def _definition_command(json_out, nothing, fn, *args, **kwargs):
    from gapp.admin.sdk import definitions
    try:
        result = fn(definitions)(*args, **kwargs)
    except definitions.Refused as e:
        _refused(e, json_out)
    _say_changes(result, json_out, nothing)
    return result


@main.command("add")
@click.argument("name", required=False)
@click.option("--project", default=None, metavar="PROJECT", help="The Google Cloud project it runs in.")
@click.option("--repo", default=None, metavar="REPO", help="Where its code is. Inside a clone: the clone's remote.")
@click.option("--ref", default=None, metavar="REF", help="The branch, tag or commit a deploy builds. Left out: the default branch.")
@click.option("--id", "id_", default=None, metavar="ID", help="The prefix on its cloud resources. Left out: the name.")
@click.option("--copy-of", default=None, metavar="EXISTING", help="Make it a second deployment of that one's repo, with its settings.")
@_JSON
def add_cmd(name, project, repo, ref, id_, copy_of, json_out):
    """Define a deployment. Touches no cloud.

    \b
    gapp add --project my-project                 in a clone: named after the repo
    gapp add my-app --project my-project --repo owner/my-app
    gapp add my-app-staging --copy-of my-app --project my-staging --ref develop
    """
    from gapp.admin.sdk import definitions
    try:
        result = definitions.add(name, project=project, repo=repo, ref=ref, id=id_, copy_of=copy_of)
    except definitions.Refused as e:
        _refused(e, json_out)
    if json_out:
        _json(result)
        return
    d = result["definition"]
    click.echo(f"{result['deployment']} is defined: {d.get('repo') or 'no repo'}, on {d['project']}, "
               f"building {d.get('ref') or 'the default branch'}.")
    if result["copied_from"]:
        click.echo(f"Its settings are {result['copied_from']}'s, without the domain.")
    click.echo(f"Nothing is deployed yet. Deploy it from a clone of its repo: gapp deploy --deployment {result['deployment']}")


@main.command("rename")
@click.argument("old")
@click.argument("new")
@_JSON
def rename_cmd(old, new, json_out):
    """Change a deployment's name. Its cloud resources keep theirs."""
    _definition_command(json_out, "", lambda d: d.rename, old, new)


@main.command("show")
@_DEPLOYMENT
@_JSON
def show_cmd(deployment, json_out):
    """One deployment's definition and the settings a deploy would use."""
    from gapp.admin.sdk import definitions
    try:
        result = definitions.show(deployment)
    except definitions.Refused as e:
        _refused(e, json_out)
    if json_out:
        _json(result)
        return
    click.echo(f"{result['deployment']}")
    click.echo(f"  repo     {result['repo'] or 'none'}")
    click.echo(f"  project  {result['project']}")
    click.echo(f"  builds   {result['ref'] or 'the default branch'}")
    click.echo(f"  gapp     {result['gapp']}" + (
        f"  (this is gapp {result['this_gapp']}; upgrade available: gapp upgrade --deployment {result['deployment']})"
        if result["upgrade"] else ""))
    click.echo("  settings")
    for row in result["settings"]:
        click.echo(f"    {row['key']}: {row['value']}  ({row['from']})")


@main.command("upgrade")
@_DEPLOYMENT
@_JSON
def upgrade_cmd(deployment, json_out):
    """Bring one deployment's definition to this gapp's version.

    Changes the definition only. The next deploy applies it, and needs
    --upgrade.
    """
    from gapp.admin.sdk import definitions
    try:
        result = definitions.upgrade(deployment)
    except definitions.Refused as e:
        _refused(e, json_out)
    if json_out:
        _json(result)
        return
    change = result["changes"][0]
    click.echo(f"{result['deployment']}: gapp {change['before']} -> {change['after']}")
    click.echo("No other changes are needed.")
    click.echo("The next deploy applies it: add --upgrade to that deploy.")


@main.command("set")
@click.argument("key")
@click.argument("value")
@_DEPLOYMENT
@_JSON
def set_cmd(key, value, deployment, json_out):
    """Set one value of a deployment's definition.

    \b
    KEY is one of: ref, repo, public, domain, start, memory, cpu, max_instances
    """
    _definition_command(json_out, f"Nothing to change: {key} is already that.",
                        lambda d: d.set_value, key, value, deployment=deployment)


@main.command("unset")
@click.argument("key")
@_DEPLOYMENT
@_JSON
def unset_cmd(key, deployment, json_out):
    """Clear one value of a deployment's definition; its default applies again."""
    _definition_command(json_out, f"Nothing to change: {key} is not set.",
                        lambda d: d.unset_value, key, deployment=deployment)


@main.group("apis")
def apis_group():
    """The extra Google Cloud APIs a deployment's app uses."""


@apis_group.command("add")
@click.argument("api")
@_DEPLOYMENT
@_JSON
def apis_add_cmd(api, deployment, json_out):
    """Add an API, such as drive.googleapis.com."""
    _definition_command(json_out, f"Nothing to change: {api} is already listed.",
                        lambda d: d.apis_add, api, deployment=deployment)


@apis_group.command("remove")
@click.argument("api")
@_DEPLOYMENT
@_JSON
def apis_remove_cmd(api, deployment, json_out):
    """Remove an API from the list."""
    _definition_command(json_out, f"Nothing to change: {api} is not listed.",
                        lambda d: d.apis_remove, api, deployment=deployment)


@main.group("env")
def env_group():
    """The variables a deployment's app reads."""


@env_group.command("set")
@click.argument("name")
@click.argument("value", required=False)
@click.option("--secret", default=None, is_flag=False, flag_value="", metavar="[SECRET]",
              help="Read it from a secret. SECRET left out: the variable's name in lower case with dashes.")
@click.option("--generate", is_flag=True, help="With --secret: gapp makes the value on the first deploy.")
@click.option("--data-path", default=None, is_flag=False, flag_value="", metavar="[SUBFOLDER]",
              help="Point it at the deployment's data folder, which survives restarts.")
@_DEPLOYMENT
@_JSON
def env_set_cmd(name, value, secret, generate, data_path, deployment, json_out):
    """Add or replace one variable.

    \b
    gapp env set MAX_USERS 25
    gapp env set SIGNING_KEY --secret --generate
    gapp env set AUTH_ISSUER --secret auth-issuer
    gapp env set APP_USERS_PATH --data-path users
    """
    _definition_command(json_out, f"Nothing to change: {name} is already that.",
                        lambda d: d.env_set, name, value, secret=secret, generate=generate,
                        data_path=data_path, deployment=deployment)


@env_group.command("unset")
@click.argument("name")
@_DEPLOYMENT
@_JSON
def env_unset_cmd(name, deployment, json_out):
    """Remove one variable. A secret it read is left in place."""
    _definition_command(json_out, f"Nothing to change: {name} is not set.",
                        lambda d: d.env_unset, name, deployment=deployment)


@main.command("apply")
@click.argument("file", type=click.Path(dir_okay=False))
@_DEPLOYMENT
@_JSON
def apply_cmd(file, deployment, json_out):
    """Replace a deployment's settings with a file's, all at once.

    \b
    FILE holds the settings and nothing else, for example:
      public: true
      domain: app.example.com
      env:
        MAX_USERS: {value: "25"}
    A setting the file leaves out goes back to its default.
    """
    from gapp.admin.sdk import definitions
    path = Path(file)
    if not path.is_file():
        _refused(definitions.Refused(f"{file} is not a file."), json_out)
    _definition_command(json_out, "Nothing to change: the settings are already the file's.",
                        lambda d: d.apply, path.read_text(), deployment=deployment, source=file)


@main.group("manifest")
@click.option("--file", "manifest_file", default=None, metavar="FILE",
              help="Show or change FILE, a gapp.yaml-shaped file such as a deployment's fleet-side manifest "
                   "(from `gapp ci manifest`), instead of the repo's gapp.yaml. Created on the first change.")
@click.pass_context
def manifest_group(ctx, manifest_file):
    """Show or change this app's gapp.yaml.

    \b
    gapp manifest show                     this repo's settings, each with what it means
    gapp manifest set KEY VALUE            name, public, domain, start, memory, cpu, max-instances
    gapp manifest unset KEY                remove one of those (back to its default)
    gapp manifest env set NAME VALUE       a plain environment variable
    gapp manifest env set NAME --secret S [--generate]   a variable read from a secret
    gapp manifest env set NAME --data-path [SUBFOLDER]   the data volume's path
    gapp manifest env unset NAME
    gapp manifest example                  every key gapp.yaml takes, annotated
    gapp manifest schema                   the JSON Schema, for tools

    Each change is checked against the schema and written only when the
    file changes. How the app starts is not in gapp.yaml: it is a Procfile
    web: line or a Dockerfile. A file gapp can't read is never changed;
    `gapp init --fresh` starts over and keeps it as gapp.yaml.bak.

    --file FILE points every command at another gapp.yaml-shaped file, such
    as a deployment's fleet-side manifest: `gapp ci manifest NAME > FILE`,
    `gapp manifest --file FILE set memory 1Gi`, then `gapp ci setup NAME ...
    --manifest FILE --update`. Such a file has no name.

    Two advanced keys have no command; edit gapp.yaml at the root of the
    app's git repo (`gapp manifest show` prints its path) for them: `paths`,
    for a repo with several services, and `prerequisites.apis`, for extra
    GCP APIs. `gapp manifest example` shows their format, and every gapp
    command checks the file.
    """
    ctx.meta["manifest_file"] = manifest_file



@manifest_group.command("show")
@_json_option
def manifest_show_cmd(json_out):
    """This repo's gapp.yaml settings as configured now, each with what it means.

    Every setting is listed, set or not (an unset one shows its default),
    with each environment variable by kind and how the app starts. --json
    prints the result dict and nothing else.
    """
    from gapp.admin.sdk.manifest_edit import describe
    try:
        result = describe(file=_manifest_file())
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)
    if json_out:
        _emit_json(result)
    _show_manifest(result)
    if result.get("file"):
        click.echo()
        click.echo(f"  Change: gapp manifest --file {result['file']} set|unset KEY, ... env set|unset NAME")
        click.echo(f"  Upload: gapp ci setup NAME --repo OWNER/REPO --project PROJECT --manifest {result['file']} --update")
    elif result["exists"]:
        click.echo()
        click.echo("  Change: gapp manifest set|unset KEY, gapp manifest env set|unset NAME")
        click.echo("  Every key: gapp manifest example. paths and prerequisites.apis have no command:")
        click.echo(f"  edit {Path(result['repo']) / 'gapp.yaml'} for those.")


@manifest_group.command("set")
@click.argument("key")
@click.argument("value")
@_new_deployment_option
@_json_option
def manifest_set_cmd(key, value, new_deployment, json_out):
    """Set KEY to VALUE in gapp.yaml.

    \b
    KEY is one of:
      name           the deployment's name (needs --new-deployment to change it)
      public         true: anyone can call the URL; false: invoker access only
      domain         custom subdomain, e.g. app.example.com
      start          the start command, only for a repo with no Dockerfile or Procfile
      memory         memory per instance, e.g. 1Gi (default 512Mi)
      cpu            CPUs per instance, e.g. 2 (default 1)
      max-instances  most instances at once (default 1)
    """
    from gapp.admin.sdk.manifest_edit import set_value
    _manifest_write(set_value, json_out, key, value, new_deployment=new_deployment)


@manifest_group.command("unset")
@click.argument("key")
@_new_deployment_option
@_json_option
def manifest_unset_cmd(key, new_deployment, json_out):
    """Remove KEY from gapp.yaml, so it takes its default.

    KEY is one of the settings `gapp manifest set` takes. Unsetting name
    makes the name the repo's again, which needs --new-deployment if that
    differs.
    """
    from gapp.admin.sdk.manifest_edit import unset_value
    _manifest_write(unset_value, json_out, key, new_deployment=new_deployment)


@manifest_group.group("env")
def manifest_env_group():
    """Environment variables the deployed service gets."""


@manifest_env_group.command("set")
@click.argument("name")
@click.argument("value", required=False)
@click.option("--secret", "secret", metavar="SECRET",
              help="Read the value from this secret. Set the secret's value with `gapp secrets set SECRET`.")
@click.option("--generate", is_flag=True, help="With --secret: gapp generates a random value on the first deploy.")
@click.option("--data-path", is_flag=True,
              help="The data volume's path, kept across deploys; VALUE is an optional subfolder.")
@_json_option
def manifest_env_set_cmd(name, value, secret, generate, data_path, json_out):
    """Add or replace the variable NAME.

    \b
      gapp manifest env set LOG_LEVEL INFO
      gapp manifest env set API_KEY --secret api-key
      gapp manifest env set SIGNING_KEY --secret signing-key --generate
      gapp manifest env set APP_USERS_PATH --data-path users
    """
    from gapp.admin.sdk.manifest_edit import env_set
    _manifest_write(env_set, json_out, name, value, secret=secret, generate=generate, data_path=data_path)


@manifest_env_group.command("unset")
@click.argument("name")
@_json_option
def manifest_env_unset_cmd(name, json_out):
    """Remove the variable NAME. A secret it read stays in Secret Manager."""
    from gapp.admin.sdk.manifest_edit import env_unset
    _manifest_write(env_unset, json_out, name)


@manifest_group.command("schema")
def manifest_schema_cmd():
    """Print the JSON Schema for gapp.yaml, and nothing else.

    It comes from the model gapp validates against, so it always matches
    this version of gapp. For a commented gapp.yaml: gapp manifest example.
    """
    from gapp.admin.sdk.schema import get_schema
    click.echo(json_mod.dumps(get_schema(), indent=2))


@manifest_group.command("example")
def manifest_example_cmd():
    """Print an annotated gapp.yaml with every key, and nothing else.

    Each key's comment is its schema description, so the example always
    matches this version of gapp. The output is valid YAML.
    """
    from gapp.admin.sdk.manifest_edit import example as example_text
    click.echo(example_text(), nl=False)


@main.group("secrets")
def secrets_group():
    """List, get, and set a deployment's secret values.

    \b
    A variable reads a secret once its definition says so:
      gapp env set API_KEY --secret                       you set the value
      gapp env set SIGNING_KEY --secret --generate        gapp makes one on the first deploy
    then:
      gapp secrets list            each secret's state, with the commands that fix it
      gapp secrets set api-key     store a value (prompts; --from-stdin to pipe)
    Each is stored in Secret Manager as <deployment id>-<secret name>. The
    value goes from this machine to Secret Manager and nowhere else.
    """


def _secrets_call(json_out, fn, *args, **kwargs):
    from gapp.admin.sdk.definitions import Refused
    try:
        return fn(*args, **kwargs)
    except Refused as e:
        _refused(e, json_out)
    except RuntimeError as e:
        click.echo(f"  Error: {e}", err=True)
        raise SystemExit(1)


@secrets_group.command("list")
@_DEPLOYMENT
@_JSON
def secrets_list_cmd(deployment, json_out):
    """A deployment's secrets, whether each has a value, and what to do about any that don't."""
    from gapp.admin.sdk.secrets import list_secrets
    result = _secrets_call(json_out, list_secrets, deployment=deployment)
    if json_out:
        _json(result)
        return
    click.echo(f"Deployment: {result['deployment']}")
    click.echo(f"Project:    {result['project_id']}")
    if result["secrets"]:
        click.echo()
        click.echo(f"  {'Secret':<20} {'Variable':<25} {'Status':<18} Generate")
        click.echo("  " + "-" * 70)
        for s in result["secrets"]:
            click.echo(
                f"  {s['name']:<20} {s['env_var']:<25} "
                f"{s['status']:<18} {'yes' if s['generate'] else 'no'}"
            )
    else:
        click.echo(f"\n  {result['deployment']} reads no secrets.")
    if result["orphans"]:
        click.echo(f"\n  In Google Cloud for {result['deployment']}, but read by no variable:")
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
@click.option("--plaintext", is_flag=True, help="Print the value itself (default: a hash and the length only).")
@_DEPLOYMENT
@_JSON
def secrets_get_cmd(name, plaintext, deployment, json_out):
    """Whether a secret is set. Prints a hash and the length; --plaintext prints the value."""
    from gapp.admin.sdk.secrets import get_secret
    result = _secrets_call(json_out, get_secret, name, plaintext=plaintext, deployment=deployment)
    if json_out:
        _json(result)
    elif plaintext:
        click.echo(result["value"])
    else:
        click.echo(f"  Name:      {result['name']}")
        click.echo(f"  Secret ID: {result['secret_id']}")
        click.echo(f"  Hash:      {result['hash']}")
        click.echo(f"  Length:    {result['length']}")


@secrets_group.command("set")
@click.argument("name")
@click.option("--from-stdin", is_flag=True, help="Read the value from stdin, to pipe it in.")
@_DEPLOYMENT
@_JSON
def secrets_set_cmd(name, from_stdin, deployment, json_out):
    """Store a secret's value. Prompts for it, or reads it from stdin with --from-stdin.

    The value is never an argument, so it does not land in shell history.
    """
    from gapp.admin.sdk.secrets import _find_secret, set_secret
    _secrets_call(json_out, _find_secret, name, deployment)      # the deployment and the secret, before asking
    if from_stdin:
        value = sys.stdin.read().rstrip("\n")
    else:
        value = click.prompt("Value", hide_input=True, confirmation_prompt=True)
    result = _secrets_call(json_out, set_secret, name, value, deployment=deployment)
    if json_out:
        _json(result)
        return
    click.echo(f"  set: {result.get('secret_id', name)}")


def cli_entry():
    from gapp.admin.sdk.config import ConfigError
    from gapp.admin.sdk.schema import ManifestUnreadable, ManifestValidationError
    try:
        main(standalone_mode=True)
    except (ManifestUnreadable, ManifestValidationError) as e:
        for line in str(e).splitlines():
            click.echo(f"  {line}", err=True)
        sys.exit(1)
    except ConfigError as e:
        click.echo(f"  Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli_entry()
