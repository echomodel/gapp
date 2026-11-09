"""gapp ci: the CI fleet system.

gapp has two fleet systems that do not talk to each other. The root
commands (`gapp setup`, `deploy`, `status`, `list`, ...) deploy the local
checkout to GCP with gcloud. The `gapp ci` commands here manage the fleet
through GitHub only: they never use gcloud (except `ci project setup`,
once per project) and never read the working directory. Their record is
`gapp/deployments.yaml` in the fleet repo (one entry per deployment, see
`fleet.py`) plus the project's GitHub Environment.

Rules this module keeps (CONTRIBUTING, "Rules of the CI system"):

- Only `ci init` sets the fleet repo, in gapp's local config. Every other
  command reads it from there and stops with one message when it is unset.
- Every result carries `ok` and, when False, `next_step` with `action` and
  `hint`: a plain sentence and the exact fix. Nothing here raises for a
  condition the operator can act on.
- Something is reported missing only when GitHub's answer confirms it does
  not exist; a refused or failed read is "couldn't check", with GitHub's
  own status and message and the request that failed.
- Every GitHub call is a REST call through `gh api`. Higher-level `gh`
  subcommands (`workflow run`, `run view`, `run watch`, `repo create`,
  `repo edit`, `search`) reach for GraphQL, which some sessions (a Claude
  Code cloud session, among others) do not allow.
"""

from __future__ import annotations

import base64
import io
import json
import os
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

import yaml

from gapp.admin.sdk import fleet
from gapp.admin.sdk.config import get_fleet_repo, set_fleet_repo
from gapp.admin.sdk.fleet import DEPLOYMENTS_FILE, FleetRefusal
from gapp.admin.sdk.util import run_gcloud, require_gcloud_account

FLEET_TOPIC = "gapp-ci"

# The hosting GitHub App that lets a run clone a private repo: an Actions
# variable and an Actions secret of the fleet repo. Fixed names, like
# GITHUB_TOKEN: the credentials belong to the fleet repo (one pair per
# operator), so nothing about a deployment could want different ones.
APP_ID_VAR = "HOSTING_GITHUB_APP_ID"
APP_KEY_SECRET = "HOSTING_GITHUB_APP_PRIVATE_KEY"

# Comma-separated GitHub owners whose repos may be deployed from the fleet.
# Managed by `gapp ci sources`.
ALLOWED_SOURCES_VAR = "GAPP_ALLOWED_SOURCES"

# Per-project deploy identity, recorded in the fleet repo as a GitHub
# Environment named after the project id, holding these two variables.
# Written by `gapp ci project setup`, read by every deploy.
ENV_WIF_PROVIDER_VAR = "GAPP_WIF_PROVIDER"
ENV_DEPLOY_SA_VAR = "GAPP_DEPLOY_SERVICE_ACCOUNT"

# The gapp-owned files in the fleet repo.
DEPLOY_WORKFLOW = "gapp-deploy.yml"
REGISTER_WORKFLOW = "gapp-register.yml"
STATUS_WORKFLOW = "gapp-status.yml"
WORKFLOWS_DIR = ".github/workflows"
DEPLOY_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{DEPLOY_WORKFLOW}"
REGISTER_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{REGISTER_WORKFLOW}"
STATUS_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{STATUS_WORKFLOW}"
GAPP_WORKFLOWS = (DEPLOY_WORKFLOW, REGISTER_WORKFLOW, STATUS_WORKFLOW)
LEGACY_SOLUTIONS_FILE = "gapp/solutions.yaml"

# The register workflow prefixes every line meant for the operator, and
# reports its outcome as `gapp-register: result=<code>`.
REGISTER_LOG_PREFIX = "gapp-register:"
REGISTER_SUCCESS_CODES = ("added", "updated", "unchanged", "renamed")
# The status workflow reports `gapp-status: result=<json>`.
STATUS_LOG_PREFIX = "gapp-status:"

# --- the messages the operator reads, verbatim ---

NO_FLEET_REPO_MESSAGE = (
    "No fleet repo is set on this machine. Start here: gapp ci init "
    "(It finds your existing fleet repo, or tells you how to create one.)"
)
REPO_MISSING_MESSAGE = (
    "{repo} doesn't exist. Create it as a new private fleet repo: gapp ci init {repo} --create"
)
REPO_EXISTS_MESSAGE = (
    "{repo} already exists, so it wasn't created. To use it as your fleet repo: gapp ci init {repo}"
)
PROJECT_NOT_TRUSTED_MESSAGE = (
    "Fleet repo {fleet_repo} is not yet trusted to deploy to GCP project {project}. One-time setup:\n"
    "\n"
    "1. Run `gapp ci project setup {project}`\n"
    "   - Requires gcloud signed in as an Owner of {project}\n"
    "   - Requires `gh` signed in as an admin of {fleet_repo}\n"
    "2. Run this command again: {command}"
)
SOURCE_NOT_ALLOWED_MESSAGE = (
    "Apps owned by {owner} aren't allowed in your fleet yet (fleet repo {fleet_repo}). One-time setup:\n"
    "\n"
    "1. Run `gapp ci sources add {owner}`\n"
    "   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo {fleet_repo} --body <current-list>,{owner}\n"
    "   - Requires `gh` signed in with write access to {fleet_repo}\n"
    "2. Run this command again: {command}"
)
PROJECT_SETUP_REQUIREMENTS = (
    "`gapp ci project setup <project>` requires gcloud signed in as an Owner of the "
    "project and `gh` signed in as an admin of the fleet repo (its owner, for a personal repo)."
)
UNCHECKED_VARIABLES_NOTE = (
    "not checked: the GitHub Actions variables and secrets API is not reachable "
    "from this machine; the register and deploy runs check them"
)


# --- outcomes ---


def _outcome(action: str, hint: str, **extra) -> dict:
    """A plain outcome: what stands in the way, and the exact next step."""
    result = {"ok": False, "next_step": {"action": action, "hint": hint}}
    result.update(extra)
    return result


def _gh_missing_outcome() -> dict:
    return _outcome("install gh", (
        "The GitHub CLI (gh) is not installed on this machine, and every `gapp ci` command works "
        "through it. Install it, then sign in with `gh auth login`."
    ))


def _no_fleet_repo_outcome() -> dict:
    return _outcome("ci init", NO_FLEET_REPO_MESSAGE)


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


def _read_path(repo: str, path: str, thing: str) -> Read:
    """A file or directory of a repo through the Contents API.

    A 404 counts as absent only when the repo itself answers, so a repo the
    login cannot see is never reported as a missing file.
    """
    request = f"GET repos/{repo}/contents/{path}"
    rc, out, err = _gh_api([f"repos/{repo}/contents/{path}"])
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


def _read_file(repo: str, path: str) -> Read:
    return _read_path(repo, path, f"{path} in {repo}")


def _list_dir(repo: str, path: str) -> Read:
    rd = _read_path(repo, path, f"{path}/ in {repo}")
    if rd.present:
        rd.data = [item.get("name") for item in (rd.data or []) if isinstance(item, dict) and item.get("type") == "file"]
    return rd


