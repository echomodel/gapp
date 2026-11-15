"""gapp ci: deploying the fleet's deployments on GitHub Actions runners.

Everything gapp knows about GitHub Actions is in this module and in the
workflows it installs. It installs those workflows in the fleet repo,
connects Google Cloud projects to the repo's runners, keeps the list of
owners whose repos a runner may deploy, starts a deploy run and reports
on runs. It never changes the fleet: a definition is changed by the fleet
commands, as a commit.

The fleet has to be kept in a GitHub repo (`gapp fleet use <owner>/<repo>`).
What a run deploys is read from that repo's `gapp.yaml` by the run itself;
a command here reads the same definition only to refuse early what the
run would refuse.

Rules this module keeps:

- Every result carries `ok`. When it is False, `next_step` holds `action`
  and `hint`, a plain sentence with the exact command to run, and
  `refused` says which kind it is: True when the command was refused and
  nothing was started or changed (exit code 2), False when something
  failed (exit code 1).
- Something is reported missing only when GitHub's answer confirms it does
  not exist. A refused or failed read is "couldn't check", with GitHub's
  own status and message and the request that failed.
- Every GitHub call is a REST call through `gh api`. Higher-level `gh`
  subcommands (`workflow run`, `run view`, `run watch`) reach for GraphQL,
  which some sessions do not allow.
"""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from urllib.parse import quote

from gapp.admin.sdk import fleet
from gapp.admin.sdk.ci_runner import DEPLOY_LOG_PREFIX, STATUS_LOG_PREFIX
from gapp.admin.sdk.util import run_gcloud, require_gcloud_account

# The GitHub App that lets a run clone a private app repo: an Actions
# variable and an Actions secret of the fleet repo. The names are fixed,
# because the pair belongs to the fleet repo and not to any deployment.
APP_ID_VAR = "HOSTING_GITHUB_APP_ID"
APP_KEY_SECRET = "HOSTING_GITHUB_APP_PRIVATE_KEY"

# A project's deploy identity, recorded in the fleet repo as a GitHub
# Environment named after the project id, holding these two variables.
# Written by `gapp ci projects connect`, read by every run.
ENV_WIF_PROVIDER_VAR = "GAPP_WIF_PROVIDER"
ENV_DEPLOY_SA_VAR = "GAPP_DEPLOY_SERVICE_ACCOUNT"

# The workflows gapp owns in the fleet repo.
DEPLOY_WORKFLOW = "gapp-deploy.yml"
STATUS_WORKFLOW = "gapp-status.yml"
WORKFLOWS_DIR = ".github/workflows"
DEPLOY_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{DEPLOY_WORKFLOW}"
STATUS_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{STATUS_WORKFLOW}"
GAPP_WORKFLOWS = (DEPLOY_WORKFLOW, STATUS_WORKFLOW)

# A repo on GitHub, as a definition and the profile record it.
GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$")

# --- the messages a person reads, verbatim ---

FLEET_NOT_ON_GITHUB_MESSAGE = (
    "Your fleet is kept in {where}, and `gapp ci` works on a fleet kept in a GitHub repo. "
    "Move it to one: gapp fleet use <owner>/<repo>"
)
PROJECT_NOT_CONNECTED_MESSAGE = (
    "Runners in {fleet_repo} are not connected to {project}, so nothing was started. "
    "Connect them, once per project: gapp ci projects connect {project}"
)
WORKFLOW_MISSING_MESSAGE = (
    "{fleet_repo} has no {path}, so there is nothing to start. Install the workflows: gapp ci init"
)
CONNECT_REQUIREMENTS = (
    "`gapp ci projects connect <project>` needs gcloud signed in as an Owner of the "
    "project and `gh` signed in as an admin of the fleet repo (its owner, for a personal repo)."
)
UNCHECKED_VARIABLES_NOTE = (
    "not checked: the GitHub Actions variables and secrets API is not reachable "
    "from this machine; a run checks them"
)


# --- outcomes ---


def _outcome(action: str, hint: str, **extra) -> dict:
    """Something failed: what, and the exact next step."""
    result = {"ok": False, "refused": False, "next_step": {"action": action, "hint": hint}}
    result.update(extra)
    return result


def _refusal(action: str, hint: str, **extra) -> dict:
    """The command was refused and nothing was started or changed."""
    return {**_outcome(action, hint, **extra), "refused": True}


def _gh_missing_outcome() -> dict:
    return _refusal("install gh", (
        "The GitHub CLI (gh) is not installed on this machine, and every `gapp ci` command works "
        "through it. Install it, then sign in with `gh auth login`."
    ))


def _gh_available() -> bool:
    return shutil.which("gh") is not None


# --- GitHub plumbing: everything goes through the gh CLI ---


def _gh(args: list[str], payload: dict | None = None) -> tuple[int, str, str]:
    """Run `gh` with the given arguments (and a JSON body for `api`); returns (rc, stdout, stderr)."""
    cmd = ["gh"] + args
    kwargs = {"capture_output": True, "text": True}
    if payload is not None:
        cmd += ["--input", "-"]
        kwargs["input"] = json.dumps(payload)
    res = subprocess.run(cmd, **kwargs)
    return res.returncode, res.stdout or "", res.stderr or ""


def _gh_api(args: list[str], payload: dict | None = None) -> tuple[int, str, str]:
    return _gh(["api"] + args, payload)


def _gh_failure(stderr: str) -> tuple[int | None, str]:
    """GitHub's status and its own message from a failed gh call, trimmed."""
    text = (stderr or "").strip()
    first = text.splitlines()[0] if text else ""
    match = re.search(r"HTTP (\d{3})", first)
    status = int(match.group(1)) if match else None
    message = re.sub(r"\s*\(HTTP \d{3}\)", "", first)
    message = re.sub(r"^gh:\s*", "", message)
    message = re.sub(r"^HTTP \d{3}:\s*", "", message)
    message = re.sub(r"\s*\(https?://\S+\)\s*$", "", message).strip().rstrip(".")
    return status, message


def _gh_login() -> str:
    rc, out, _ = _gh_api(["user", "--jq", ".login"])
    return out.strip() if rc == 0 and out.strip() else "unknown"


def _couldnt_check(thing: str, request: str, stderr: str) -> str:
    """The only way a failed read is reported: GitHub's own answer, and which request failed."""
    status, message = _gh_failure(stderr)
    if status == 404:
        return (f"Couldn't check {thing}: GitHub returned 404 Not Found: it doesn't exist, or your "
                f"GitHub login ({_gh_login()}) can't see it. Request: {request}")
    if status is not None:
        return f"Couldn't check {thing}: GitHub returned {status}: {message}. Request: {request}"
    return f"Couldn't check {thing}: gh said: {message or 'nothing'}. Request: {request}"


def _refused(stderr: str) -> str:
    """A failed write, in GitHub's words."""
    status, message = _gh_failure(stderr)
    return f"GitHub returned {status}: {message}" if status else (message or "gh gave no answer")


def _is_auth_failure(stderr: str) -> bool:
    """Whether gh failed for want of a usable login (401, or not signed in), not for another refusal."""
    status, message = _gh_failure(stderr)
    lowered = (stderr or "").lower()
    return status == 401 or "gh auth login" in lowered or "not logged" in lowered or "authentication required" in lowered


