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


def _emit_json(result):
    """The --json ending: the result exactly. Exit 2 on a refusal, 1 on a failure."""
    click.echo(json_mod.dumps(result, indent=2, default=str))
    if result.get("ok") is False:
        raise SystemExit(2 if result.get("refused") else 1)
    raise SystemExit(0)


def _ci_end(result, json_out):
    """How every `gapp ci` command ends.

    Done: the lines the result carries, then "Next: <command>" when there
    is one. Refused or failed: the one sentence that says why and what to
    run, and exit code 2 or 1.
    """
    if json_out:
        _emit_json(result)
    if result.get("ok") is False:
        click.echo(result["next_step"]["hint"], err=True)
        raise SystemExit(2 if result.get("refused") else 1)
    for line in result.get("lines") or []:
        click.echo(line)
    if next_step := result.get("next_step"):
        click.echo(f"Next: {next_step['hint']}")


_ci_deployment = click.option("--deployment", "-d", "deployment", default=None, metavar="NAME",
                              help="The deployment. Left out inside a clone: the one this repo has.")
_ci_json = click.option("--json", "json_out", is_flag=True,
                        help="Print the result as JSON (what the MCP tool returns) and nothing else.")


@main.group("ci")
def ci_group():
    """Deploy on GitHub Actions runners. Optional.

    Everything gapp does with GitHub Actions is here, and nothing else in
    gapp needs it. A runner does the deploy `gapp deploy` does, so a
    machine with only `gh` can start one. These commands need the fleet to
    be kept in a GitHub repo (`gapp fleet use <owner>/<repo>`); the
    workflows run in that repo and read its gapp.yaml. They never change
    the fleet: a definition is changed with `gapp add`, `gapp set` and the
    other fleet commands.

    \b
    From a fleet in a repo to a deploy on a runner:
      gapp ci init                         installs the workflows in the fleet repo
      gapp ci sources add <owner>          allows the owner whose repos runners deploy
      gapp project setup <project>         once per project (gcloud as an Owner)
      gapp ci projects connect <project>   once per project: lets runners sign in to it
      gapp ci deploy --deployment <name>   deploys it on a runner
      gapp ci status --deployment <name>   the last run; --live asks the project
    Each command's output names the next one.

    \b
    A public app repo needs nothing more. To deploy private ones, give the
    fleet repo a GitHub App that can read them: on the organization that
    holds them, create a GitHub App with Contents: Read-only and no
    webhook, install it there, generate a private key, and store the two:
      gh variable set HOSTING_GITHUB_APP_ID --repo <fleet-repo> --body <app-id>
      gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo <fleet-repo> < <key>.pem
    """


@ci_group.command("init")
@_ci_json
def ci_init_cmd(json_out):
    """Install the deploy and status workflows in the fleet repo.

    Writes .github/workflows/gapp-deploy.yml and gapp-status.yml there and
    nothing else. Run it again after installing another gapp to bring the
    two files up to it. Needs `gh` signed in as an admin of the fleet repo.
    Refused when the fleet is not kept in a GitHub repo.
    """
    from gapp.admin.sdk import ci
    _ci_end(ci.init_workflows(), json_out)


@ci_group.group("projects")
def ci_projects_group():
    """The Google Cloud projects the fleet repo's runners can deploy to."""


@ci_projects_group.command("connect")
@click.argument("project")
@_ci_json
def ci_projects_connect_cmd(project, json_out):
    """Let the fleet repo's runners sign in to PROJECT and deploy. Once per project.

    In the project: the Workload Identity pool and provider, the
    gapp-deploy service account and its roles, and the binding that lets
    runs of the fleet repo act as it. In the fleet repo: the GitHub
    Environment named PROJECT, holding the deploy identity
    (GAPP_WIF_PROVIDER, GAPP_DEPLOY_SERVICE_ACCOUNT). Running it again only
    adds what is missing.

    Refused on a project that is not set up: run `gapp project setup
    PROJECT` first. Needs gcloud signed in as an Owner of PROJECT, and `gh`
    signed in as an admin of the fleet repo.
    """
    from gapp.admin.sdk import ci
    _ci_end(ci.connect_project(project), json_out)


@ci_projects_group.command("list")
@_ci_json
def ci_projects_list_cmd(json_out):
    """The projects the fleet repo's runners can deploy to.

    Read from the fleet repo's record: its GitHub Environments that hold a
    deploy identity. Needs only `gh`; Google Cloud is not asked.
    """
    from gapp.admin.sdk import ci
    _ci_end(ci.list_projects(), json_out)


@ci_group.group("sources")
def ci_sources_group():
    """The GitHub owners whose repos runners may deploy.

    The list is the fleet repo's repository variable GAPP_ALLOWED_SOURCES.
    A deploy run refuses a deployment whose repo belongs to an owner that
    is not on it. Needs `gh` with write access to the fleet repo (read
    access for `list`).
    """


@ci_sources_group.command("add")
@click.argument("owner")
@_ci_json
def ci_sources_add_cmd(owner, json_out):
    """Let runners deploy repos owned by OWNER."""
    from gapp.admin.sdk import ci
    _ci_end(ci.manage_sources("add", owner), json_out)


@ci_sources_group.command("remove")
@click.argument("owner")
@_ci_json
def ci_sources_remove_cmd(owner, json_out):
    """Stop runners deploying repos owned by OWNER."""
    from gapp.admin.sdk import ci
    _ci_end(ci.manage_sources("remove", owner), json_out)


@ci_sources_group.command("list")
@_ci_json
def ci_sources_list_cmd(json_out):
    """Show whose repos runners may deploy."""
    from gapp.admin.sdk import ci
    _ci_end(ci.manage_sources("list"), json_out)


@ci_group.command("deploy")
@_ci_deployment
@click.option("--upgrade", is_flag=True,
              help="Apply a pending gapp upgrade. Required when one is pending, refused when none is.")
@click.option("--no-watch", is_flag=True, help="Return once the run is started, without waiting for it.")
@_ci_json
def ci_deploy_cmd(deployment, upgrade, no_watch, json_out):
    """Deploy a deployment on a runner: the deploy `gapp deploy` does, from GitHub Actions.

    The run checks out the ref the definition builds, installs the gapp
    version the definition names, and deploys. What it deploys comes from
    the definition and nothing else:

    \b
      gapp show         the definition and its settings
      gapp set ref ...  what it builds

    Waits for the run and prints what it said, unless --no-watch is given.
    Refused before a run is started when the definition has no repo on
    GitHub, when runners are not connected to its project (`gapp ci
    projects connect`), or when the workflows are not installed (`gapp ci
    init`). Needs only `gh`; no gcloud or Terraform on this machine.
    """
    from gapp.admin.sdk import ci
    result = ci.deploy(deployment, upgrade=upgrade, watch=not no_watch,
                       progress=None if json_out else click.echo)
    _ci_end(result, json_out)


@ci_group.command("status")
@_ci_deployment
@click.option("--live", is_flag=True,
              help="Ask the project what is live, through the fleet repo's status workflow. Takes a runner start.")
@_ci_json
def ci_status_cmd(deployment, live, json_out):
    """The last deploy run of a deployment, and what its definition says.

    Reads GitHub only: the definition's repo, project, ref and gapp
    version, and the newest run of the deploy workflow for it. --live
    starts the status workflow instead: a runner signs in to the project
    as a deploy does and reports the URL, whether it answers, and the
    domain's state. Nothing is written anywhere either way.
    """
    from gapp.admin.sdk import ci
    result = ci.status(deployment, live=live, progress=None if json_out else click.echo)
    _ci_end(result, json_out)


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