def _write_file(repo: str, path: str, content: str, message: str) -> str:
    """Create or update a gapp-owned file in the fleet repo via the Contents API.

    gapp is not a git client for the operator's repos: the file is generated
    configuration, so it is written as a config-store update. GitHub makes
    the commit server-side; nothing is cloned, committed or pushed on the
    operator's machine. Idempotent: an identical file is "unchanged".
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


def _delete_file(repo: str, path: str, sha: str, message: str) -> None:
    rc, _, err = _gh_api(["-X", "DELETE", f"repos/{repo}/contents/{path}"], {"message": message, "sha": sha})
    if rc != 0:
        raise RuntimeError(f"{path} in {repo} could not be removed: {_refused(err)}")


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


def _actions_variable_value(fleet_repo: str, name: str) -> tuple[int, str | None, str]:
    rc, out, err = _gh_api([f"repos/{fleet_repo}/actions/variables/{name}"])
    if rc != 0:
        return rc, None, err
    return 0, (json.loads(out).get("value") or "").strip(), ""


def _variables_report(fleet_repo: str) -> dict:
    """Presence of the three operator-set values in the fleet repo.

    `variables` maps each name to True, False or None (not checked). Never
    sets them: the values are the operator's.
    """
    names = {ALLOWED_SOURCES_VAR: "variables", APP_ID_VAR: "variables", APP_KEY_SECRET: "secrets"}
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


def _environment_record_status(fleet_repo: str, project_id: str) -> dict:
    """Existence of the Environment and presence of each variable, for `ci status`."""
    env = _environment(fleet_repo, project_id)
    out = {"exists": None if env.unchecked else env.present, "note": env.note,
           "wif_provider_present": None, "service_account_present": None, "checked": False}
    if env.absent:
        out.update(wif_provider_present=False, service_account_present=False, checked=True)
        return out
    if env.unchecked or not _actions_api_available(fleet_repo):
        out["note"] = out["note"] or UNCHECKED_VARIABLES_NOTE
        return out
    out["checked"] = True
    for key, var in (("wif_provider_present", ENV_WIF_PROVIDER_VAR), ("service_account_present", ENV_DEPLOY_SA_VAR)):
        rc, body, _ = _gh_api([f"repos/{fleet_repo}/environments/{project_id}/variables/{var}"])
        out[key] = bool((json.loads(body).get("value") or "").strip()) if (rc == 0 and body.strip()) else False
    return out


# --- the gapp-owned files in the fleet repo ---


def _render_template(name: str, gapp_repo: str, gapp_ref: str) -> str:
    text = resources.files("gapp").joinpath("templates", "ci", name).read_text()
    return text.replace("__GAPP_REPO__", gapp_repo).replace("__GAPP_REF__", gapp_ref)


def _operator_files(gapp_repo: str, gapp_ref: str) -> dict[str, str]:
    """Path -> content of the workflows `ci init` installs (deploy, register, status)."""
    return {f"{WORKFLOWS_DIR}/{name}": _render_template(name, gapp_repo, gapp_ref) for name in GAPP_WORKFLOWS}


def _install_operator_files(fleet_repo: str) -> dict[str, str]:
    """Install or update gapp's workflows and create the deployments file.

    The workflows are written whenever the rendered content differs from
    what the repo holds (a new gapp release pins a new reusable-workflow
    ref). The deployments file is created when missing and never
    overwritten: the register workflow owns its content from then on.
    Returns path -> "created" | "updated" | "unchanged".
    """
    gapp_repo = _get_gapp_repo()
    gapp_ref = _resolve_gapp_ref(gapp_repo)
    statuses = {}
    for path, content in _operator_files(gapp_repo, gapp_ref).items():
        statuses[path] = _write_file(fleet_repo, path, content, f"Install gapp workflow {path.rsplit('/', 1)[-1]}")
    existing = _read_file(fleet_repo, DEPLOYMENTS_FILE)
    if existing.unchecked:
        raise RuntimeError(existing.note)
    if existing.absent:
        statuses[DEPLOYMENTS_FILE] = _write_file(fleet_repo, DEPLOYMENTS_FILE, fleet.DEPLOYMENTS_FILE_INITIAL,
                                                 "Add the gapp deployments file")
    else:
        statuses[DEPLOYMENTS_FILE] = "unchanged"
    return statuses


def _files_report(fleet_repo: str) -> dict:
    """Presence (or that it couldn't be checked), and currency against this
    gapp's templates, of the four files."""
    report = {}
    try:
        gapp_repo = _get_gapp_repo()
        expected = _operator_files(gapp_repo, _resolve_gapp_ref(gapp_repo))
    except RuntimeError:
        expected = {}
    for path in (DEPLOY_WORKFLOW_PATH, REGISTER_WORKFLOW_PATH, STATUS_WORKFLOW_PATH, DEPLOYMENTS_FILE):
        rd = _read_file(fleet_repo, path)
        current = (rd.content == expected[path]) if (rd.present and path in expected) else None
        report[path] = {"present": None if rd.unchecked else rd.present, "current": current, "note": rd.note}
    return report


def _legacy_workflows(fleet_repo: str) -> list[str]:
    """Deployments still run by a per-solution caller workflow from gapp 3.x.

    Any workflow other than gapp's own that calls gapp's
    reusable deploy workflow is one; they keep working from the Actions
    tab until deleted, and `ci list --verbose` notes them.
    """
    names = []
    listing = _list_dir(fleet_repo, WORKFLOWS_DIR)
    for name in (listing.data or []) if listing.present else []:
        if name in GAPP_WORKFLOWS or not name.endswith((".yml", ".yaml")):
            continue
        rd = _read_file(fleet_repo, f"{WORKFLOWS_DIR}/{name}")
        if rd.present and rd.content and "/.github/workflows/deploy.yml@" in rd.content:
            names.append(name.rsplit(".", 1)[0])
    return names


def _load_fleet(fleet_repo: str) -> tuple[dict | None, Read]:
    """The parsed deployments document and its read; the document is None
    when the file is absent or unreadable (the read says which)."""
    rd = _read_file(fleet_repo, DEPLOYMENTS_FILE)
    if not rd.present:
        return None, rd
    try:
        return fleet.parse(rd.content), rd
    except FleetRefusal as e:
        rd.state, rd.note = "unchecked", e.message
        return None, rd


def _fleet_file_outcome(fleet_repo: str, rd: Read) -> dict:
    """The outcome for a deployments file that is absent or unreadable."""
    if rd.absent:
        return _outcome("ci init", (
            f"{fleet_repo} has no {DEPLOYMENTS_FILE}, so it holds no deployments yet. Install gapp's files: "
            f"gapp ci init {fleet_repo}"
        ))
    return _outcome("check GitHub access", rd.note)


def _require(result: dict) -> tuple[str | None, dict | None]:
    """gh present and the fleet repo set; else the outcome to return."""
    if not _gh_available():
        return None, {**result, **_gh_missing_outcome()}
    fleet_repo, _ = get_fleet_repo()
    if not fleet_repo:
        return None, {**result, **_no_fleet_repo_outcome()}
    result["fleet_repo"] = fleet_repo
    return fleet_repo, None


# --- ci init ---


def _search_fleet_repos(owner: str | None) -> tuple[list[str] | None, str | None]:
    """Repos carrying the gapp-ci topic under an owner (the gh user when None): one REST search."""
    who = owner or _gh_login()
    query = f"topic:{FLEET_TOPIC} user:{who}"
    request = f"GET search/repositories?q={query}"
    rc, out, err = _gh_api(["-X", "GET", "search/repositories", "-f", f"q={query}", "-f", "per_page=100"])
    if rc != 0:
        return None, _couldnt_check(f"the repos tagged {FLEET_TOPIC} under {owner or 'your account'}", request, err)
    try:
        return sorted(r["full_name"] for r in (json.loads(out or "{}").get("items") or [])), None
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None, f"Couldn't check the repos tagged {FLEET_TOPIC}: GitHub returned an answer gapp did not recognise. Request: {request}"


def init_fleet(repo: str | None = None, *, owner: str | None = None, create: bool = False) -> dict:
    """Show, find, set or create the fleet repo for this machine.

    Without a repo: the saved fleet repo and how it was set; when none is
    saved (or an owner is named), one search of that owner's repos for the
    gapp-ci topic: one match is saved and shown, several are listed, none
    gives the create guidance. With a repo: it is set for this machine if
    it exists (and gapp's files are installed or updated in it), or
    created private, tagged and populated with `create`. The only command
    that writes the fleet repo setting.
    """
    result = {"ok": True, "fleet_repo": None, "set_by": None, "found": [], "created": False,
              "files": None, "legacy_solutions": None, "variables": None, "variables_note": None,
              "topic": None, "next_step": None}
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}

    if not repo:
        saved, set_by = get_fleet_repo()
        if saved and not owner:
            result.update(fleet_repo=saved, set_by=set_by)
            result["next_step"] = None
            return result
        found, note = _search_fleet_repos(owner)
        if found is None:
            return {**result, **_outcome("check GitHub access", note)}
        result["found"] = found
        where = owner or "your GitHub account"
        if not found:
            return {**result, **_outcome("create a fleet repo", (
                f"No fleet repo is set on this machine, and no repo under {where} carries the {FLEET_TOPIC} topic. "
                f"Create one: gapp ci init {owner or '<owner>'}/<repo> --create"
            ))}
        if len(found) > 1:
            return {**result, **_outcome("pick one fleet repo", (
                f"Several repos under {where} carry the {FLEET_TOPIC} topic: {', '.join(found)}. "
                f"Pick one: gapp ci init <owner>/<repo>"
            ))}
        set_fleet_repo(found[0], f"found by its {FLEET_TOPIC} topic under {where}")
        result.update(fleet_repo=found[0], set_by=f"found by its {FLEET_TOPIC} topic under {where}")
        return result

    repo = repo.strip()
    if not fleet.REPO_RE.match(repo):
        return {**result, **_outcome("name the repo as owner/name", (
            f"'{repo}' is not of the form owner/name. Name the fleet repo as <owner>/<repo>."
        ))}
    rc, _, err = _gh_api([f"repos/{repo}"])
    if rc != 0:
        status, _ = _gh_failure(err)
        if status != 404:
            return {**result, **_outcome("check GitHub access", _couldnt_check(repo, f"GET repos/{repo}", err))}
        exists = False
    else:
        exists = True

    if create:
        if exists:
            return {**result, **_outcome("use the existing repo", REPO_EXISTS_MESSAGE.format(repo=repo))}
        owner_name, name = repo.split("/", 1)
        endpoint = "user/repos" if owner_name.lower() == _gh_login().lower() else f"orgs/{owner_name}/repos"
        rc, _, err = _gh_api(["-X", "POST", endpoint], {
            "name": name, "private": True, "description": "gapp fleet: deployments and their workflows",
        })
        if rc != 0:
            return {**result, **_outcome("create the fleet repo", (
                f"{repo} could not be created: {_refused(err)}. Create it on GitHub (private), then run "
                f"`gapp ci init {repo}`."
            ))}
        result["created"] = True
    elif not exists:
        return {**result, **_outcome("create the fleet repo", REPO_MISSING_MESSAGE.format(repo=repo))}

    set_fleet_repo(repo, f"gapp ci init {repo}" + (" --create" if create else ""))
    result.update(fleet_repo=repo, set_by=f"gapp ci init {repo}" + (" --create" if create else ""))

    result["topic"] = _add_topic(repo)

    try:
        result["files"] = _install_operator_files(repo)
    except RuntimeError as e:
        result["files"] = None
        result.update(_outcome("install gapp's files", (
            f"{repo} is your fleet repo now, but gapp's files could not be installed in it from this machine: "
            f"{e}. Run `gapp ci init {repo}` from a machine whose `gh` can write to the repo; it installs "
            f"{DEPLOY_WORKFLOW_PATH}, {REGISTER_WORKFLOW_PATH}, {STATUS_WORKFLOW_PATH} and {DEPLOYMENTS_FILE}."
        )))
        return result

    result["legacy_solutions"] = _retire_legacy_solutions_file(repo)
    report = _variables_report(repo)
    result["variables"] = report["variables"]
    result["variables_note"] = report["note"]
    if result["legacy_solutions"] and result["legacy_solutions"].get("kept"):
        n = result["legacy_solutions"]["count"]
        result["next_step"] = {"action": "set up the 3.9 solutions again", "hint": (
            f"{repo} still has {LEGACY_SOLUTIONS_FILE} from gapp 3.9 with {n} solution(s). Set each up again "
            f"with `gapp ci setup <name> --repo <owner>/<repo> --project <project>`, then delete that file."
        )}
    elif report["variables"][ALLOWED_SOURCES_VAR] is True:
        result["next_step"] = {"action": "ci project setup", "hint": "gapp ci project setup <project>"}
    else:
        result["next_step"] = {"action": "ci sources add", "hint": "gapp ci sources add <owner>"}
    return result