def _fix_for(stderr: str, need: str) -> str:
    """The fix for a refused call: sign in only when the refusal was about the login; else name what's needed."""
    if _is_auth_failure(stderr):
        return f"Sign in with `gh auth login` as an account that has {need}, then"
    return f"GitHub refused the request itself, so check that the signed-in account has {need} and that this machine's GitHub access allows it; then"


@dataclass
class Read:
    """A read of something in a GitHub repo.

    `state` is "present", "absent" (GitHub confirmed it does not exist) or
    "unchecked" (the read failed; `note` says how, in GitHub's words).
    """
    state: str
    sha: str | None = None
    content: str | None = None
    data: object = None
    note: str | None = None

    @property
    def present(self) -> bool:
        return self.state == "present"

    @property
    def absent(self) -> bool:
        return self.state == "absent"

    @property
    def unchecked(self) -> bool:
        return self.state == "unchecked"


def _repo_visible(repo: str) -> bool:
    rc, _, _ = _gh_api([f"repos/{repo}"])
    return rc == 0


def _read_path(repo: str, path: str, thing: str, ref: str | None = None) -> Read:
    """A file or directory of a repo through the Contents API, at `ref`
    (a commit, tag or branch) or else the default branch.

    A 404 counts as absent only when the repo itself answers, so a repo the
    login cannot see is never reported as a missing file.
    """
    endpoint = f"repos/{repo}/contents" + (f"/{path}" if path else "") + (f"?ref={ref}" if ref else "")
    request = f"GET {endpoint}"
    rc, out, err = _gh_api([endpoint])
    if rc != 0:
        status, _ = _gh_failure(err)
        if status == 404 and _repo_visible(repo):
            return Read("absent")
        return Read("unchecked", note=_couldnt_check(thing, request, err))
    try:
        data = json.loads(out) if out.strip() else None
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        content = base64.b64decode(data.get("content", "") or "").decode()
        return Read("present", sha=data.get("sha"), content=content, data=data)
    if isinstance(data, list):
        return Read("present", data=data)
    return Read("unchecked", note=_couldnt_check(thing, request, "GitHub returned an answer gapp did not recognise"))


def _read_file(repo: str, path: str, ref: str | None = None) -> Read:
    return _read_path(repo, path, f"{path} in {repo}", ref)


def _write_file(repo: str, path: str, content: str, message: str) -> str:
    """Create or update a gapp-owned file in the fleet repo via the Contents API.

    A workflow is a file gapp generates, so it is written through GitHub's
    API, which makes the commit itself. An identical file is "unchanged".
    Returns "created", "updated" or "unchanged"; raises RuntimeError with
    GitHub's words when the write is refused or the current file unreadable.
    """
    current = _read_file(repo, path)
    if current.unchecked:
        raise RuntimeError(current.note)
    if current.present and current.content == content:
        return "unchanged"
    payload = {"message": message, "content": base64.b64encode(content.encode()).decode()}
    if current.sha:
        payload["sha"] = current.sha
    rc, _, err = _gh_api(["-X", "PUT", f"repos/{repo}/contents/{path}"], payload)
    if rc != 0:
        raise RuntimeError(f"{path} in {repo} could not be written: {_refused(err)}")
    return "updated" if current.sha else "created"


def _repo_permission(fleet_repo: str) -> tuple[bool | None, str]:
    """Whether the gh login is an admin of the fleet repo: (admin, why unknown)."""
    rc, out, err = _gh_api([f"repos/{fleet_repo}", "--jq", ".permissions.admin"])
    if rc != 0:
        return None, _couldnt_check(f"your permission on {fleet_repo}", f"GET repos/{fleet_repo}", err)
    value = out.strip().lower()
    if value in ("true", "false"):
        return value == "true", ""
    return None, f"GitHub did not report this login's permission on {fleet_repo}"


# --- Actions variables and secrets (presence only; GitHub never returns a secret) ---


def _actions_api_available(fleet_repo: str) -> bool:
    """Can this machine read the fleet repo's Actions variables and secrets?

    Some sessions reach GitHub through a proxy that refuses the Actions
    variables and secrets API while allowing contents reads and workflow
    dispatch. Probed once, up front, so a check this machine cannot
    perform is reported as not checked rather than as missing.
    """
    rc, _, _ = _gh_api([f"repos/{fleet_repo}/actions/variables?per_page=1"])
    return rc == 0


def _actions_item_present(fleet_repo: str, kind: str, name: str) -> bool | None:
    """Whether an Actions variable or secret exists: True, False (GitHub says 404
    on a repo whose variables it listed), or None when the read failed."""
    rc, _, err = _gh_api([f"repos/{fleet_repo}/actions/{kind}/{name}"])
    if rc == 0:
        return True
    status, _ = _gh_failure(err)
    return False if status == 404 else None


def _variables_report(fleet_repo: str) -> dict:
    """Presence of the two operator-set values in the fleet repo.

    `variables` maps each name to True, False or None (not checked). Never
    sets them: the values are the operator's.
    """
    names = {APP_ID_VAR: "variables", APP_KEY_SECRET: "secrets"}
    if not _actions_api_available(fleet_repo):
        return {"variables": {n: None for n in names}, "checked": False, "note": UNCHECKED_VARIABLES_NOTE}
    return {"variables": {n: _actions_item_present(fleet_repo, kind, n) for n, kind in names.items()},
            "checked": True, "note": None}


# --- the per-project Environment record ---


def _environment(fleet_repo: str, project_id: str) -> Read:
    """Whether the fleet repo has a GitHub Environment named after the project.

    The repo environments endpoint is readable wherever the repo itself is,
    unlike the Actions variables API.
    """
    request = f"GET repos/{fleet_repo}/environments/{project_id}"
    rc, out, err = _gh_api([f"repos/{fleet_repo}/environments/{project_id}"])
    if rc == 0 and out.strip():
        return Read("present")
    status, _ = _gh_failure(err)
    if status == 404 and _repo_visible(fleet_repo):
        return Read("absent")
    return Read("unchecked", note=_couldnt_check(f"the Environment '{project_id}' of {fleet_repo}", request, err))


def _write_environment_record(fleet_repo: str, project_id: str, wif_provider: str, service_account: str) -> str:
    """Record a project's deploy identity in the fleet repo: the Environment
    and its two variables, each written only when its value differs."""
    rc, _, err = _gh_api(["-X", "PUT", f"repos/{fleet_repo}/environments/{project_id}"])
    if rc != 0:
        raise RuntimeError(f"create the Environment '{project_id}': {_refused(err)}")
    status = "unchanged"
    for var, value in ((ENV_WIF_PROVIDER_VAR, wif_provider), (ENV_DEPLOY_SA_VAR, service_account)):
        endpoint = f"repos/{fleet_repo}/environments/{project_id}/variables"
        rc, out, _ = _gh_api([f"{endpoint}/{var}"])
        if rc == 0 and out.strip():
            if (json.loads(out).get("value") or "").strip() == value:
                continue
            rc, _, err = _gh_api(["-X", "PATCH", f"{endpoint}/{var}"], {"name": var, "value": value})
            status = "updated" if status != "created" else status
        else:
            rc, _, err = _gh_api(["-X", "POST", endpoint], {"name": var, "value": value})
            status = "created"
        if rc != 0:
            raise RuntimeError(f"write the Environment variable {var} on '{project_id}': {_refused(err)}")
    return status


