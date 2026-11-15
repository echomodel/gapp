"""gapp's MCP server, on stdio: one tool per command, with the same arguments.

A tool returns what the command prints with `--json`. A refusal comes
back as data, `{"refused": true, "message": ...}`, and changes nothing.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("gapp-admin")


def _tool():
    return mcp.tool()


@_tool()
def gapp_deploy(deployment: str | None = None, dry_run: bool = False, rebuild: bool = False,
                replace_domain: bool = False, upgrade: bool = False) -> dict:
    """Build a deployment's ref from the clone the server runs in and
    deploy it to Cloud Run, from its definition in the fleet. The clone
    must be of the definition's repo and checked out at what it builds;
    the project must have had `gapp_project_setup` once.

    Call it with `dry_run=True` first: it reports the commit, the project,
    `upgrade_pending`, and what the app's own `app.json` adds at that
    commit: `defaults_from_app_json` (variables taken from its defaults),
    `missing_variables` (variables it needs that the definition does not
    set; a deploy is refused until each is set with gapp_env_set, and
    `generated` true means a generated secret suits it) and `warnings`
    (variables the definition sets that `app.json` does not list, which
    the app may not read; relay them, they stop nothing). `app.json` is
    read only by a deploy; the definition always wins over it.
    Pass `upgrade=True` exactly when `upgrade_pending` is true;
    it is refused otherwise, and a deploy that would apply an upgrade is
    refused without it. `replace_domain=True` allows replacing or removing
    a live custom domain, which stops the old one serving. `rebuild` builds
    the image even if one exists for the commit.

    A refusal comes back as `{"refused": true, "message": ...}`: nothing
    was built or changed, and the message names what to do.
    """
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.definitions import Refused
    try:
        return GappSDK().deploy_definition(deployment, dry_run=dry_run, rebuild=rebuild,
                                           replace_domain=replace_domain, upgrade=upgrade)
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_project_setup(project: str) -> dict:
    """Make a Google Cloud project ready for gapp deployments, once per
    project: turns on the APIs every deployment needs, gives Cloud Build
    its permissions, makes the project's gapp bucket (Terraform state and
    the record of each deploy), and marks the project. Running it again
    adds only what is missing. Needs gcloud signed in as an
    Owner of the project. Sets up nothing about CI.
    """
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.definitions import Refused
    try:
        return GappSDK().project_setup(project)
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_secrets_get(name: str, plaintext: bool = False, deployment: str | None = None) -> dict:
    """Whether a deployment's secret is set. `name` is the secret's short
    name, as `gapp_secrets_list` shows it (for example "app-key").
    By default returns a hash prefix and the length, enough to confirm it
    exists without exposing it. `plaintext=True` returns the value itself:
    use it only when the user asks for the value.
    """
    from gapp.admin.sdk.definitions import Refused
    from gapp.admin.sdk.secrets import get_secret
    try:
        return get_secret(name, plaintext=plaintext, deployment=deployment)
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_secrets_list(deployment: str | None = None) -> dict:
    """A deployment's secrets and whether each has a value. Call it before
    `gapp_deploy`: a deploy fails when a secret that is not generated has
    no value.

    Each entry has `name` (the secret's short name), `env_var` (the
    variable that reads it), `secret_id`, `generate` (true when gapp
    makes its value at the first deploy) and `status`, the secret's state
    in Secret Manager: "ready" (it has a value), "missing", "unattached"
    (one exists without gapp's label) or "conflict" (it belongs to another
    deployment). A "missing" secret with `generate` true needs nothing:
    the next deploy makes it. A "missing" one with `generate` false stops
    a deploy until the user sets it. `status` says nothing about the
    running service: a value set after the last deploy reaches the app at
    the next one. `orphans` are secrets labelled for this deployment that
    no variable reads. `hints` give the commands that fix each case.

    No tool stores a secret's value. The user stores one themselves, at a
    prompt: `gapp secrets set <name> --deployment <deployment>`.
    """
    from gapp.admin.sdk.definitions import Refused
    from gapp.admin.sdk.secrets import list_secrets
    try:
        return list_secrets(deployment=deployment)
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_ci_init() -> dict:
    """Install the deploy and status workflows in the fleet repo, so
    deployments can be deployed on GitHub Actions runners.

    The gapp_ci_* tools are everything gapp does with GitHub Actions, and
    they are optional: gapp_deploy deploys from a clone with no CI. They
    need the fleet to be kept in a GitHub repo (gapp_fleet says where it
    is; gapp_fleet_use moves it) and the GitHub CLI, `gh`, signed in. They
    never change the fleet: a definition is changed with gapp_add, gapp_set
    and the other fleet tools.

    The order, from a fleet in a repo to a deploy on a runner: gapp_ci_init,
    gapp_ci_sources("add", owner), gapp_project_setup(project),
    gapp_ci_projects("connect", project), gapp_ci_deploy(deployment).

    This tool takes no arguments. It writes
    .github/workflows/gapp-deploy.yml and gapp-status.yml in the fleet repo
    and nothing else; calling it again brings the two files up to the
    installed gapp's. It needs `gh` signed in as an admin of the fleet repo.

    Every gapp_ci_* tool returns `ok`. When it is true, `lines` is what to
    tell the user and `next_step.hint` is the next command, when there is
    one. When it is false, `next_step.hint` is one sentence saying why and
    the exact command that fixes it, and `refused` says which kind it is:
    true means the request was refused and nothing was started or changed,
    false means something failed part way. Here `files` maps each workflow
    to "created", "updated" or "unchanged".
    """
    from gapp.admin.sdk import ci
    return ci.init_workflows()


@_tool()
def gapp_ci_projects(action: str, project: str | None = None) -> dict:
    """Connect a Google Cloud project to the fleet repo's runners, or list
    the projects they can deploy to.

    action "connect" (needs `project`): lets runs of the fleet repo sign in
    to the project and deploy. Once per project; calling it again only adds
    what is missing. In the project it makes the Workload Identity pool and
    provider and the `gapp-deploy` service account with its roles, and
    binds them to the fleet repo. In the fleet repo it records the deploy
    identity as the GitHub Environment named after the project. It is
    refused on a project that gapp_project_setup has not been run on, and
    the refusal names that step. It needs gcloud signed in as an Owner of
    the project and `gh` signed in as an admin of the fleet repo; when
    either is missing the result says which, and the user runs `gapp ci
    projects connect <project>` on a machine that has both.

    action "list": the projects runners can deploy to, as `projects`, read
    from the fleet repo's Environments. It needs only `gh` and asks
    Google Cloud nothing. `unchecked` names Environments whose variables could not
    be read from this machine.

    Returns `ok`, `lines` and `next_step` as gapp_ci_init describes.

    Args:
        action: "connect" or "list".
        project: The Google Cloud project id. Required for "connect".
    """
    from gapp.admin.sdk import ci
    if action == "connect":
        return ci.connect_project(project or "")
    if action == "list":
        return ci.list_projects()
    return {"ok": False, "refused": True, "next_step": {
        "action": "choose an action", "hint": 'action is "connect" (with a project) or "list".'}}


@_tool()
def gapp_ci_sources(action: str, owner: str | None = None) -> dict:
    """List, add to, or remove from the GitHub owners whose repos the
    fleet repo's runners may deploy.

    A deploy run refuses a deployment whose repo belongs to an owner that
    is not on this list, so add the owner of each app repo once, before its
    first gapp_ci_deploy. The list is kept as the fleet repo's repository
    variable GAPP_ALLOWED_SOURCES, which a change to the fleet cannot
    alter. It needs `gh` with write access to the fleet repo (read access
    for "list").

    Returns `owners` (the list after the call) and `changed`, with `ok`,
    `lines` and `next_step` as gapp_ci_init describes. When `gh` cannot
    read or write the variable from this machine, `next_step.hint` carries
    GitHub's answer and the `gh variable set` command to run elsewhere.

    Args:
        action: "add", "remove" or "list".
        owner: The GitHub user or organization. Required for "add" and "remove".
    """
    from gapp.admin.sdk import ci
    return ci.manage_sources(action, owner)


@_tool()
def gapp_ci_deploy(deployment: str | None = None, upgrade: bool = False, watch: bool = True) -> dict:
    """Deploy one deployment on a GitHub Actions runner: the deploy
    gapp_deploy does, without gcloud, Terraform or a clone on this machine.

    The run reads the deployment's definition from the fleet repo, checks
    out the ref the definition builds (its default branch when it names
    none), installs the gapp version the definition names, and deploys to
    the definition's project. Nothing about the deploy is passed here but
    the deployment's name: to build something else, change the definition
    first with gapp_set("ref", ...).

    It is refused before any run is started, with the command that fixes
    it, when: the fleet is not kept in a GitHub repo; the definition has no
    repo, or one that is not on GitHub; runners are not connected to its
    project (gapp_ci_projects "connect"); the workflows are not installed
    (gapp_ci_init).

    With `watch` true it waits for the run, which takes minutes, and
    returns its `conclusion`, the `run_url`, and `deployed`: what the run
    reported, including `service_url` and `commit`. A run that refused the
    deploy comes back with `refused` true and its reason; a failed run
    comes back with `ok` false and the reason the run printed. With `watch`
    false it returns as soon as the run is found, with `run_url`; ask
    gapp_ci_status later.

    Args:
        deployment: The deployment's name, as gapp_list shows it. May be
            left out only when the server runs inside a clone of the one
            repo a single deployment builds.
        upgrade: Apply a pending gapp upgrade. Pass true exactly when
            gapp_show says the next deploy applies one; the run refuses
            it otherwise, and refuses a deploy that would apply one
            without it.
        watch: Wait for the run to finish (default true).
    """
    from gapp.admin.sdk import ci
    return ci.deploy(deployment, upgrade=upgrade, watch=watch)


@_tool()
def gapp_ci_status(deployment: str | None = None, live: bool = False) -> dict:
    """The last deploy run of a deployment and what its definition says,
    or, with `live`, what is running.

    Without `live` it reads GitHub only and is quick: `repo`, `project`,
    `ref` (null means the default branch) and `gapp` (the version that
    deploys it) from the definition, and `last_deploy.run`, the newest run
    of the deploy workflow for it, with its `status`, `conclusion`,
    `created_at` and `url`. `last_deploy.run` is null when it has never
    been deployed on a runner, and `last_deploy.checked` is false, with a
    `note`, when the runs could not be read.

    With `live` true it starts the fleet repo's status workflow and waits:
    a runner signs in to the project as a deploy does and reports what is
    live. That takes about as long as a runner start. The answer is in
    `live`, the same result gapp_status returns: `deployed`, `url`,
    `healthy`, `domain`, `last_deployed_by`, `upgrade_pending` and
    `next_step`. Use it when this machine has no Google Cloud login;
    gapp_status is quicker when it has one. Nothing is written anywhere.

    Returns `ok`, `lines` and `next_step` as gapp_ci_init describes.

    Args:
        deployment: The deployment's name, as gapp_list shows it. May be
            left out only when the server runs inside a clone of the one
            repo a single deployment builds.
        live: Ask the project what is live, through a runner.
    """
    from gapp.admin.sdk import ci
    return ci.status(deployment, live=live)


@_tool()
def gapp_status(deployment: str | None = None) -> dict:
    """What is live for a deployment of the fleet. Returns `deployed`,
    `url` and `healthy` (an HTTP check of the URL), `domain` (the custom
    domain's state: pending_dns with the CNAME to add, pending_cert,
    active or error), `last_deployed_by` (the gapp version that last
    deployed it), `upgrade_pending` (its definition is on a newer gapp
    than that), and `next_step`, a sentence naming the command to run when
    there is one. A missing or unusable Google Cloud login comes back as
    `next_step` with the login command, not as an error.
    """
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.definitions import Refused
    try:
        return GappSDK().status_definition(deployment)
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_list() -> dict:
    """Every deployment in the fleet: its name, repo, project, the ref it
    builds and the gapp version that wrote its definition. `upgrade` is
    "available" when this gapp is newer than a definition. `fleet` says
    where the fleet is kept.
    """
    from gapp.admin.sdk.core import GappSDK
    from gapp.admin.sdk.definitions import Refused
    try:
        return GappSDK().list_definitions()
    except Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_fleet() -> dict:
    """Where the fleet lives: a file on this machine (`kind: local`, `path`)
    or a GitHub repo (`kind: github`, `repo`), and how many deployments it
    holds. The fleet is the record of the deployments (each one's name,
    repo, project and settings) and lives in exactly one place. `problem`
    is set, and `deployments` is null, when it could not be read: an
    unreadable fleet is never reported as empty. `backup` is the copy a
    local fleet keeps from before its last change.
    """
    from gapp.admin.sdk.fleetstore import describe_fleet
    return describe_fleet()


@_tool()
def gapp_fleet_use(target: str, create: bool = False) -> dict:
    """Move the fleet to a git repo, or back to this machine. `target` is
    `owner/repo` on GitHub, `host/path` on another git host, a git address,
    or "local". In a repo the fleet is one file, `gapp.yaml` at its root,
    and each later change is one commit to it; nothing else is put in the
    repo and no CI is set up. The fleet is carried across when the place
    it is going holds none; when both hold deployments the move is refused.
    `create` makes the repo on GitHub first, private, when it is not there.
    """
    from gapp.admin.sdk import fleetstore
    from gapp.admin.sdk.fleet import FleetRefusal
    try:
        return fleetstore.use(target, create=create)
    except FleetRefusal as e:
        return {"refused": True, "message": e.message}
    except fleetstore.FleetUnavailable as e:
        return {"refused": True, "message": str(e)}


def _fleet_tool(fn) -> dict:
    from gapp.admin.sdk import fleetstore
    from gapp.admin.sdk.fleet import FleetRefusal
    try:
        return fn(fleetstore)
    except FleetRefusal as e:
        return {"refused": True, "message": e.message}
    except fleetstore.FleetUnavailable as e:
        return {"refused": True, "message": str(e)}


@_tool()
def gapp_fleet_export() -> dict:
    """The whole fleet as one document: `format` and `deployments`, each
    deployment with its repo, project, ref, gapp version and settings. It
    is what gapp_fleet_import takes. It holds no secret values: a fleet
    names its secrets and never holds them. Nothing is written.
    """
    import yaml
    return _fleet_tool(lambda store: {"fleet": yaml.safe_load(store.export_text())})


@_tool()
def gapp_fleet_import(fleet: dict) -> dict:
    """Replace the fleet with `fleet`, all of it or none of it. `fleet` is
    a whole fleet document as gapp_fleet_export returns one (`format` and
    `deployments`); every deployment the fleet holds now and `fleet` does
    not is dropped from the fleet, so start from an export and get the
    user's yes before calling this. Every definition is checked first and
    one that is wrong refuses the whole import. The fleet it replaces is
    kept: a fleet on this machine as a backup file (`backup`), a fleet in
    a repo as the commit before this one. Touches no cloud: a deployment
    that is dropped keeps running until it is taken down in the cloud.
    """
    import yaml
    if not isinstance(fleet, dict):
        return {"refused": True, "message": "`fleet` is a fleet document: `format` and `deployments`."}
    return _fleet_tool(lambda store: store.import_text(yaml.safe_dump(fleet, sort_keys=False), "fleet"))


# --- definitions and their settings (the fleet; no cloud) ---


def _definition_tool(fn, *args, **kwargs) -> dict:
    from gapp.admin.sdk import definitions
    try:
        return getattr(definitions, fn)(*args, **kwargs)
    except definitions.Refused as e:
        return {"refused": True, "message": e.message}


@_tool()
def gapp_add(name: str | None = None, project: str | None = None, repo: str | None = None,
             ref: str | None = None, copy_of: str | None = None) -> dict:
    """Define a deployment in the fleet: its name, the repo it runs
    (`owner/repo`), its Google Cloud project, and optionally the `ref` a
    deploy builds (a branch, tag or commit; none means the default branch).
    Touches no cloud and deploys nothing. Refused when the name exists.
    With `copy_of`, the new deployment is another deployment of that one's
    repo: it takes its settings except the domain, and its project and ref
    unless given. The definition records the gapp version that wrote it.
    A refusal comes back as `{"refused": true, "message": ...}` and means
    nothing was changed; the same holds for every tool that changes a
    definition.
    """
    return _definition_tool("add", name, project=project, repo=repo, ref=ref, copy_of=copy_of)


@_tool()
def gapp_rename(old: str, new: str) -> dict:
    """Change a deployment's name. Its id, and so every cloud resource it
    has, stays as it is."""
    return _definition_tool("rename", old, new)


@_tool()
def gapp_remove(name: str) -> dict:
    """Take a deployment out of the fleet. It touches no cloud: a service,
    bucket and secrets it had deployed stay, and gapp has no tool that
    takes them down. The result names them under `left`, and holds
    the definition it removed under `removed`. A deployment added with the
    commands under `add_again` uses those resources again, data included;
    that does not bring its settings back. Confirm with the user before
    calling it."""
    return _definition_tool("remove", name)


@_tool()
def gapp_show(deployment: str | None = None) -> dict:
    """One deployment's definition (repo, project, ref, the gapp version
    that wrote it) and the settings a deploy would use, each marked as
    from the definition or a default (`effective`). `settings` is the
    definition's settings object, exactly what `gapp_apply` takes.
    `upgrade` is "available" when this gapp is newer than the
    definition. `deployment` may be left out only
    when the server runs inside a clone of the app's repo.
    """
    return _definition_tool("show", deployment)


@_tool()
def gapp_upgrade(deployment: str | None = None) -> dict:
    """Bring one deployment's definition to this gapp's version. It takes
    no version: the target is always the gapp running it. Changes the
    definition only; the next deploy applies it and must be given
    `upgrade`. Refused when there is nothing to upgrade, when the
    definition is newer than this gapp, or when it is too old for this
    gapp to upgrade from (the message names the gapp to install first).
    Check `gapp_show` first: call this only when it reports an upgrade.
    """
    return _definition_tool("upgrade", deployment)


@_tool()
def gapp_set(key: str, value: str, deployment: str | None = None) -> dict:
    """Set one value of a deployment's definition. `key` is one of: ref,
    repo, public (true/false), domain, start, memory (512Mi, 1Gi), cpu,
    max_instances. A deployment's id, project and gapp version cannot be
    set. Variables are `gapp_env_set`; extra APIs are `gapp_apis`.
    """
    return _definition_tool("set_value", key, value, deployment=deployment)


@_tool()
def gapp_unset(key: str, deployment: str | None = None) -> dict:
    """Clear one value of a deployment's definition (ref, public, domain,
    start, memory, cpu or max_instances), so its default applies again."""
    return _definition_tool("unset_value", key, deployment=deployment)


@_tool()
def gapp_apis(action: str, api: str, deployment: str | None = None) -> dict:
    """Add or remove one extra Google Cloud API the deployment's app uses
    (`action`: "add" or "remove"; `api` such as drive.googleapis.com)."""
    if action not in ("add", "remove"):
        return {"refused": True, "message": "action is 'add' or 'remove'."}
    return _definition_tool(f"apis_{action}", api, deployment=deployment)


@_tool()
def gapp_env_set(name: str, value: str | None = None, secret: str | None = None, generate: bool = False,
                 data_path: str | None = None, deployment: str | None = None) -> dict:
    """Add or replace one variable the deployment's app reads. Exactly one
    kind: a plain `value`; `secret`, the name of the secret it reads (pass
    "" for the default, the variable's name in lower case with dashes),
    with `generate` to have gapp make the value on the first deploy; or
    `data_path`, a subfolder of the deployment's data folder, which
    survives restarts (pass "" for the folder itself). A plain value is
    stored in the fleet as written, so anything sensitive goes in a
    secret. This tool never takes a secret's value: `next` names the
    command the user runs to store it.
    """
    return _definition_tool("env_set", name, value, secret=secret, generate=generate,
                            data_path=data_path, deployment=deployment)


@_tool()
def gapp_env_unset(name: str, deployment: str | None = None) -> dict:
    """Remove one variable from a deployment's definition. A secret it
    read is left in place."""
    return _definition_tool("env_unset", name, deployment=deployment)


@_tool()
def gapp_apply(settings: dict, deployment: str | None = None, dry_run: bool = False) -> dict:
    """Replace a deployment's settings with `settings`, all at once or not
    at all. `settings` is the whole settings object (public, domain,
    start, memory, cpu, max_instances, apis, env); anything it leaves out
    goes back to its default or is removed, variables included. Read the
    current settings with `gapp_show` (its `settings`) first and send
    them back with the changes made. `changes` lists every value that
    changed, with `before` and `after`. With `dry_run` true the same
    checks run and `changes` is what would change; nothing is written.
    """
    import yaml
    return _definition_tool("apply", yaml.safe_dump(settings or {}), deployment=deployment, source="settings",
                            dry_run=dry_run)


def main():
    """Run the MCP server on stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