def _add_topic(repo: str) -> str:
    """Add the gapp-ci topic to a repo, keeping its other topics (REST)."""
    rc, out, err = _gh_api([f"repos/{repo}/topics"])
    if rc != 0:
        return f"not set: {_refused(err)}"
    try:
        names = list(json.loads(out or "{}").get("names") or [])
    except (json.JSONDecodeError, AttributeError):
        names = []
    if FLEET_TOPIC in names:
        return "set"
    rc, _, err = _gh_api(["-X", "PUT", f"repos/{repo}/topics"], {"names": names + [FLEET_TOPIC]})
    return "set" if rc == 0 else f"not set: {_refused(err)}"


def _retire_legacy_solutions_file(fleet_repo: str) -> dict | None:
    """Remove an empty gapp/solutions.yaml left by 3.9.0; report a non-empty one."""
    rd = _read_file(fleet_repo, LEGACY_SOLUTIONS_FILE)
    if not rd.present:
        return None
    try:
        entries = (yaml.safe_load(rd.content) or {}).get("solutions") or []
    except (yaml.YAMLError, AttributeError):
        entries = []
    if entries:
        return {"kept": True, "count": len(entries)}
    try:
        _delete_file(fleet_repo, LEGACY_SOLUTIONS_FILE, rd.sha, "Remove the empty gapp 3.9 solutions file")
    except RuntimeError as e:
        return {"kept": True, "count": 0, "note": str(e)}
    return {"kept": False, "count": 0}


# --- ci project setup: the one CI command that touches GCP ---


def _gcloud_outcome(step: str, project_id: str, detail: str) -> dict:
    """Classify a gcloud failure into words with the requirement restated."""
    text = (detail or "").strip()
    first = text.splitlines()[0] if text else ""
    lowered = text.lower()
    if "unauthenticated" in lowered or "credential" in lowered or "login" in lowered:
        why = "gcloud has no working sign-in on this machine"
    elif "permission" in lowered or "403" in lowered or "forbidden" in lowered:
        why = f"the signed-in gcloud account is not an Owner of {project_id} (permission denied)"
    elif "not found" in lowered or "404" in lowered:
        why = f"project {project_id} was not found by the signed-in gcloud account"
    else:
        why = f"gcloud could not {step}" + (f" ({first})" if first else "")
    return _outcome("sign in to gcloud as an Owner", (
        f"Could not {step} for project {project_id}: {why}. {PROJECT_SETUP_REQUIREMENTS} "
        f"Sign in with `gcloud auth login` (or point gapp at the right account with "
        f"`gapp config account <email>`), then run `gapp ci project setup {project_id}` again."
    ))