# --- the workflows gapp owns in the fleet repo ---


def _render_template(name: str, gapp_repo: str, gapp_ref: str) -> str:
    text = resources.files("gapp").joinpath("templates", "ci", name).read_text()
    import gapp
    return (text.replace("__GAPP_REPO__", gapp_repo).replace("__GAPP_REF__", gapp_ref)
            .replace("__GAPP_COMPATIBLE_FROM__", gapp.COMPATIBLE_FROM).replace("__GAPP_VERSION__", gapp.__version__))


def _workflow_files(gapp_repo: str, gapp_ref: str) -> dict[str, str]:
    """Path -> content of the workflows `gapp ci init` installs."""
    return {f"{WORKFLOWS_DIR}/{name}": _render_template(name, gapp_repo, gapp_ref) for name in GAPP_WORKFLOWS}


def _install_workflows(fleet_repo: str) -> dict[str, str]:
    """Install the workflows in the fleet repo, or bring them up to this gapp's.

    A file is written only when its rendered content differs from what the
    repo holds. Returns path -> "created" | "updated" | "unchanged".
    """
    gapp_repo = _get_gapp_repo()
    gapp_ref = _resolve_gapp_ref(gapp_repo)
    return {path: _write_file(fleet_repo, path, content, f"Install gapp workflow {path.rsplit('/', 1)[-1]}")
            for path, content in _workflow_files(gapp_repo, gapp_ref).items()}


# --- the fleet repo, and the definition a command means ---


def _fleet_repo(result: dict) -> tuple[str | None, dict | None]:
    """The GitHub repo the fleet is kept in, or the refusal to return.

    `gapp ci` runs workflows in the fleet repo, so the fleet has to be in
    one, and on GitHub. Where the fleet lives is read from the one place
    that says (`fleetstore.location`); nothing is searched for.
    """
    from gapp.admin.sdk import fleetstore
    if not _gh_available():
        return None, {**result, **_gh_missing_outcome()}
    where = fleetstore.location()
    if where.kind != "github" or not GITHUB_REPO_RE.match(where.repo or ""):
        kept = where.describe() if where.kind != "github" else f"{where.repo}, which is not a repo on GitHub"
        return None, {**result, **_refusal("fleet use", FLEET_NOT_ON_GITHUB_MESSAGE.format(where=kept))}
    result["fleet_repo"] = where.repo
    return where.repo, None


def _definition(result: dict, deployment: str | None, cwd: Path | None, command: str) -> tuple[str | None, dict | None, dict | None]:
    """The deployment a command means and its definition, read from the
    fleet: (name, definition, None), or (None, None, the refusal)."""
    from gapp.admin.sdk import definitions, fleetstore
    from gapp.admin.sdk.lookup import LookupRefusal
    try:
        deployments = fleetstore.read()
        name = definitions.which(deployments, deployment, cwd, command)
    except definitions.Refused as e:
        return None, None, {**result, **_refusal("name the deployment", e.message)}
    except (LookupRefusal, fleetstore.FleetUnavailable) as e:
        return None, None, {**result, **_refusal("name the deployment", str(e))}
    definition = deployments[name]
    result.update(deployment=name, id=definition.get("id"), repo=definition.get("repo"),
                  project=definition.get("project"), ref=definition.get("ref") or None, gapp=definition.get("gapp"))
    return name, definition, None


def _workflow_stop(fleet_repo: str, path: str) -> dict | None:
    """The outcome when a workflow a command starts is not in the fleet repo, or could not be checked."""
    workflow = _read_file(fleet_repo, path)
    if workflow.absent:
        return _refusal("ci init", WORKFLOW_MISSING_MESSAGE.format(fleet_repo=fleet_repo, path=path))
    if workflow.unchecked:
        return _outcome("check GitHub access", workflow.note)
    return None


def _handoff_stop(fleet_repo: str, name: str, version: str | None) -> dict | None:
    """The refusal when the workflows in the fleet repo and the gapp the
    definition names do not work together, or None.

    The workflows say the oldest definition they run (`minimum=`, their
    gapp's COMPATIBLE_FROM) and are their gapp's version (the tag their
    deploy workflow calls). A definition on this gapp's version also needs
    workflows from this gapp's COMPATIBLE_FROM on. Anything that cannot be
    read here is left to the run, which checks both sides itself.
    """
    import gapp
    from gapp.admin.sdk.entryfile import EntryFileRefusal, version_key
    read = _read_file(fleet_repo, DEPLOY_WORKFLOW_PATH)
    text = (read.content or "") if read.present else ""
    minimum = re.search(r'minimum="([^"]+)"', text)
    tag = re.search(r"/deploy\.yml@v(\S+)", text)
    try:
        defined = version_key(version)
        if minimum and defined < version_key(minimum.group(1)):
            return _refusal("upgrade", (
                f"'{name}' is defined with gapp {version}, and the workflows in {fleet_repo} work with "
                f"{minimum.group(1)} or later. Upgrade it first: gapp upgrade --deployment {name}, then "
                f"gapp ci deploy --deployment {name} --upgrade"))
        if (tag and version == gapp.__version__
                and version_key(tag.group(1)) < version_key(gapp.COMPATIBLE_FROM)):
            return _refusal("ci init", (
                f"'{name}' is defined with gapp {version}, and the workflows in {fleet_repo} are gapp "
                f"{tag.group(1)}'s, older than it works with ({gapp.COMPATIBLE_FROM} or later). "
                f"Bring them up to date: gapp ci init"))
    except EntryFileRefusal:
        return None
    return None


def _project_stop(fleet_repo: str, project: str) -> dict | None:
    """The refusal when GitHub says the fleet repo's runners are not connected to a project.

    Only a confirmed absence stops a run here. When the Environment cannot
    be read (a token or a proxy that does not allow that API, as in an agent
    session with GitHub access only), the run is started: the runner itself
    refuses, before signing in or changing anything, when the project's
    deploy identity is missing, with the same `gapp ci projects connect`.
    """
    env = _environment(fleet_repo, project)
    if env.absent:
        return _refusal("ci projects connect", PROJECT_NOT_CONNECTED_MESSAGE.format(fleet_repo=fleet_repo, project=project))
    return None


# --- ci init ---


def init_workflows() -> dict:
    """Install the deploy and status workflows in the fleet repo (`gapp ci init`).

    Running it again brings the files up to the installed gapp's. It
    writes those two files and nothing else: the fleet file and the
    setting that says where the fleet lives belong to `gapp fleet use`.
    """
    result = {"ok": True, "fleet_repo": None, "files": None, "variables": None, "variables_note": None,
              "next_step": None, "lines": []}
    fleet_repo, stop = _fleet_repo(result)
    if stop:
        return stop
    try:
        result["files"] = _install_workflows(fleet_repo)
    except RuntimeError as e:
        return {**result, **_outcome("install the workflows", (
            f"The workflows could not be installed in {fleet_repo}: {str(e).rstrip('.')}. `gapp ci init` needs "
            f"`gh` signed in as an admin of {fleet_repo}. Then run it again: gapp ci init"
        ))}
    statuses = set(result["files"].values())
    if "created" in statuses:
        result["lines"].append(f"Installed the deploy and status workflows in {fleet_repo}.")
    elif "updated" in statuses:
        result["lines"].append(f"Updated the workflows in {fleet_repo} to this gapp's.")
    else:
        result["lines"].append(f"The workflows in {fleet_repo} are this gapp's already. Nothing changed.")
    report = _variables_report(fleet_repo)
    result["variables"], result["variables_note"] = report["variables"], report["note"]
    result["next_step"] = {"action": "ci projects connect", "hint": "gapp ci projects connect <project>"}
    return result


# --- ci projects: which projects the fleet repo's runners can deploy to ---


def _gcloud_outcome(step: str, project_id: str, detail: str) -> dict:
    """A gcloud failure in words, with what the command needs restated."""
    text = (detail or "").strip()
    first = text.splitlines()[0] if text else ""
    lowered = text.lower()
    again = f"gapp ci projects connect {project_id}"
    if "unrecognized arguments" in lowered or "invalid choice" in lowered:
        # gcloud rejected the command itself: nothing about the sign-in is wrong.
        return _outcome("report the gcloud error", (
            f"Could not {step} for project {project_id}: gcloud rejected the command gapp sent ({first}). "
            f"This is a gapp defect, not a sign-in or permission problem; report it with this message."
        ))
    if "unauthenticated" in lowered or "credential" in lowered or "login" in lowered:
        why = "gcloud has no working sign-in on this machine"
    elif "permission" in lowered or "403" in lowered or "forbidden" in lowered:
        why = f"the signed-in gcloud account is not an Owner of {project_id} (permission denied)"
    elif "not found" in lowered or "404" in lowered:
        why = f"project {project_id} was not found by the signed-in gcloud account"
    else:
        return _outcome("check the error", (
            f"Could not {step} for project {project_id}: {first or 'gcloud failed'}. That is the error "
            f"itself, not a sign-in problem. Fix what it names, then run it again: {again}"
        ))
    return _outcome("sign in to gcloud as an Owner", (
        f"Could not {step} for project {project_id}: {why}. {CONNECT_REQUIREMENTS} "
        f"Sign in with `gcloud auth login` (or point gapp at the right account with "
        f"`gapp config account <email>`), then run it again: {again}"
    ))


def _project_is_set_up(project_id: str) -> bool:
    """Whether the project carries the mark `gapp project setup` leaves."""
    from gapp.admin.sdk.cloud import get_provider
    from gapp.admin.sdk.cloud.base import CloudAccessError
    from gapp.admin.sdk.core import HOSTED_LABEL, HOSTED_VALUE
    try:
        return (get_provider().get_project_labels(project_id) or {}).get(HOSTED_LABEL) == HOSTED_VALUE
    except CloudAccessError as e:
        raise RuntimeError(str(e).splitlines()[0]) from e


def connect_project(project: str) -> dict:
    """Let the fleet repo's runners sign in to a project and deploy to it (`gapp ci projects connect`).

    Once per project; running it again only adds what is missing. In the
    project: the APIs a sign-in from GitHub Actions uses, the Workload
    Identity pool and provider, the `gapp-deploy` service account with its
    roles, and the binding that lets runs of the fleet repo act as it. In
    the fleet repo: the GitHub Environment named after the project, holding
    the two variables a run signs in with.

    It prepares nothing a deployment itself needs. That is `gapp project
    setup`, and a project without its mark is refused here. Needs gcloud
    signed in as an Owner of the project and `gh` signed in as an admin of
    the fleet repo.
    """
    project_id = (project or "").strip()
    result = {"ok": True, "project": project_id, "fleet_repo": None, "apis": None, "wif_pool": None,
              "wif_provider": None, "service_account": None, "binding": None, "environment": project_id,
              "environment_record": None, "changed": None, "next_step": None, "lines": []}
    try:
        project_id = fleet.validate_project(project_id)
    except fleet.FleetRefusal as e:
        return {**result, **_refusal("pass the project", f"{e.message} Name the project: gapp ci projects connect <project>")}
    fleet_repo, stop = _fleet_repo(result)
    if stop:
        return stop
    again = f"gapp ci projects connect {project_id}"

    admin, reason = _repo_permission(fleet_repo)
    if admin is not True:
        why = reason if admin is None else f"the signed-in `gh` account is not an admin of {fleet_repo}"
        return {**result, **_refusal("sign in to gh as an admin", (
            f"The project's deploy identity cannot be recorded in {fleet_repo}: {why}. {CONNECT_REQUIREMENTS} "
            f"Sign in with `gh auth login` as the repo's admin, then run it again: {again}"
        ))}
    if shutil.which("gcloud") is None:
        return {**result, **_refusal("install gcloud", (
            f"gcloud is not installed on this machine. {CONNECT_REQUIREMENTS} Run it on a machine that has "
            f"gcloud signed in as an Owner of {project_id}: {again}"
        ))}
    try:
        require_gcloud_account()
    except RuntimeError as e:
        return {**result, **_refusal("sign in to gcloud as an Owner", f"{e} {CONNECT_REQUIREMENTS}")}
    try:
        set_up = _project_is_set_up(project_id)
    except RuntimeError as e:
        return {**result, **_gcloud_outcome("read the project's labels", project_id, str(e))}
    if not set_up:
        return {**result, **_refusal("project setup", (
            f"{project_id} is not set up for gapp, so runners were not connected to it. Set it up, once per "
            f"project: gapp project setup {project_id}. Then run this again: {again}"
        ))}

    steps = (
        ("apis", "enable the APIs a sign-in from GitHub Actions uses", lambda: _enable_sign_in_apis(project_id)),
        ("wif_pool", "create or confirm the Workload Identity pool", lambda: _ensure_wif_pool(project_id)),
        ("wif_provider", "create or confirm the Workload Identity provider",
         lambda: _ensure_wif_provider(project_id, fleet_repo.split("/")[0])),
        ("service_account", "create or confirm the gapp-deploy service account", lambda: _ensure_deploy_sa(project_id)),
        ("binding", "bind the Workload Identity pool to the fleet repo", lambda: _ensure_wif_binding(project_id, fleet_repo)),
    )
    for key, step, fn in steps:
        try:
            result[key] = fn()
        except RuntimeError as e:
            return {**result, **_gcloud_outcome(step, project_id, str(e))}
    try:
        project_number = _get_project_number(project_id)
    except RuntimeError as e:
        return {**result, **_gcloud_outcome("read the project number", project_id, str(e))}
    try:
        result["environment_record"] = _write_environment_record(
            fleet_repo, project_id, _wif_provider_path(project_number), _deploy_sa_email(project_id),
        )
    except RuntimeError as e:
        return {**result, **_outcome("sign in to gh as an admin", (
            f"The project is ready, but its deploy identity could not be recorded in {fleet_repo}: could not {e}. "
            f"{CONNECT_REQUIREMENTS} Then run it again; it only adds what is missing: {again}"
        ))}
    result["changed"] = any(result[k] == "created" for k in ("wif_pool", "wif_provider", "service_account")) \
        or result["environment_record"] != "unchanged"
    result["lines"].append(
        f"Runners in {fleet_repo} can deploy to {project_id}." if result["changed"]
        else f"Runners in {fleet_repo} could already deploy to {project_id}. Nothing changed.")
    result["next_step"] = {"action": "ci deploy", "hint": "gapp ci deploy --deployment <name>"}
    return result