def setup_ci_project(project: str) -> dict:
    """Make the fleet repo trusted to deploy to a GCP project. Once per project, idempotent.

    Creates or confirms the Workload Identity pool and provider and the
    `gapp-deploy` service account in the project, binds the pool to the
    fleet repo, and records the deploy identity in the fleet repo as the
    GitHub Environment named after the project. The only `gapp ci` command
    that touches GCP: it requires gcloud signed in as an Owner of the
    project and `gh` signed in as an admin of the fleet repo, and every
    failure says which.
    """
    project_id = (project or "").strip()
    result = {"ok": True, "project_id": project_id, "fleet_repo": None,
              "wif_pool": None, "wif_provider": None, "service_account": None, "binding": None,
              "hosted": None, "environment": project_id, "environment_record": None, "changed": None, "next_step": None}
    if not project_id:
        return {**result, **_outcome("pass the project", "Name the GCP project: `gapp ci project setup <project>`.")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop

    admin, reason = _repo_permission(fleet_repo)
    if admin is not True:
        why = reason if admin is None else f"the signed-in `gh` account is not an admin of {fleet_repo}"
        return {**result, **_outcome("sign in to gh as an admin", (
            f"Cannot record the project's deploy identity in {fleet_repo}: {why}. "
            f"{PROJECT_SETUP_REQUIREMENTS} Sign in with `gh auth login` as the repo's admin "
            f"(its owner, for a personal repo), then run `gapp ci project setup {project_id}` again."
        ))}

    if shutil.which("gcloud") is None:
        return {**result, **_outcome("install gcloud", (
            f"gcloud is not installed on this machine. {PROJECT_SETUP_REQUIREMENTS} Run "
            f"`gapp ci project setup {project_id}` from a machine with gcloud signed in as an "
            f"Owner of {project_id}; every other `gapp ci` command works from here."
        ))}
    try:
        require_gcloud_account()
    except RuntimeError as e:
        return {**result, **_outcome("sign in to gcloud as an Owner", f"{e} {PROJECT_SETUP_REQUIREMENTS}")}

    steps = (
        ("wif_pool", "create or confirm the Workload Identity pool", lambda: _ensure_wif_pool(project_id)),
        ("wif_provider", "create or confirm the Workload Identity provider",
         lambda: _ensure_wif_provider(project_id, fleet_repo.split("/")[0])),
        ("service_account", "create or confirm the gapp-deploy service account", lambda: _ensure_deploy_sa(project_id)),
        ("binding", "bind the Workload Identity pool to the fleet repo", lambda: _ensure_wif_binding(project_id, fleet_repo)),
        ("hosted", "mark the project as hosting gapp deployments (gapp=hosted)", lambda: _mark_project_hosted(project_id)),
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
            f"GCP is ready, but the deploy identity could not be recorded in {fleet_repo}: could not {e}. "
            f"{PROJECT_SETUP_REQUIREMENTS} Then run `gapp ci project setup {project_id}` again; the GCP "
            f"steps are idempotent."
        ))}
    result["changed"] = any(result[k] == "created" for k in ("wif_pool", "wif_provider", "service_account")) \
        or result["environment_record"] != "unchanged"
    return result


# --- ci sources ---


def _sources_list(fleet_repo: str) -> tuple[list[str] | None, dict | None]:
    """The allowed owners, or (None, outcome) when they cannot be read."""
    rc, value, err = _actions_variable_value(fleet_repo, ALLOWED_SOURCES_VAR)
    if rc != 0:
        status, _ = _gh_failure(err)
        if status == 404 and _actions_api_available(fleet_repo):
            return [], None
        return None, _outcome("check GitHub access", (
            _couldnt_check(f"{ALLOWED_SOURCES_VAR} in {fleet_repo}",
                           f"GET repos/{fleet_repo}/actions/variables/{ALLOWED_SOURCES_VAR}", err)
            + f" `gapp ci sources` needs `gh` signed in with write access to {fleet_repo}."
        ))
    return [o.strip() for o in (value or "").split(",") if o.strip()], None


def _sources_write(fleet_repo: str, owners: list[str], existed: bool) -> dict | None:
    endpoint = f"repos/{fleet_repo}/actions/variables"
    value = ",".join(owners)
    if not owners and existed:
        rc, _, err = _gh_api(["-X", "DELETE", f"{endpoint}/{ALLOWED_SOURCES_VAR}"])
    elif existed:
        rc, _, err = _gh_api(["-X", "PATCH", f"{endpoint}/{ALLOWED_SOURCES_VAR}"], {"name": ALLOWED_SOURCES_VAR, "value": value})
    else:
        rc, _, err = _gh_api(["-X", "POST", endpoint], {"name": ALLOWED_SOURCES_VAR, "value": value})
    if rc != 0:
        return _outcome("sign in to gh with write access", (
            f"{ALLOWED_SOURCES_VAR} in {fleet_repo} could not be written: {_refused(err)}. "
            f"{_fix_for(err, f'write access to {fleet_repo}')} run the command again, or set the variable from a "
            f"machine that can:\n"
            f"  gh variable set {ALLOWED_SOURCES_VAR} --repo {fleet_repo} --body {value or '<owners>'}"
        ))
    return None