def list_projects() -> dict:
    """The projects the fleet repo's runners can deploy to (`gapp ci projects list`).

    Read from the fleet repo's record alone: its GitHub Environments that
    hold the deploy identity `gapp ci projects connect` writes.
    Google Cloud is not asked. `unchecked` names the Environments whose variables
    this machine's GitHub access could not read.
    """
    result = {"ok": True, "fleet_repo": None, "projects": [], "unchecked": [], "next_step": None, "lines": []}
    fleet_repo, stop = _fleet_repo(result)
    if stop:
        return stop
    request = f"GET repos/{fleet_repo}/environments"
    rc, out, err = _gh_api([f"repos/{fleet_repo}/environments?per_page=100"])
    if rc != 0:
        return {**result, **_outcome("check GitHub access", _couldnt_check(f"the Environments of {fleet_repo}", request, err))}
    try:
        names = sorted(str(e["name"]) for e in (json.loads(out or "{}").get("environments") or []))
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return {**result, **_outcome("check GitHub access", _couldnt_check(
            f"the Environments of {fleet_repo}", request, "GitHub returned an answer gapp did not recognise"))}
    for name in names:
        rc, out, err = _gh_api([f"repos/{fleet_repo}/environments/{quote(name, safe='')}/variables/{ENV_WIF_PROVIDER_VAR}"])
        if rc == 0 and out.strip():
            result["projects"].append(name)
        elif _gh_failure(err)[0] != 404:
            result["unchecked"].append(name)
    if result["projects"]:
        result["lines"].append(f"Runners in {fleet_repo} can deploy to:")
        result["lines"] += [f"  {name}" for name in result["projects"]]
    else:
        result["lines"].append(f"Runners in {fleet_repo} are not connected to any project.")
        result["next_step"] = {"action": "ci projects connect", "hint": "gapp ci projects connect <project>"}
    if result["unchecked"]:
        result["lines"].append(
            f"Couldn't check {', '.join(result['unchecked'])}: the variables of those Environments are not "
            f"readable with this machine's GitHub access.")
    return result


# --- workflow runs ---


def _default_branch(repo: str) -> tuple[str | None, str]:
    """The repo's default branch (REST), or (None, stderr)."""
    rc, out, err = _gh_api([f"repos/{repo}", "--jq", ".default_branch"])
    branch = out.strip()
    return (branch, "") if rc == 0 and branch and branch != "null" else (None, err or "GitHub did not report a default branch")


# How far a runner's clock may lag this machine's when matching a run to its
# dispatch, and how long to wait for GitHub to list the new run.
RUN_CLOCK_SKEW = timedelta(seconds=120)
RUN_APPEAR_TIMEOUT = 120


@dataclass
class Dispatch:
    """One workflow dispatch: the id its run carries in its run name, and when it was sent."""
    request_id: str
    sent_at: datetime
    refused: str | None = None
    stderr: str = ""


def _dispatch(fleet_repo: str, workflow_file: str, fields: dict[str, str]) -> Dispatch:
    """Dispatch a workflow on the fleet repo's default branch (REST).

    Every dispatch carries a fresh `request_id` input, which each fleet
    workflow puts at the end of its run name, so the run is found by that id
    (`_find_run`), never as "the latest run". `refused` is GitHub's refusal
    in words; `stderr` is kept for choosing the fix.
    """
    request_id = uuid.uuid4().hex[:12]
    sent_at = datetime.now(timezone.utc)
    branch, err = _default_branch(fleet_repo)
    if branch is None:
        return Dispatch(request_id, sent_at, f"its default branch could not be read: {_refused(err)}", err)
    rc, _, err = _gh_api(["-X", "POST", f"repos/{fleet_repo}/actions/workflows/{workflow_file}/dispatches"],
                         {"ref": branch, "inputs": {**fields, "request_id": request_id}})
    if rc == 0:
        return Dispatch(request_id, sent_at)
    said = _refused(err)
    if "unexpected inputs" in (err or "").lower():
        said += (f" (the fleet repo's {workflow_file} does not take the inputs this gapp sends; "
                 f"bring the workflows up to this gapp's with `gapp ci init`)")
    return Dispatch(request_id, sent_at, said, err)


def _parse_time(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _find_run(fleet_repo: str, workflow_file: str, dispatch: Dispatch, timeout: int | None = None) -> str | None:
    """The run that this dispatch created, by the request id in its run name.

    Polls the workflow's dispatch runs (REST) until one created after the
    dispatch (less the clock skew) carries the id, or the timeout passes.
    An older run is never accepted, whatever its name.
    """
    earliest = dispatch.sent_at - RUN_CLOCK_SKEW
    deadline = time.monotonic() + (RUN_APPEAR_TIMEOUT if timeout is None else timeout)
    while True:
        rc, out, _ = _gh_api([f"repos/{fleet_repo}/actions/workflows/{workflow_file}/runs?event=workflow_dispatch&per_page=30"])
        if rc == 0:
            try:
                runs = json.loads(out or "{}").get("workflow_runs") or []
            except (json.JSONDecodeError, AttributeError):
                runs = []
            for run in runs:
                title = run.get("display_title") or run.get("name") or ""
                created = _parse_time(run.get("created_at"))
                if title.split()[-1:] == [dispatch.request_id] and created is not None and created >= earliest:
                    return str(run.get("id"))
        if time.monotonic() >= deadline:
            return None
        time.sleep(3)


def _wait_for_run(fleet_repo: str, run_id: str, timeout: int = 900) -> tuple[str | None, str | None]:
    """Poll a run until it completes; returns (status, conclusion)."""
    deadline = time.monotonic() + timeout
    status = conclusion = None
    while True:
        rc, out, _ = _gh_api([f"repos/{fleet_repo}/actions/runs/{run_id}", "--jq", ".status + \" \" + (.conclusion // \"\")"])
        if rc == 0 and out.strip():
            status, _, conclusion = out.strip().partition(" ")
            conclusion = conclusion or None
            if status == "completed":
                return status, conclusion
        if time.monotonic() >= deadline:
            return status, conclusion
        time.sleep(5)


# A line a step printed, as GitHub stores it in a job log: an ISO timestamp,
# one space, then the text. GitHub also echoes each `run:` script into the
# log (under "##[group]Run ...", lines starting with a colour code or with
# spaces), so a message counts only when the printed text itself starts
# with the prefix.
_LOG_LINE = re.compile(r"^\ufeff?\d{4}-\d{2}-\d{2}T[\d:.]+Z (?P<text>.*)$")


def _emitted_messages(log_text: str, prefix: str) -> list[str]:
    """The messages a run's steps printed with `prefix`, never the echoed script."""
    messages = []
    for line in (log_text or "").splitlines():
        match = _LOG_LINE.match(line)
        text = match.group("text") if match else line
        if text.startswith(prefix):
            messages.append(text[len(prefix):].strip())
    return messages


def _run_log(fleet_repo: str, run_id: str) -> str:
    """The text of every job log of a run (REST): per job, then the run's zip as a fallback."""
    rc, out, _ = _gh_api([f"repos/{fleet_repo}/actions/runs/{run_id}/jobs", "--jq", ".jobs[].id"])
    texts = []
    if rc == 0:
        for job_id in out.split():
            rc, log, _ = _gh_api([f"repos/{fleet_repo}/actions/jobs/{job_id}/logs"])
            if rc == 0:
                texts.append(log)
    if texts:
        return "\n".join(texts)
    res = subprocess.run(["gh", "api", f"repos/{fleet_repo}/actions/runs/{run_id}/logs"], capture_output=True)
    if res.returncode != 0 or not res.stdout:
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(res.stdout)) as archive:
            return "\n".join(archive.read(n).decode("utf-8", "replace") for n in archive.namelist() if "/" not in n)
    except zipfile.BadZipFile:
        return ""