def manage_sources(action: str, owner: str | None = None) -> dict:
    """List, add to, or remove from the GitHub owners whose repos may be deployed from the fleet.

    The list is the fleet repo's repository variable `GAPP_ALLOWED_SOURCES`
    (comma-separated owners); the register run refuses a repo whose owner
    is not on it. Needs `gh` with write access to the fleet repo (read
    access for `list`).
    """
    result = {"ok": True, "action": action, "fleet_repo": None, "owner": owner, "owners": None,
              "changed": False, "next_step": None}
    if action not in ("add", "remove", "list"):
        return {**result, **_outcome("choose an action", "Use `gapp ci sources add|remove|list [<owner>]`.")}
    if action != "list" and not (owner or "").strip():
        return {**result, **_outcome("pass the owner", f"Name the GitHub owner: `gapp ci sources {action} <owner>`.")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop

    owners, outcome = _sources_list(fleet_repo)
    if outcome:
        return {**result, **outcome}
    existed = _actions_item_present(fleet_repo, "variables", ALLOWED_SOURCES_VAR) is True
    result["owners"] = list(owners)
    if action == "list":
        return result

    owner = owner.strip()
    present = any(o.lower() == owner.lower() for o in owners)
    if action == "add":
        if present:
            return result
        new_owners = owners + [owner]
    else:
        if not present:
            return result
        new_owners = [o for o in owners if o.lower() != owner.lower()]
    refusal = _sources_write(fleet_repo, new_owners, existed)
    if refusal:
        return {**result, **refusal}
    result["owners"] = new_owners
    result["changed"] = True
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
        said += (f" (the fleet repo's {workflow_file} is older than this gapp and does not take a request id; "
                 f"update gapp's files with `gapp ci init {fleet_repo}`)")
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


def _run_messages(fleet_repo: str, run_id: str, prefix: str) -> list[str]:
    """The lines a run's steps printed for the operator (those starting with `prefix`)."""
    return _emitted_messages(_run_log(fleet_repo, run_id), prefix)


def _run_url(fleet_repo: str, run_id: str | None) -> str | None:
    return f"https://github.com/{fleet_repo}/actions/runs/{run_id}" if run_id else None


def _run_conclusion(fleet_repo: str, run_id: str) -> str | None:
    rc, out, _ = _gh_api([f"repos/{fleet_repo}/actions/runs/{run_id}", "--jq", ".conclusion"])
    return (out.strip() or None) if rc == 0 else None


def _last_deploy(fleet_repo: str, name: str) -> dict:
    """The newest run of the deploy workflow for a deployment, by its run name
    "Deploy <name> (<ref>) <request id>"; `checked` False with a note when runs cannot be read."""
    request = f"GET repos/{fleet_repo}/actions/workflows/{DEPLOY_WORKFLOW}/runs"
    rc, out, err = _gh_api([f"repos/{fleet_repo}/actions/workflows/{DEPLOY_WORKFLOW}/runs?per_page=50"])
    if rc != 0:
        return {"checked": False, "run": None, "note": _couldnt_check(f"the deploy runs of {fleet_repo}", request, err)}
    try:
        runs = json.loads(out).get("workflow_runs") or []
    except (json.JSONDecodeError, AttributeError):
        runs = []
    pattern = re.compile(rf"^Deploy {re.escape(name)} \((?P<ref>[^)]*)\)(?: \S+)?$")
    for run in runs:
        match = pattern.match(run.get("display_title") or run.get("name") or "")
        if match:
            return {"checked": True, "note": None, "run": {
                "id": str(run.get("id")), "status": run.get("status"), "conclusion": run.get("conclusion"),
                "created_at": run.get("created_at"), "ref": match.group("ref"),
                "url": run.get("html_url") or _run_url(fleet_repo, run.get("id")),
            }}
    return {"checked": True, "run": None, "note": None}


# --- ci setup and ci rename: the register run writes the fleet file ---


def _register(fleet_repo: str, result: dict, inputs: dict, *, watch: bool, command: str,
              owner: str | None, project: str | None) -> dict:
    """Dispatch the register workflow and read its result into `result`."""
    register = _read_file(fleet_repo, REGISTER_WORKFLOW_PATH)
    if register.absent:
        return {**result, **_outcome("ci init", (
            f"{fleet_repo} has no {REGISTER_WORKFLOW_PATH}, so there is nothing to dispatch. Install gapp's "
            f"files: gapp ci init {fleet_repo}"
        ))}
    if register.unchecked:
        return {**result, **_outcome("check GitHub access", register.note)}

    dispatch = _dispatch(fleet_repo, REGISTER_WORKFLOW, inputs)
    if dispatch.refused:
        said, err = dispatch.refused, dispatch.stderr
        return {**result, **_outcome("dispatch the register workflow", (
            f"The register workflow could not be dispatched in {fleet_repo}: {said}. "
            f"{_fix_for(err, f'access to dispatch workflows in {fleet_repo}')} run this command again: {command}"
        ))}
    result["dispatched"] = True
    result["request_id"] = dispatch.request_id
    result["run_id"] = _find_run(fleet_repo, REGISTER_WORKFLOW, dispatch)
    result["run_url"] = _run_url(fleet_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Dispatched {REGISTER_WORKFLOW} in {fleet_repo}, but no run carrying its request id {dispatch.request_id} "
            f"appeared within {RUN_APPEAR_TIMEOUT} seconds, so no result is reported (an older run's never is). Check the "
            f"repo's Actions tab for the register run, then `gapp ci status {result.get('name')}`."
        ))}
    if not watch:
        return result

    status, conclusion = _wait_for_run(fleet_repo, result["run_id"])
    result["conclusion"] = conclusion
    result["messages"] = _run_messages(fleet_repo, result["run_id"], REGISTER_LOG_PREFIX)
    fields = dict(m.split("=", 1) for m in result["messages"] if re.match(r"^(result|name|id|project)=", m))
    result["result_code"] = fields.get("result")
    if fields.get("name"):
        result["name"] = fields["name"]
    if fields.get("id"):
        result["id"] = fields["id"]
    if fields.get("project"):
        result["project"] = fields["project"]
    texts = [m for m in result["messages"] if not re.match(r"^(result|name|id|project)=", m)]
    if status != "completed":
        return {**result, **_outcome("check the run", (
            f"The register run did not finish within the wait: {result['run_url']}. Check it there; when it "
            f"has written '{result.get('name')}', `gapp ci deploy {result.get('name')}` works. Otherwise run this "
            f"command again: {command}"
        ))}
    if result["result_code"] in REGISTER_SUCCESS_CODES and conclusion == "success":
        result["action"] = result["result_code"]
        return result
    if result["result_code"] == "source-owner-not-allowed":
        return {**result, **_outcome("ci sources add", SOURCE_NOT_ALLOWED_MESSAGE.format(
            owner=owner or "<owner>", fleet_repo=fleet_repo, command=command))}
    if result["result_code"] == "project-not-trusted":
        return {**result, **_outcome("ci project setup", PROJECT_NOT_TRUSTED_MESSAGE.format(
            fleet_repo=fleet_repo, project=project or "<project>", command=command))}
    said = " ".join(texts) if texts else "it ended with no message"
    return {**result, **_outcome("fix and run again", (
        f"The register run refused the change: {said} ({result['run_url']}). Fix what it names, then run "
        f"this command again: {command}"
    ))}


def _refusal_action(code: str) -> str:
    return {
        "name-taken": "pick another name", "project-bound": "add a deployment", "repo-deployed": "update or copy",
        "name-too-long": "pick a shorter name", "source-missing": "name an existing deployment",
        "deployment-missing": "name an existing deployment", "invalid-input": "fix the input",
    }.get(code, "fix the input")


def _default_command(name, *, repo=None, project=None, domain=None, copy_of=None, config_path=None) -> str:
    parts = ["gapp ci setup", name]
    if copy_of:
        parts += ["--copy-of", copy_of]
    if repo and not copy_of:
        parts += ["--repo", repo]
    if project:
        parts += ["--project", project]
    if domain is not None:
        parts += ["--domain", domain or '""']
    if config_path:
        parts += ["--config", config_path]
    return " ".join(parts)


def setup_deployment(name: str | None = None, *, repo: str | None = None, project: str | None = None,
                     domain: str | None = None, config: dict | None = None, copy_of: str | None = None,
                     watch: bool = True, command: str | None = None) -> dict:
    """Add or update a deployment of the fleet (`gapp ci setup`). Never uses gcloud, never reads the cwd.

    The name defaults to the repo's name, derived only from `repo`. Same
    name and repo: an update of its gapp.yaml keys (`domain` sets or, as
    "", removes the domain; `config` is a mapping of gapp.yaml keys). With
    `copy_of`, a new deployment from an existing one: the same repo and
    gapp.yaml keys, never the id, the domain, deploy history or secret
    values; `project` defaults to the source's. Every refusal (name taken,
    repo already deployed under another name, id too long, project not
    trusted, owner not allowed) is a plain outcome; the same checks run
    again in the register run, which writes the file with its own token.
    """
    result = {"ok": True, "name": None, "repo": None, "project": None, "domain": None, "id": None,
              "fleet_repo": None, "copy_of": copy_of, "action": None, "changes": None,
              "copied_env": [], "secrets": [], "project_from_source": False,
              "workflow": REGISTER_WORKFLOW, "dispatched": False, "run_id": None, "run_url": None,
              "conclusion": None, "result_code": None, "messages": [], "next_step": None}
    changes = dict(config or {})
    if domain is not None:
        changes["domain"] = domain.strip() or None
    result["changes"] = changes

    if copy_of:
        if not (name or "").strip():
            return {**result, **_outcome("pass the new name", (
                f"Name the new deployment: `gapp ci setup <name> --copy-of {copy_of}`."
            ))}
    else:
        if not (repo or "").strip():
            return {**result, **_outcome("pass --repo", (
                "Name the repo to deploy: `gapp ci setup [<name>] --repo <owner>/<repo> --project <project>`. "
                "The deployment name defaults to the repo's name."
            ))}
        repo = repo.strip()
        if not fleet.REPO_RE.match(repo):
            return {**result, **_outcome("fix the input", f"'{repo}' is not a repo of the form owner/name.")}
        if not (project or "").strip():
            return {**result, **_outcome("pass --project", (
                "Name the GCP project: `gapp ci setup [<name>] --repo <owner>/<repo> --project <project>`. "
                "The CI system never looks a project up in GCP."
            ))}
        name = (name or "").strip() or repo.split("/", 1)[1]
    name = name.strip()
    project = (project or "").strip() or None
    result.update(name=name, repo=repo, project=project)
    command = command or _default_command(name, repo=repo, project=project, domain=domain, copy_of=copy_of)

    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}

    try:
        if copy_of:
            new_doc, entry, info = fleet.copy_entry(doc, name, copy_of, project=project, changes=changes)
            result.update(repo=entry["repo"], project=entry["project"], copied_env=info["copied_env"],
                          secrets=info["secrets"], project_from_source=not project)
            action = "added"
            operation = "copy"
        else:
            new_doc, action, entry = fleet.set_entry(doc, name, repo=repo, project=project, changes=changes)
            operation = "set"
    except FleetRefusal as e:
        hint = e.message
        if e.code == "repo-deployed":
            hint = f"{repo} can't be set up as {name}: {e.message}"
        return {**result, **_outcome(_refusal_action(e.code), hint)}
    # The run confirms the action against the live file; only "unchanged" is final here.
    result.update(id=entry["id"], domain=entry.get("domain"), project=entry["project"],
                  action=action if action == "unchanged" else None)
    if action == "unchanged":
        return result

    env = _environment(fleet_repo, entry["project"])
    if env.absent:
        return {**result, **_outcome("ci project setup", PROJECT_NOT_TRUSTED_MESSAGE.format(
            fleet_repo=fleet_repo, project=entry["project"], command=command))}

    inputs = {"operation": operation, "name": name, "repo": entry["repo"] if operation == "set" else "",
              "project": project or "", "changes": json.dumps(changes), "source": copy_of or ""}
    result["id"] = None                                    # the run allocates the id against the live file
    return _register(fleet_repo, result, inputs, watch=watch, command=command,
                     owner=entry["repo"].split("/")[0], project=entry["project"])


def rename_deployment(old: str, new: str, *, watch: bool = True) -> dict:
    """Change a deployment's name only (`gapp ci rename`); its id and GCP resources are untouched."""
    result = {"ok": True, "name": (new or "").strip(), "old_name": (old or "").strip(), "id": None, "repo": None,
              "project": None, "fleet_repo": None, "action": None, "workflow": REGISTER_WORKFLOW,
              "dispatched": False, "run_id": None, "run_url": None, "conclusion": None, "result_code": None,
              "messages": [], "next_step": None}
    if not result["old_name"] or not result["name"]:
        return {**result, **_outcome("pass both names", "Use `gapp ci rename <old> <new>`.")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}
    try:
        new_doc = fleet.rename_entry(doc, result["old_name"], result["name"])
    except FleetRefusal as e:
        return {**result, **_outcome(_refusal_action(e.code), e.message)}
    entry = new_doc[result["name"]]
    result.update(id=entry["id"], repo=entry.get("repo"), project=entry.get("project"))
    inputs = {"operation": "rename", "name": result["name"], "repo": "", "project": "", "changes": "{}",
              "source": result["old_name"]}
    return _register(fleet_repo, result, inputs, watch=watch,
                     command=f"gapp ci rename {result['old_name']} {result['name']}",
                     owner=(entry.get("repo") or "/").split("/")[0], project=entry.get("project"))


# --- ci deploy ---


def deploy_deployment(name: str, *, ref: str = "main", watch: bool = True) -> dict:
    """Deploy one deployment through the fleet repo's deploy workflow (`gapp ci deploy <name>`).

    Dispatches `gapp-deploy.yml` with the name and the ref and, when
    `watch` is True, streams the run and reports its conclusion. Needs only
    a `gh` that can dispatch workflows in the fleet repo.
    """
    result = {"ok": True, "name": (name or "").strip(), "repo": None, "project": None, "fleet_repo": None,
              "workflow": DEPLOY_WORKFLOW, "ref": ref, "run_id": None, "run_url": None, "watched": False,
              "conclusion": None, "next_step": None}
    if not result["name"]:
        return {**result, **_outcome("pass the name", "Name the deployment: `gapp ci deploy <name>`. See them: gapp ci list")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}
    entry = doc.get(result["name"])
    if entry is None:
        return {**result, **_outcome("ci setup", (
            f"'{result['name']}' is not a deployment in {fleet_repo}, so there is nothing to deploy. Set it up: "
            f"gapp ci setup {result['name']} --repo <owner>/<repo> --project <project>. See the deployments: gapp ci list"
        ))}
    result.update(repo=entry.get("repo"), project=entry.get("project"))
    deploy = _read_file(fleet_repo, DEPLOY_WORKFLOW_PATH)
    if deploy.absent:
        return {**result, **_outcome("ci init", (
            f"{fleet_repo} has no {DEPLOY_WORKFLOW_PATH}. Install gapp's files: gapp ci init {fleet_repo}"
        ))}
    if deploy.unchecked:
        return {**result, **_outcome("check GitHub access", deploy.note)}

    dispatch = _dispatch(fleet_repo, DEPLOY_WORKFLOW, {"deployment": result["name"], "ref": ref})
    if dispatch.refused:
        said, err = dispatch.refused, dispatch.stderr
        return {**result, **_outcome("dispatch the deploy workflow", (
            f"The deploy workflow could not be dispatched in {fleet_repo}: {said}. "
            f"{_fix_for(err, f'access to dispatch workflows in {fleet_repo}')} run `gapp ci deploy {result['name']}` again."
        ))}
    result["request_id"] = dispatch.request_id
    result["run_id"] = _find_run(fleet_repo, DEPLOY_WORKFLOW, dispatch)
    result["run_url"] = _run_url(fleet_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Dispatched {DEPLOY_WORKFLOW} in {fleet_repo} for '{result['name']}', but no run carrying its request id "
            f"{dispatch.request_id} appeared within {RUN_APPEAR_TIMEOUT} seconds, so no result is reported (an older "
            f"run's never is). Check the repo's Actions tab, or `gapp ci status {result['name']}`."
        ))}
    if not watch:
        return result

    status, conclusion = _wait_for_run(fleet_repo, result["run_id"], timeout=3600)
    result["watched"] = True
    result["conclusion"] = conclusion if status == "completed" else _run_conclusion(fleet_repo, result["run_id"])
    if result["conclusion"] != "success":
        return {**result, **_outcome("read the run log", (
            f"'{result['name']}' didn't deploy: the run ended with '{result['conclusion'] or 'no conclusion'}': "
            f"{result['run_url']}. Its first steps name a missing credential or Environment variable in words; "
            f"the deploy step's log has gapp's own message otherwise."
        ))}
    return result


# --- ci status and ci list ---