def _run_url(fleet_repo: str, run_id: str | None) -> str | None:
    return f"https://github.com/{fleet_repo}/actions/runs/{run_id}" if run_id else None



def _last_deploy(fleet_repo: str, name: str) -> dict:
    """The newest run of the deploy workflow for a deployment, found by its
    run name, "Deploy <name> <request id>". `checked` is False, with a
    note, when the runs cannot be read."""
    request = f"GET repos/{fleet_repo}/actions/workflows/{DEPLOY_WORKFLOW}/runs"
    rc, out, err = _gh_api([f"repos/{fleet_repo}/actions/workflows/{DEPLOY_WORKFLOW}/runs?per_page=50"])
    if rc != 0:
        return {"checked": False, "run": None, "note": _couldnt_check(f"the deploy runs of {fleet_repo}", request, err)}
    try:
        runs = json.loads(out).get("workflow_runs") or []
    except (json.JSONDecodeError, AttributeError):
        runs = []
    pattern = re.compile(rf"^Deploy {re.escape(name)}(?: \S+)?\s*$")
    for run in runs:
        if pattern.match(run.get("display_title") or run.get("name") or ""):
            return {"checked": True, "note": None, "run": {
                "id": str(run.get("id")), "status": run.get("status"), "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"),
                "url": run.get("html_url") or _run_url(fleet_repo, run.get("id")),
            }}
    return {"checked": True, "run": None, "note": None}


@dataclass
class RunReport:
    """What a finished run said in its log.

    `result`, `refused` and `error` are the one line gapp prints on the
    runner (`ci_runner`). `said` holds the other lines a workflow printed
    for a person, and `errors` the errors GitHub Actions itself logged.
    """
    result: dict | None = None
    refused: str | None = None
    error: str | None = None
    said: list = None
    errors: list = None
    log: str = ""

    def words(self) -> str:
        """Why the run failed, in the run's own words, or "" when it gave none."""
        return " ".join(self.said or self.errors or [])


def _run_report(fleet_repo: str, run_id: str, prefix: str, name: str) -> RunReport:
    log = _run_log(fleet_repo, run_id)
    report = RunReport(said=[], errors=_emitted_messages(log, "##[error]"), log=log)
    for message in _emitted_messages(log, prefix):
        if message.startswith("result="):
            try:
                report.result = json.loads(message[len("result="):])
            except json.JSONDecodeError:
                report.result = None
        elif message.startswith("refused="):
            report.refused = message[len("refused="):]
        elif message.startswith("error="):
            report.error = message[len("error="):]
        elif not message.startswith(f"{name} -> "):       # the line that says what the run is about to deploy
            report.said.append(message)
    return report


def _start_run(fleet_repo: str, workflow: str, inputs: dict, result: dict, doing: str, again: str) -> dict | None:
    """Dispatch a workflow and find its run, filling `result`. Returns the
    outcome to stop with, or None when the run was found."""
    dispatch = _dispatch(fleet_repo, workflow, inputs)
    if dispatch.refused:
        return {**result, **_outcome(f"start the {doing} run", (
            f"The {doing} run could not be started in {fleet_repo}: {dispatch.refused}. "
            f"{_fix_for(dispatch.stderr, f'access to start workflows in {fleet_repo}')} run it again: {again}"
        ))}
    result["dispatched"] = True
    result["request_id"] = dispatch.request_id
    result["run_id"] = _find_run(fleet_repo, workflow, dispatch)
    result["run_url"] = _run_url(fleet_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Started {workflow} in {fleet_repo} for '{result['deployment']}', but no run carrying its request id "
            f"{dispatch.request_id} appeared within {RUN_APPEAR_TIMEOUT} seconds, so nothing is reported about it "
            f"(another run's result never is). Look for it on the repo's Actions tab, then: "
            f"gapp ci status --deployment {result['deployment']}"
        ))}
    return None


# --- ci deploy ---


def _builds(ref: str | None) -> str:
    return ref or "the default branch"


def _ref_is_missing(log: str, repo: str, ref: str | None) -> bool:
    """Whether a failed run failed because the app repo has no such ref:
    the checkout said so, or GitHub confirms it for a repo this login sees."""
    if not ref:
        return False
    if re.search(r"A branch or tag with the name '[^']*' could not be found|couldn't find remote ref|not our ref", log or ""):
        return True
    rc, _, err = _gh_api([f"repos/{repo}/commits/{ref}", "--jq", ".sha"])
    return rc != 0 and _gh_failure(err)[0] in (404, 422) and _repo_visible(repo)


def deploy(deployment: str | None = None, *, upgrade: bool = False, watch: bool = True,
           cwd: Path | None = None, progress=None) -> dict:
    """Deploy one deployment on a runner (`gapp ci deploy`): the same deploy `gapp deploy` does, from GitHub Actions.

    The run is started with the deployment's name and nothing else. It
    reads the definition from the fleet repo, checks out the definition's
    ref of its repo, installs the gapp version the definition names and
    deploys. This command reads the same definition first, to refuse
    before a run is started what the run would refuse: a definition with
    no repo on GitHub, a project the runners are not connected to, and
    workflows that are not installed. `upgrade` applies a pending gapp
    upgrade and is refused by the run when none is pending.

    With `watch`, it waits for the run and reports what the run said.
    `progress`, when given, is called with one line just before the run
    is started.
    """
    result = {"ok": True, "deployment": (deployment or "").strip() or None, "id": None, "repo": None, "project": None,
              "ref": None, "gapp": None, "upgrade": bool(upgrade), "fleet_repo": None, "workflow": DEPLOY_WORKFLOW,
              "dispatched": False, "request_id": None, "run_id": None, "run_url": None, "watched": False,
              "conclusion": None, "deployed": None, "messages": [], "next_step": None, "lines": []}
    fleet_repo, stop = _fleet_repo(result)
    if stop:
        return stop
    name, _, stop = _definition(result, result["deployment"], cwd, "gapp ci deploy")
    if stop:
        return stop
    repo, project, ref = result["repo"], result["project"], result["ref"]
    from_clone = f"Deploy it from a clone of its repo: gapp deploy --deployment {name}"
    if not repo:
        return {**result, **_refusal("deploy from a clone", (
            f"'{name}' has no repo, so a runner has nothing to check out. {from_clone}"
        ))}
    if not GITHUB_REPO_RE.match(repo):
        return {**result, **_refusal("deploy from a clone", (
            f"'{name}' runs {repo}, which is not a repo on GitHub, and a runner checks out GitHub repos. {from_clone}"
        ))}
    stop = (_project_stop(fleet_repo, project) or _workflow_stop(fleet_repo, DEPLOY_WORKFLOW_PATH)
            or _handoff_stop(fleet_repo, name, result["gapp"]))
    if stop:
        return {**result, **stop}

    again = f"gapp ci deploy --deployment {name}" + (" --upgrade" if upgrade else "")
    if progress:
        progress(f"Deploying {name}: {_builds(ref)} of {repo}, to {project}.")
    inputs = {"deployment": name, "upgrade": "true" if upgrade else "false"}
    stop = _start_run(fleet_repo, DEPLOY_WORKFLOW, inputs, result, "deploy", again)
    if stop:
        return stop
    status_hint = {"action": "ci status", "hint": f"gapp ci status --deployment {name}"}
    if not watch:
        result["lines"].append(f"Started a deploy of {name} ({_builds(ref)} of {repo}, to {project}): {result['run_url']}")
        result["next_step"] = status_hint
        return result

    status, conclusion = _wait_for_run(fleet_repo, result["run_id"], timeout=3600)
    result["watched"] = True
    result["conclusion"] = conclusion
    url = result["run_url"]
    if status != "completed":
        return {**result, **_outcome("check the run", (
            f"The deploy run for '{name}' did not finish within the wait: {url}. See how it ended: "
            f"gapp ci status --deployment {name}"
        ))}
    report = _run_report(fleet_repo, result["run_id"], DEPLOY_LOG_PREFIX, name)
    result["messages"] = report.said
    result["deployed"] = report.result
    if report.refused:
        # The runner speaks of the deploy it ran; the way to run it again is this command.
        why = report.refused.replace("gapp deploy --deployment", "gapp ci deploy --deployment")
        return {**result, **_refusal("fix and deploy again", (
            f"The run refused to deploy '{name}', and nothing was changed: {why} ({url})"
        ))}
    if report.error:
        return {**result, **_outcome("read the run log", f"'{name}' did not deploy: {report.error} ({url})")}
    if conclusion != "success":
        if _ref_is_missing(report.log, repo, ref):
            return {**result, **_outcome("set the ref", (
                f"'{name}' builds '{ref}', and {repo} has no branch, tag or commit of that name, so the run could "
                f"not check it out ({url}). Name one that exists: gapp set ref <ref> --deployment {name}"
            ))}
        said = report.words()
        return {**result, **_outcome("read the run log", (
            f"'{name}' did not deploy: {said} ({url})" if said else
            f"'{name}' did not deploy: the run ended with '{conclusion or 'no conclusion'}' and printed no reason. "
            f"Its log is at {url}"
        ))}
    done = report.result or {}
    result["lines"] += [str(line) for line in done.get("warnings") or []]
    built = _builds(done.get("ref", ref)) + (f" at {done['commit']}" if done.get("commit") else "")
    result["lines"].append(f"{name} is deployed to {project}, built from {built}.")
    if done.get("upgraded_from"):
        result["lines"].append(f"Upgraded from gapp {done['upgraded_from']} to {done.get('gapp')}.")
    if done.get("service_url"):
        result["lines"].append(f"URL: {done['service_url']}")
    result["lines"].append(f"Run: {url}")
    result["next_step"] = {"action": "ci status", "hint": f"gapp ci status --deployment {name} --live"}
    return result


# --- ci status ---


def live_lines(live: dict) -> list[str]:
    """What is live for a deployment, as lines, from the result a status run reports."""
    lines = [f"{live.get('deployment')}, on {live.get('project')}"]
    if live.get("deployed"):
        lines.append(f"  URL      {live.get('url')}")
        lines.append(f"  Healthy  {'yes' if live.get('healthy') else 'no'}")
        lines.append(f"  Deployed by gapp {live.get('last_deployed_by') or 'unknown'}")
    if domain := live.get("domain"):
        lines.append(f"  Domain   {domain.get('name')} ({domain.get('status')})")
        if domain.get("status") == "pending_dns":
            lines.append(f"    CNAME  {domain.get('name')} -> {domain.get('cname_target')}")
        if domain.get("detail"):
            lines.append(f"    {domain['detail']}")
    if live.get("next_step"):
        lines.append(str(live["next_step"]))
    return lines


def status(deployment: str | None = None, *, live: bool = False, cwd: Path | None = None, progress=None) -> dict:
    """The last deploy run of a deployment and what its definition says (`gapp ci status`).

    Without `live`, GitHub alone is read: the definition's repo, project,
    ref and gapp version, and the newest run of the deploy workflow for
    the deployment. With `live`, the status workflow is started: a runner
    signs in to the project as a deploy does and reports what is live (the
    same result `gapp status` gives), which takes about as long as a
    runner start. Nothing is written anywhere either way. `progress`,
    when given, is called with one line just before a run is started.
    """
    result = {"ok": True, "deployment": (deployment or "").strip() or None, "id": None, "repo": None, "project": None,
              "ref": None, "gapp": None, "fleet_repo": None, "last_deploy": None, "live": None,
              "workflow": STATUS_WORKFLOW if live else None, "dispatched": False, "request_id": None,
              "run_id": None, "run_url": None, "conclusion": None, "messages": [], "next_step": None, "lines": []}
    fleet_repo, stop = _fleet_repo(result)
    if stop:
        return stop
    name, _, stop = _definition(result, result["deployment"], cwd, "gapp ci status")
    if stop:
        return stop
    repo, project = result["repo"], result["project"]

    if not live:
        result["lines"].append(
            f"{name}: {_builds(result['ref'])} of {repo or 'no repo'}, on {project}, with gapp {result['gapp']}.")
        last = result["last_deploy"] = _last_deploy(fleet_repo, name)
        if last["checked"] is False:
            result["lines"].append(f"Last deploy run: {last['note']}")
        elif last["run"]:
            run = last["run"]
            result["lines"].append(
                f"Last deploy run: {run['conclusion'] or run['status'] or 'unknown'}, {run['created_at']}: {run['url']}")
            result["next_step"] = {"action": "ci status --live", "hint": f"gapp ci status --deployment {name} --live"}
        else:
            result["lines"].append("No deploy run yet.")
            result["next_step"] = {"action": "ci deploy", "hint": f"gapp ci deploy --deployment {name}"}
        return result

    stop = (_project_stop(fleet_repo, project) or _workflow_stop(fleet_repo, STATUS_WORKFLOW_PATH)
            or _handoff_stop(fleet_repo, name, result["gapp"]))
    if stop:
        return {**result, **stop}
    again = f"gapp ci status --deployment {name} --live"
    if progress:
        progress(f"Asking {project} what is live for {name}. This takes about as long as a runner start.")
    stop = _start_run(fleet_repo, STATUS_WORKFLOW, {"deployment": name}, result, "status", again)
    if stop:
        return stop
    state, conclusion = _wait_for_run(fleet_repo, result["run_id"])
    result["conclusion"] = conclusion
    url = result["run_url"]
    if state != "completed":
        return {**result, **_outcome("check the run", (
            f"The status run for '{name}' did not finish within the wait: {url}. Ask again: {again}"
        ))}
    report = _run_report(fleet_repo, result["run_id"], STATUS_LOG_PREFIX, name)
    result["messages"] = report.said
    result["live"] = report.result
    if report.refused:
        return {**result, **_refusal("fix and ask again", f"The run refused to report on '{name}': {report.refused} ({url})")}
    if conclusion != "success" or report.result is None:
        said = report.error or report.words()
        return {**result, **_outcome("read the run log", (
            f"The status run for '{name}' did not report what is live: {said} ({url})" if said else
            f"The status run for '{name}' ended with '{conclusion or 'no conclusion'}' and reported nothing. "
            f"Its log is at {url}"
        ))}
    result["lines"] = live_lines(report.result) + [f"Run: {url}"]
    return result