def status_deployment(name: str) -> dict:
    """One deployment's state (`gapp ci status <name>`): its repo, project,
    domain, last deploy, and whether a deploy can run now. Never touches GCP."""
    result = {"ok": True, "name": (name or "").strip(), "fleet_repo": None, "registered": None, "id": None,
              "repo": None, "project": None, "domain": None, "overlay": None, "environment_record": None,
              "last_deploy": None, "problem": None, "live_hint": None, "next_step": None}
    if not result["name"]:
        return {**result, **_outcome("pass the name", "Name the deployment: `gapp ci status <name>`. See them: gapp ci list")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}
    entry = doc.get(result["name"])
    result["registered"] = entry is not None
    if entry is None:
        return {**result, **_outcome("ci setup", (
            f"'{result['name']}' is not a deployment in {fleet_repo}. Set it up: gapp ci setup {result['name']} "
            f"--repo <owner>/<repo> --project <project>. See the deployments: gapp ci list"
        ))}
    result.update(id=entry.get("id"), repo=entry.get("repo"), project=entry.get("project"),
                  domain=entry.get("domain"), overlay=fleet.manifest_keys(entry),
                  live_hint=f"For the live service state: gapp ci status {result['name']} --live")
    record = _environment_record_status(fleet_repo, entry["project"]) if entry.get("project") else None
    result["environment_record"] = record
    result["last_deploy"] = _last_deploy(fleet_repo, result["name"])
    if record is not None:
        if record["exists"] is False:
            result["problem"] = (
                f"{fleet_repo} has no Environment named '{entry['project']}', so a deploy cannot authenticate to "
                f"that project (gcloud as an Owner of the project, `gh` as an admin of {fleet_repo})."
            )
            result["next_step"] = {"action": "ci project setup", "hint": f"gapp ci project setup {entry['project']}"}
        elif record["checked"] and not (record["wif_provider_present"] and record["service_account_present"]):
            missing = [v for v, ok in ((ENV_WIF_PROVIDER_VAR, record["wif_provider_present"]),
                                       (ENV_DEPLOY_SA_VAR, record["service_account_present"])) if not ok]
            result["problem"] = (
                f"The Environment '{entry['project']}' in {fleet_repo} is missing {', '.join(missing)}, so a "
                f"deploy cannot authenticate; recording the deploy identity again fixes it."
            )
            result["next_step"] = {"action": "ci project setup", "hint": f"gapp ci project setup {entry['project']}"}
    return result


def status_live(name: str) -> dict:
    """The live service state of one deployment (`gapp ci status <name> --live`).

    Dispatches the fleet repo's read-only status workflow, which runs
    `gapp status` for the deployment's id on a runner authenticated as the
    project's existing deploy identity, and returns that result (the same
    dict `gapp_status` returns) as `live`. Takes about as long as a runner
    start. Nothing is stored: live state is asked of the project, never
    written to the fleet repo.
    """
    result = {"ok": True, "name": (name or "").strip(), "fleet_repo": None, "id": None, "repo": None,
              "project": None, "workflow": STATUS_WORKFLOW, "dispatched": False, "run_id": None, "run_url": None,
              "conclusion": None, "live": None, "messages": [], "next_step": None}
    if not result["name"]:
        return {**result, **_outcome("pass the name", "Name the deployment: `gapp ci status <name> --live`. See them: gapp ci list")}
    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}
    entry = doc.get(result["name"])
    if entry is None:
        return {**result, **_outcome("ci setup", (
            f"'{result['name']}' is not a deployment in {fleet_repo}. Set it up: gapp ci setup {result['name']} "
            f"--repo <owner>/<repo> --project <project>. See the deployments: gapp ci list"
        ))}
    result.update(id=entry.get("id"), repo=entry.get("repo"), project=entry.get("project"))
    workflow = _read_file(fleet_repo, STATUS_WORKFLOW_PATH)
    if workflow.absent:
        return {**result, **_outcome("ci init", (
            f"{fleet_repo} has no {STATUS_WORKFLOW_PATH}, so there is nothing to ask. Install gapp's files: "
            f"gapp ci init {fleet_repo}"
        ))}
    if workflow.unchecked:
        return {**result, **_outcome("check GitHub access", workflow.note)}

    dispatch = _dispatch(fleet_repo, STATUS_WORKFLOW, {"deployment": result["name"]})
    if dispatch.refused:
        said, err = dispatch.refused, dispatch.stderr
        return {**result, **_outcome("dispatch the status workflow", (
            f"The status workflow could not be dispatched in {fleet_repo}: {said}. "
            f"{_fix_for(err, f'access to dispatch workflows in {fleet_repo}')} run `gapp ci status {result['name']} --live` again."
        ))}
    result["dispatched"] = True
    result["request_id"] = dispatch.request_id
    result["run_id"] = _find_run(fleet_repo, STATUS_WORKFLOW, dispatch)
    result["run_url"] = _run_url(fleet_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Dispatched {STATUS_WORKFLOW} in {fleet_repo} for '{result['name']}', but no run carrying its request id "
            f"{dispatch.request_id} appeared within {RUN_APPEAR_TIMEOUT} seconds, so no live state is reported (an "
            f"older run's never is). Check the repo's Actions tab, or run it again."
        ))}
    status, conclusion = _wait_for_run(fleet_repo, result["run_id"])
    result["conclusion"] = conclusion
    result["messages"] = _run_messages(fleet_repo, result["run_id"], STATUS_LOG_PREFIX)
    for message in result["messages"]:
        if message.startswith("result="):
            try:
                result["live"] = json.loads(message[len("result="):])
            except json.JSONDecodeError:
                result["live"] = None
    texts = [m for m in result["messages"] if not m.startswith("result=")]
    if status != "completed":
        return {**result, **_outcome("check the run", (
            f"The status run did not finish within the wait: {result['run_url']}. Check it there, or run "
            f"`gapp ci status {result['name']} --live` again."
        ))}
    if conclusion != "success" or result["live"] is None:
        said = " ".join(t[len("error="):] if t.startswith("error=") else t for t in texts) or "it ended with no message"
        return {**result, **_outcome("read the run log", (
            f"The status run for '{result['name']}' did not report the live state: {said} ({result['run_url']})."
        ))}
    return result


def list_deployments(repo: str | None = None, *, verbose: bool = False) -> dict:
    """The fleet's deployments (`gapp ci list`), optionally one repo's. With
    `verbose`, the fleet repo's files, variables and per-solution workflows
    from gapp 3.x too. Never touches GCP."""
    result = {"ok": True, "fleet_repo": None, "repo": repo, "deployments": [], "files": None,
              "variables": None, "variables_note": None, "legacy_workflows": [], "problem": None, "next_step": None}
    fleet_repo, stop = _require(result)
    if stop:
        return stop
    doc, rd = _load_fleet(fleet_repo)
    if doc is None:
        return {**result, **_fleet_file_outcome(fleet_repo, rd)}
    names = sorted(doc) if not repo else fleet.deployments_of_repo(doc, repo)
    result["deployments"] = [{"name": n, "id": doc[n].get("id"), "repo": doc[n].get("repo"),
                              "project": doc[n].get("project"), "domain": doc[n].get("domain"),
                              "keys": sorted(fleet.manifest_keys(doc[n]))} for n in names]
    if verbose:
        result["files"] = _files_report(fleet_repo)
        report = _variables_report(fleet_repo)
        result["variables"], result["variables_note"] = report["variables"], report["note"]
        result["legacy_workflows"] = _legacy_workflows(fleet_repo)
        outdated = [p for p, f in result["files"].items() if f["current"] is False]
        if outdated:
            result["problem"] = (f"Not current with this gapp release in {fleet_repo}: {', '.join(outdated)}; "
                                 f"re-running `gapp ci init` from a machine whose `gh` can write to the repo updates them.")
            result["next_step"] = {"action": "ci init", "hint": f"gapp ci init {fleet_repo}"}
    if not result["deployments"] and result["next_step"] is None:
        result["next_step"] = {"action": "ci setup", "hint": "gapp ci setup <name> --repo <owner>/<repo> --project <project>"}
    return result


# --- what the fleet repo's workflows run on the runner ---


def runner_register(env: dict | None = None, out=print) -> int:
    """The register run: validate and write one change to the deployments file.

    Reads the dispatch inputs from the environment (OPERATION, NAME, REPO,
    PROJECT, CHANGES, SOURCE), the fleet repo's variables (ALLOWED_SOURCES,
    APP_ID, APP_KEY_SET) and FLEET_REPO, and prints the structured result
    the operator's command reads: `gapp-register: result=<code>` and one
    plain line, plus `name=`, `id=` and `project=` on success. The same
    fleet.py functions the operator's machine ran before dispatching decide
    here, against the live file, with the run's own token.
    """
    env = dict(os.environ if env is None else env)
    fleet_repo = env.get("FLEET_REPO") or env.get("GITHUB_REPOSITORY") or ""
    operation = (env.get("OPERATION") or "set").strip()
    name = (env.get("NAME") or "").strip()
    repo = (env.get("REPO") or "").strip()
    project = (env.get("PROJECT") or "").strip()
    source = (env.get("SOURCE") or "").strip()

    def refuse(code: str, message: str) -> int:
        out(f"{REGISTER_LOG_PREFIX} result={code}")
        out(f"{REGISTER_LOG_PREFIX} {message}")
        return 1

    try:
        changes = json.loads(env.get("CHANGES") or "{}")
        if not isinstance(changes, dict):
            raise ValueError
    except ValueError:
        return refuse("invalid-input", "the changes input is not a JSON object.")
    if operation not in ("set", "copy", "rename"):
        return refuse("invalid-input", f"'{operation}' is not an operation (set, copy or rename).")
    try:
        fleet.validate_name(name)
    except FleetRefusal as e:
        return refuse(e.code, e.message)

    rd = _read_file(fleet_repo, DEPLOYMENTS_FILE)
    if rd.absent:
        return refuse("data-file-missing", f"{DEPLOYMENTS_FILE} does not exist in this fleet repo; gapp ci init installs it.")
    if rd.unchecked:
        return refuse("data-file-unreadable", rd.note)
    try:
        doc = fleet.parse(rd.content)
    except FleetRefusal as e:
        return refuse(e.code, e.message)

    def owner_allowed(deployed_repo: str) -> bool:
        owner = deployed_repo.split("/")[0].lower()
        allowed = [o.strip().lower() for o in (env.get("ALLOWED_SOURCES") or "").split(",") if o.strip()]
        return owner in allowed

    def project_trusted(project_id: str) -> bool:
        rc, _, _ = _gh_api([f"repos/{fleet_repo}/environments/{project_id}"])
        return rc == 0

    try:
        if operation == "set":
            fleet.validate_repo(repo)
            fleet.validate_project(project)
            if not owner_allowed(repo):
                return refuse("source-owner-not-allowed", (
                    f"Apps owned by {repo.split('/')[0]} aren't allowed in this fleet; allow the owner with: "
                    f"gapp ci sources add {repo.split('/')[0]}"
                ))
            if not project_trusted(project):
                return refuse("project-not-trusted", (
                    f"This fleet repo is not yet trusted to deploy to GCP project {project} (no Environment named "
                    f"'{project}' here). Run: gapp ci project setup {project}"
                ))
            new_doc, action, entry = fleet.set_entry(doc, name, repo=repo, project=project, changes=changes)
            rc, out_vis, _ = _gh_api([f"repos/{repo}", "--jq", ".private"])
            if not (rc == 0 and out_vis.strip() == "false"):
                if not env.get("APP_ID") or env.get("APP_KEY_SET") != "true":
                    return refuse("repo-unreachable", (
                        f"{repo} is not readable with this run's token (private, or it does not exist), and the "
                        f"hosting app's {APP_ID_VAR} variable and {APP_KEY_SECRET} secret are not both set in this "
                        f"fleet repo. For a private repo, store them (docs/CI.md, Private repos); otherwise check "
                        f"the repo name."
                    ))
            what = f"{action} '{name}' -> {entry['repo']} on project '{entry['project']}'."
        elif operation == "copy":
            src = doc.get(source)
            if src is None:
                raise FleetRefusal("source-missing", f"'{source}' is not a deployment in this fleet, so there is nothing to copy.")
            if not owner_allowed(src.get("repo") or "/"):
                return refuse("source-owner-not-allowed", (
                    f"Apps owned by {(src.get('repo') or '/').split('/')[0]} aren't allowed in this fleet."
                ))
            new_doc, entry, _ = fleet.copy_entry(doc, name, source, project=project or None, changes=changes)
            if not project_trusted(entry["project"]):
                return refuse("project-not-trusted", (
                    f"This fleet repo is not yet trusted to deploy to GCP project {entry['project']} (no Environment "
                    f"named '{entry['project']}' here). Run: gapp ci project setup {entry['project']}"
                ))
            action = "added"
            what = f"added '{name}' as a copy of '{source}': {entry['repo']} on project '{entry['project']}'."
        else:
            new_doc = fleet.rename_entry(doc, source, name)
            entry = new_doc[name]
            action = "renamed"
            what = f"renamed '{source}' to '{name}'; its id '{entry.get('id')}' and resources are unchanged."
    except FleetRefusal as e:
        return refuse(e.code, e.message)

    if action == "unchanged":
        out(f"{REGISTER_LOG_PREFIX} result=unchanged")
        out(f"{REGISTER_LOG_PREFIX} name={name}")
        out(f"{REGISTER_LOG_PREFIX} id={entry.get('id')}")
        out(f"{REGISTER_LOG_PREFIX} project={entry.get('project')}")
        out(f"{REGISTER_LOG_PREFIX} '{name}' is already set up with these values; nothing to write.")
        return 0
    content = fleet.dump(new_doc)
    payload = {"message": f"{action.capitalize()} {name}", "content": base64.b64encode(content.encode()).decode(),
               "sha": rd.sha}
    rc, _, err = _gh_api(["-X", "PUT", f"repos/{fleet_repo}/contents/{DEPLOYMENTS_FILE}"], payload)
    if rc != 0:
        return refuse("write-rejected", (
            f"the write to {DEPLOYMENTS_FILE} was rejected: {_refused(err)}. Either the file changed during this run "
            f"(dispatch again) or a rule on the default branch blocks this workflow's token (give the workflow a "
            f"bypass, or make the change through a pull request)."
        ))
    out(f"{REGISTER_LOG_PREFIX} result={action}")
    out(f"{REGISTER_LOG_PREFIX} name={name}")
    out(f"{REGISTER_LOG_PREFIX} id={entry.get('id')}")
    out(f"{REGISTER_LOG_PREFIX} project={entry.get('project')}")
    out(f"{REGISTER_LOG_PREFIX} {what}")
    return 0


def runner_overlay(path: str, overlay_json: str | None, domain: str | None = None) -> dict:
    """The deploy run's overlay step: merge the deployment's gapp.yaml keys
    (a JSON object) over the checked-out gapp.yaml, or write them as the
    whole file when the repo has none. `domain` is the one-key form older
    callers pass. Returns the resulting manifest."""
    overlay = json.loads(overlay_json) if (overlay_json or "").strip() else {}
    if not isinstance(overlay, dict):
        raise RuntimeError("the overlay is not a JSON object")
    if domain:
        overlay["domain"] = domain
    base = {}
    if os.path.exists(path):
        with open(path) as f:
            base = yaml.safe_load(f) or {}
    merged = fleet.merge_manifest(base, overlay)
    with open(path, "w") as f:
        yaml.safe_dump(merged, f, sort_keys=False, default_flow_style=False)
    return merged


# --- WIF and service account (used only by `ci project setup`) ---

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


def _mark_project_hosted(project_id: str) -> str:
    """Label the project gapp=hosted, keeping its other labels. Only an Owner
    writes project labels; a deploy never does."""
    result = run_gcloud(["projects", "update", project_id, "--update-labels=gapp=hosted"],
                        capture_output=True, text=True)
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
    import importlib.metadata

    tag = f"v{importlib.metadata.version('gapp')}"
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