# --- Workload Identity and the deploy service account (`gapp ci projects connect`) ---

_WIF_POOL_ID = "github"
_WIF_PROVIDER_ID = "github"
_DEPLOY_SA_NAME = "gapp-deploy"

_DEPLOY_SA_ROLES = [
    "roles/cloudbuild.builds.editor",
    "roles/logging.viewer",
    "roles/run.admin",
    "roles/artifactregistry.admin",
    "roles/secretmanager.admin",
    "roles/storage.admin",
    "roles/iam.serviceAccountUser",
    "roles/iam.serviceAccountAdmin",
]


def _get_project_number(project_id: str) -> str:
    result = run_gcloud(["projects", "describe", project_id, "--format", "value(projectNumber)"],
                        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _ensure_wif_pool(project_id: str) -> str:
    check = run_gcloud(["iam", "workload-identity-pools", "describe", _WIF_POOL_ID,
                        "--project", project_id, "--location", "global"], capture_output=True, text=True)
    if check.returncode == 0:
        return "exists"
    result = run_gcloud(["iam", "workload-identity-pools", "create", _WIF_POOL_ID,
                         "--project", project_id, "--location", "global", "--display-name", "GitHub Actions"],
                        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "created"


def _ensure_wif_provider(project_id: str, github_owner: str) -> str:
    check = run_gcloud(["iam", "workload-identity-pools", "providers", "describe", _WIF_PROVIDER_ID,
                        "--project", project_id, "--location", "global", "--workload-identity-pool", _WIF_POOL_ID],
                       capture_output=True, text=True)
    if check.returncode == 0:
        return "exists"
    result = run_gcloud(
        ["iam", "workload-identity-pools", "providers", "create-oidc", _WIF_PROVIDER_ID,
         "--project", project_id, "--location", "global", "--workload-identity-pool", _WIF_POOL_ID,
         "--issuer-uri", "https://token.actions.githubusercontent.com",
         "--attribute-mapping",
         "google.subject=assertion.sub,attribute.actor=assertion.actor,"
         "attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner",
         "--attribute-condition", f"assertion.repository_owner == '{github_owner}'"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "created"


def _ensure_deploy_sa(project_id: str) -> str:
    sa_email = _deploy_sa_email(project_id)
    check = run_gcloud(["iam", "service-accounts", "describe", sa_email, "--project", project_id],
                       capture_output=True, text=True)
    status = "exists"
    if check.returncode != 0:
        result = run_gcloud(["iam", "service-accounts", "create", _DEPLOY_SA_NAME, "--project", project_id,
                             "--display-name", "gapp CI/CD deploy"], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        status = "created"
    for role in _DEPLOY_SA_ROLES:
        res = run_gcloud(["projects", "add-iam-policy-binding", project_id, "--member", f"serviceAccount:{sa_email}",
                          "--role", role, "--condition", "None"], capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())
    return status


# What a sign-in from GitHub Actions uses in a project.
_CI_SIGN_IN_APIS = ("iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com")


def _enable_sign_in_apis(project_id: str) -> str:
    """Enable the APIs a sign-in from GitHub Actions uses. The APIs a
    deployment needs are `gapp project setup`'s."""
    result = run_gcloud(["services", "enable", *_CI_SIGN_IN_APIS, "--project", project_id],
                        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "enabled"


def _ensure_wif_binding(project_id: str, fleet_repo: str) -> str:
    sa_email = _deploy_sa_email(project_id)
    project_number = _get_project_number(project_id)
    member = (f"principalSet://iam.googleapis.com/projects/{project_number}/locations/global/"
              f"workloadIdentityPools/{_WIF_POOL_ID}/attribute.repository/{fleet_repo}")
    result = run_gcloud(["iam", "service-accounts", "add-iam-policy-binding", sa_email, "--project", project_id,
                         "--role", "roles/iam.workloadIdentityUser", "--member", member], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "set"


def _wif_provider_path(project_number: str) -> str:
    return f"projects/{project_number}/locations/global/workloadIdentityPools/{_WIF_POOL_ID}/providers/{_WIF_PROVIDER_ID}"


def _deploy_sa_email(project_id: str) -> str:
    return f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"


# --- the gapp repo and ref the installed workflows pin ---


def _resolve_gapp_ref(gapp_repo: str) -> str:
    """Pin the reusable workflow to the installed gapp version's release tag.

    gapp tags every release `vX.Y.Z` (see CONTRIBUTING), so the installed
    workflows reference `deploy.yml@vX.Y.Z`. Falls back to the current
    `main` HEAD only when that tag isn't published yet (an unreleased dev
    build), so `ci init` from a contributor checkout still works.
    """
    import gapp

    tag = f"v{gapp.__version__}"
    exists = subprocess.run(["gh", "api", f"repos/{gapp_repo}/git/ref/tags/{tag}"], capture_output=True, text=True)
    if exists.returncode == 0:
        return tag
    head = subprocess.run(["gh", "api", f"repos/{gapp_repo}/commits/HEAD", "--jq", ".sha"], capture_output=True, text=True)
    fallback = head.stdout.strip() if head.returncode == 0 else "main"
    print(f"  gapp tag {tag} is not published on {gapp_repo}; pinning the fleet's workflows to {fallback}.", file=sys.stderr)
    return fallback


def _get_gapp_repo() -> str:
    """The gapp repo owner/name for workflow references, from the installed package's Project-URL metadata."""
    import importlib.metadata
    try:
        urls = importlib.metadata.metadata("gapp").get_all("Project-URL") or []
        for entry in urls:
            url = entry.split(",", 1)[-1].strip()
            if "github.com" in url:
                parts = url.split("github.com/")[-1].strip("/").split("/")
                if len(parts) >= 2:
                    return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass
    raise RuntimeError(
        "Could not determine the gapp repo. Ensure gapp is installed from a known GitHub source "
        "and that [project.urls] Repository is set in pyproject.toml."
    )
