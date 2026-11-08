"""gapp ci — the CI fleet system.

gapp has two fleet systems that do not talk to each other. The root
commands (`gapp setup`, `deploy`, `status`, ...) are the direct system:
they need a Google Cloud identity and their record is the GCP project
label. The `gapp ci` commands here are the CI system: they need GitHub
(write access to the CI repo, or just dispatch) and their record is
`gapp/solutions.yaml` in the operator repo plus the project's GitHub
Environment. Nothing in this module reads GCP, except `setup_ci_project`,
the one command that touches both, once per project.

Every command's requirements are fixed by its name, and every expected
failure comes back as a plain outcome with a next step: the result dicts
carry `ok` and, when `ok` is False, `next_step` with `action` and `hint`.
Nothing here raises for a condition the operator can act on.
"""

import base64
import json
import re
import shutil
import subprocess
import time
from importlib import resources

from gapp.admin.sdk.config import get_config_dir
from gapp.admin.sdk.util import run_gcloud, require_gcloud_account

import yaml


_CI_TOPIC = "gapp-ci"

# The hosting GitHub App that lets a run clone a private solution repo: an
# Actions variable and an Actions secret of the CI repo. Fixed names, like
# GITHUB_TOKEN: the credentials belong to the CI repo (one pair per
# operator), so nothing about a solution could want different ones.
APP_ID_VAR = "HOSTING_GITHUB_APP_ID"
APP_KEY_SECRET = "HOSTING_GITHUB_APP_PRIVATE_KEY"

# Comma-separated GitHub owners whose repos the register workflow accepts.
# Managed by `gapp ci sources`.
ALLOWED_SOURCES_VAR = "GAPP_ALLOWED_SOURCES"

# Per-project deploy identity, recorded in the CI repo as a GitHub
# Environment named after the project id, holding these two variables.
# Written by `gapp ci project setup`, read by every deploy.
ENV_WIF_PROVIDER_VAR = "GAPP_WIF_PROVIDER"
ENV_DEPLOY_SA_VAR = "GAPP_DEPLOY_SERVICE_ACCOUNT"

# The gapp-owned files in the CI repo.
DEPLOY_WORKFLOW = "gapp-deploy.yml"
REGISTER_WORKFLOW = "gapp-register.yml"
WORKFLOWS_DIR = ".github/workflows"
DEPLOY_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{DEPLOY_WORKFLOW}"
REGISTER_WORKFLOW_PATH = f"{WORKFLOWS_DIR}/{REGISTER_WORKFLOW}"
SOLUTIONS_FILE = "gapp/solutions.yaml"

SOLUTIONS_FILE_INITIAL = """\
# Solutions registered for CI deploys. gapp owns this file: the register
# workflow (gapp ci setup) writes it and the deploy workflow (gapp ci
# deploy) reads it. Each entry has name, repo (owner/name), project (the
# GCP project id; the GitHub Environment of the same name holds the deploy
# identity), and optionally domain and gapp_ref.
solutions: []
"""

UNCHECKED_NOTE = (
    "not checked from this machine (the GitHub Actions variables and secrets "
    "API is not reachable here); the register and deploy runs check it"
)

# The register workflow prefixes every line meant for the operator, and
# reports its outcome as `gapp-register: result=<code>`.
REGISTER_LOG_PREFIX = "gapp-register:"
REGISTER_SUCCESS_CODES = ("added", "updated", "unchanged")

# The two messages `ci setup` prints verbatim for the two one-time steps.
PROJECT_NOT_TRUSTED_MESSAGE = (
    "CI repo {ci_repo} is not yet trusted to deploy to GCP project {project}. One-time setup:\n"
    "\n"
    "1. Run `gapp ci project setup {project}`\n"
    "   - Requires gcloud signed in as an Owner of {project}\n"
    "   - Requires `gh` signed in as an admin of {ci_repo}\n"
    "2. Run this command again: {command}"
)
SOURCE_NOT_ALLOWED_MESSAGE = (
    "Source repos owned by {owner} are not yet allowed to deploy through CI repo {ci_repo}. One-time setup:\n"
    "\n"
    "1. Run `gapp ci sources add {owner}`\n"
    "   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo {ci_repo} --body <current-list>,{owner}\n"
    "   - Requires `gh` signed in with write access to {ci_repo}\n"
    "2. Run this command again: {command}"
)

PROJECT_SETUP_REQUIREMENTS = (
    "`gapp ci project setup <project>` requires gcloud signed in as an Owner of the "
    "project and `gh` signed in as an admin of the CI repo (its owner, for a personal repo)."
)


# --- outcomes ---


def _outcome(action: str, hint: str, **extra) -> dict:
    """A plain outcome: what stands in the way, and the exact next step."""
    result = {"ok": False, "next_step": {"action": action, "hint": hint}}
    result.update(extra)
    return result


def _gh_reason(stderr: str, ci_repo: str) -> str:
    """Translate a gh failure into words, without the raw HTTP error."""
    text = stderr or ""
    if "401" in text or "authentication" in text.lower() or "not logged" in text.lower():
        return "`gh` is not signed in on this machine"
    if "403" in text or "blocked" in text.lower() or "forbidden" in text.lower():
        return f"this machine's GitHub access is not allowed to do that in {ci_repo}"
    if "404" in text:
        return f"{ci_repo}, or the file or workflow asked for, was not found by this GitHub login"
    return "GitHub did not accept the request"


def _gh_missing_outcome() -> dict:
    return _outcome("install gh", (
        "The GitHub CLI (gh) is not installed on this machine, and every `gapp ci` command works "
        "through it. Install it, then sign in with `gh auth login`."
    ))


def _no_ci_repo_outcome() -> dict:
    return _outcome("ci init", (
        "No CI repo is configured on this machine. Run `gapp ci init <owner>/<repo>` once to "
        "designate the repo that holds gapp's deploy and register workflows."
    ))


# --- local config and repo resolution ---


def _load_ci_config() -> dict:
    """Load CI config from XDG config dir."""
    path = get_config_dir() / "ci.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _save_ci_config(config: dict) -> None:
    """Save CI config to XDG config dir."""
    path = get_config_dir() / "ci.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def _resolve_repo(repo: str) -> str:
    """Resolve repo arg to owner/name. If no owner, default to gh user."""
    if "/" in repo:
        return repo
    result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not determine GitHub user. Run 'gh auth login' first."
        )
    owner = result.stdout.strip()
    return f"{owner}/{repo}"


def _resolve_solution_repo(value: str) -> str:
    """Resolve an explicit solution-repo arg to ``owner/name``.

    Accepts ``owner/name``, a bare ``name`` (owner defaults to the
    authenticated gh user), or a full GitHub URL (HTTPS or SSH). gapp CI
    is GitHub-only, so any non-GitHub URL is rejected.
    """
    v = value.strip()
    m = re.search(r'github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$', v)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if "://" in v or "@" in v:
        raise RuntimeError(
            f"Unsupported repo '{value}'. gapp CI is GitHub-only — pass "
            f"owner/name, a bare name, or a github.com URL."
        )
    return _resolve_repo(v)


def _find_ci_repo_by_topic() -> str | None:
    """Find a repo tagged with gapp-ci topic for the authenticated user."""
    result = subprocess.run(
        ["gh", "search", "repos", "--topic", _CI_TOPIC, "--owner", "@me",
         "--json", "fullName"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    repos = json.loads(result.stdout)
    if repos:
        return repos[0]["fullName"]
    return None


def _resolve_ci_repo() -> str | None:
    """CI repo from local config, else from the GitHub topic, else None."""
    config = _load_ci_config()
    local_repo = config.get("repo")
    if local_repo:
        return local_repo
    try:
        return _find_ci_repo_by_topic()
    except Exception:
        return None


def get_ci_repo() -> str | None:
    """Get the CI repo name. Local config takes priority over remote."""
    return _resolve_ci_repo()


def _gh_available() -> bool:
    return shutil.which("gh") is not None


# --- GitHub API plumbing (everything goes through the gh CLI) ---


def _gh_api_json(args: list[str], payload: dict | None = None) -> tuple[int, str, str]:
    """Run `gh api` with an optional JSON body; returns (rc, stdout, stderr)."""
    cmd = ["gh", "api"] + args
    kwargs = {"capture_output": True, "text": True}
    if payload is not None:
        cmd += ["--input", "-"]
        kwargs["input"] = json.dumps(payload)
    res = subprocess.run(cmd, **kwargs)
    return res.returncode, res.stdout or "", res.stderr or ""


def _read_ci_file(ci_repo: str, path: str) -> tuple[str | None, str | None]:
    """Fetch a file of the CI repo through the Contents API.

    Returns (sha, content), or (None, None) when the file does not exist.
    """
    rc, out, _ = _gh_api_json([f"repos/{ci_repo}/contents/{path}"])
    if rc != 0 or not out.strip():
        return None, None
    data = json.loads(out)
    if not isinstance(data, dict):
        return None, None
    content = base64.b64decode(data.get("content", "")).decode()
    return data.get("sha"), content


def _list_ci_dir(ci_repo: str, path: str) -> list[str]:
    """Names of the files in a directory of the CI repo, or [] when absent."""
    rc, out, _ = _gh_api_json([f"repos/{ci_repo}/contents/{path}"])
    if rc != 0 or not out.strip():
        return []
    data = json.loads(out)
    if not isinstance(data, list):
        return []
    return [item.get("name") for item in data if item.get("type") == "file"]


def _write_ci_file(ci_repo: str, path: str, content: str, message: str) -> str:
    """Create or update a gapp-owned file in the CI repo via the Contents API.

    gapp is not a git client for the operator's repos: the file is generated
    configuration, so it is written as a config-store update. GitHub makes
    the commit server-side; nothing is cloned, committed, or pushed on the
    operator's machine, so local git hooks are neither triggered nor
    bypassed. Idempotent: an identical file is reported as "unchanged".

    Returns "created", "updated", or "unchanged".
    """
    sha, current = _read_ci_file(ci_repo, path)
    if current == content:
        return "unchanged"
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    rc, _, err = _gh_api_json(["-X", "PUT", f"repos/{ci_repo}/contents/{path}"], payload)
    if rc != 0:
        raise RuntimeError(f"Failed to write {path} to {ci_repo}: {err.strip()}")
    return "updated" if sha else "created"


def _repo_permission(ci_repo: str) -> tuple[bool | None, str]:
    """Whether the gh login is an admin of the CI repo: (admin, reason when unknown)."""
    rc, out, err = _gh_api_json([f"repos/{ci_repo}", "--jq", ".permissions.admin"])
    if rc != 0:
        return None, _gh_reason(err, ci_repo)
    value = out.strip().lower()
    if value in ("true", "false"):
        return value == "true", ""
    return None, "GitHub did not report this login's permission on the repo"


# --- Actions variables and secrets (presence only; GitHub never returns a secret) ---


def _actions_api_available(ci_repo: str) -> bool:
    """Can this machine read the CI repo's Actions variables and secrets?

    Some sessions reach GitHub through a proxy that refuses the Actions
    variables and secrets API while allowing contents reads and workflow
    dispatch. The capability is probed once, up front, so a check this
    machine cannot perform is reported as unchecked rather than as a
    failure or a missing value.
    """
    res = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/variables?per_page=1"],
        capture_output=True, text=True,
    )
    return res.returncode == 0


def _actions_item_present(ci_repo: str, kind: str, name: str) -> bool | None:
    """Whether an Actions variable or secret exists in the CI repo.

    `kind` is "variables" or "secrets". Returns True, False (404), or None
    when the check itself could not be performed from this machine.
    """
    res = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/{kind}/{name}"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        return True
    if "404" in (res.stderr or ""):
        return False
    return None


def _actions_variable_value(ci_repo: str, name: str) -> tuple[int, str | None, str]:
    """(rc, value or None when absent, stderr) of an Actions variable read."""
    rc, out, err = _gh_api_json([f"repos/{ci_repo}/actions/variables/{name}"])
    if rc != 0:
        return rc, None, err
    return 0, (json.loads(out).get("value") or "").strip(), ""


def _variables_report(ci_repo: str) -> dict:
    """Presence of the three operator-set values in the CI repo.

    `variables` maps each name to True, False, or None (unchecked). The
    hosting app's pair matters only for private solution repos; the source
    allowlist is needed before any solution can be registered. Never sets
    or overwrites them: the values are the operator's.
    """
    names = {ALLOWED_SOURCES_VAR: "variables", APP_ID_VAR: "variables", APP_KEY_SECRET: "secrets"}
    if not _actions_api_available(ci_repo):
        return {"variables": {n: None for n in names}, "checked": False, "note": UNCHECKED_NOTE}
    return {
        "variables": {n: _actions_item_present(ci_repo, kind, n) for n, kind in names.items()},
        "checked": True, "note": None,
    }


def _variables_next_step(ci_repo: str, report: dict) -> dict | None:
    """The commands for whichever operator-set values are missing."""
    if not report["checked"]:
        return None
    present = report["variables"]
    lines = []
    if present[ALLOWED_SOURCES_VAR] is not True:
        lines.append(
            f"No source owners are allowed yet in {ci_repo}: the register workflow accepts only "
            f"solution repos whose GitHub owner is listed. Allow each owner once:\n"
            f"  gapp ci sources add <owner>"
        )
    missing_app = [n for n in (APP_ID_VAR, APP_KEY_SECRET) if present[n] is not True]
    if missing_app:
        what = " and ".join(
            ("Actions variable " if n == APP_ID_VAR else "Actions secret ") + n for n in missing_app
        )
        lines.append(
            f"Not yet in {ci_repo}: {what}. Only private solution repos need them: CI clones "
            f"a private repo with a GitHub App installed on its owner. When you have one, store "
            f"its id and private key once:\n"
            f"  gh variable set {APP_ID_VAR} --repo {ci_repo} --body <app-id>\n"
            f"  gh secret set {APP_KEY_SECRET} --repo {ci_repo} < key.pem\n"
            f"See docs/CI.md, \"Private solution repos\"."
        )
    if not lines:
        return None
    return {"action": "configure variables", "hint": "\n".join(lines)}


# --- the per-project Environment record ---


def _environment_exists(ci_repo: str, project_id: str) -> bool:
    """Whether the CI repo has a GitHub Environment named after the project.

    Uses the repo environments endpoint, which is readable wherever the
    repo itself is, unlike the Actions variables API.
    """
    rc, out, _ = _gh_api_json([f"repos/{ci_repo}/environments/{project_id}"])
    return rc == 0 and bool(out.strip())


def _write_environment_record(ci_repo: str, project_id: str,
                              wif_provider: str, service_account: str) -> str:
    """Record a project's deploy identity in the CI repo.

    Creates the Environment named after the project id if needed and sets
    the two variables, creating or updating each only when its value
    differs. Returns "created", "updated", or "unchanged".
    """
    rc, _, err = _gh_api_json(["-X", "PUT", f"repos/{ci_repo}/environments/{project_id}"])
    if rc != 0:
        raise RuntimeError(f"create the Environment '{project_id}': {_gh_reason(err, ci_repo)}")
    status = "unchanged"
    for var, value in ((ENV_WIF_PROVIDER_VAR, wif_provider), (ENV_DEPLOY_SA_VAR, service_account)):
        endpoint = f"repos/{ci_repo}/environments/{project_id}/variables"
        rc, out, _ = _gh_api_json([f"{endpoint}/{var}"])
        if rc == 0 and out.strip():
            if (json.loads(out).get("value") or "").strip() == value:
                continue
            rc, _, err = _gh_api_json(["-X", "PATCH", f"{endpoint}/{var}"], {"name": var, "value": value})
            status = "updated" if status != "created" else status
        else:
            rc, _, err = _gh_api_json(["-X", "POST", endpoint], {"name": var, "value": value})
            status = "created"
        if rc != 0:
            raise RuntimeError(f"write the Environment variable {var} on '{project_id}': {_gh_reason(err, ci_repo)}")
    return status


def _environment_record_status(ci_repo: str, project_id: str) -> dict:
    """Existence of the Environment and presence of each variable, for `ci status`.

    `exists` comes from the repo environments endpoint. The two presence
    flags are None, with `checked` False, when the Actions variables API
    is not reachable from this machine.
    """
    exists = _environment_exists(ci_repo, project_id)
    out = {"exists": exists, "wif_provider_present": None, "service_account_present": None,
           "checked": False, "note": None}
    if not exists:
        out.update(wif_provider_present=False, service_account_present=False, checked=True)
        return out
    if not _actions_api_available(ci_repo):
        out["note"] = UNCHECKED_NOTE
        return out
    out["checked"] = True
    for key, var in (("wif_provider_present", ENV_WIF_PROVIDER_VAR), ("service_account_present", ENV_DEPLOY_SA_VAR)):
        rc, body, _ = _gh_api_json([f"repos/{ci_repo}/environments/{project_id}/variables/{var}"])
        out[key] = bool((json.loads(body).get("value") or "").strip()) if (rc == 0 and body.strip()) else False
    return out


# --- the gapp-owned files in the CI repo ---


def _render_template(name: str, gapp_repo: str, gapp_ref: str) -> str:
    """A workflow template shipped with gapp, pinned to a gapp repo and ref."""
    text = resources.files("gapp").joinpath("templates", "ci", name).read_text()
    return text.replace("__GAPP_REPO__", gapp_repo).replace("__GAPP_REF__", gapp_ref)


def _operator_files(gapp_repo: str, gapp_ref: str) -> dict[str, str]:
    """Path -> content of the two workflows `ci init` installs."""
    return {
        DEPLOY_WORKFLOW_PATH: _render_template(DEPLOY_WORKFLOW, gapp_repo, gapp_ref),
        REGISTER_WORKFLOW_PATH: _render_template(REGISTER_WORKFLOW, gapp_repo, gapp_ref),
    }


def _install_operator_files(ci_repo: str) -> dict[str, str]:
    """Install or update the generic workflows and create the data file.

    The workflows are written whenever the rendered content differs from
    what the repo holds (a new gapp release pins a new reusable-workflow
    ref). The data file is created when missing and never overwritten: the
    register workflow owns its content from then on. Returns path -> status.
    """
    gapp_repo = _get_gapp_repo()
    gapp_ref = _resolve_gapp_ref(gapp_repo)
    statuses = {}
    for path, content in _operator_files(gapp_repo, gapp_ref).items():
        statuses[path] = _write_ci_file(ci_repo, path, content, f"Install gapp workflow {path.rsplit('/', 1)[-1]}")
    _, existing = _read_ci_file(ci_repo, SOLUTIONS_FILE)
    if existing is None:
        statuses[SOLUTIONS_FILE] = _write_ci_file(ci_repo, SOLUTIONS_FILE, SOLUTIONS_FILE_INITIAL,
                                                  "Add the gapp solutions file")
    else:
        statuses[SOLUTIONS_FILE] = "unchanged"
    return statuses


def _files_report(ci_repo: str) -> dict:
    """Presence, and currency against this gapp's templates, of the three files."""
    report = {}
    try:
        gapp_repo = _get_gapp_repo()
        expected = _operator_files(gapp_repo, _resolve_gapp_ref(gapp_repo))
    except RuntimeError:
        expected = {}
    for path in (DEPLOY_WORKFLOW_PATH, REGISTER_WORKFLOW_PATH):
        _, content = _read_ci_file(ci_repo, path)
        report[path] = {"present": content is not None,
                        "current": (content == expected.get(path)) if (content is not None and path in expected) else None}
    _, data = _read_ci_file(ci_repo, SOLUTIONS_FILE)
    report[SOLUTIONS_FILE] = {"present": data is not None, "current": None}
    return report


def _load_solutions(ci_repo: str) -> list[dict] | None:
    """The entries of gapp/solutions.yaml in the CI repo, or None when the file is absent."""
    _, content = _read_ci_file(ci_repo, SOLUTIONS_FILE)
    if content is None:
        return None
    try:
        doc = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return []
    entries = doc.get("solutions") if isinstance(doc, dict) else None
    return [e for e in (entries or []) if isinstance(e, dict) and e.get("name")]


def _find_entry(entries: list[dict] | None, solution_name: str) -> dict | None:
    for entry in entries or []:
        if entry.get("name") == solution_name:
            return entry
    return None


def _legacy_workflows(ci_repo: str) -> list[str]:
    """Solution names still deployed by a per-solution caller workflow.

    Any workflow in the CI repo other than the two gapp-owned ones that
    calls gapp's reusable deploy workflow is one; `ci status` lists them so
    the operator can register each and delete the old file.
    """
    names = []
    for name in _list_ci_dir(ci_repo, WORKFLOWS_DIR):
        if name in (DEPLOY_WORKFLOW, REGISTER_WORKFLOW) or not name.endswith((".yml", ".yaml")):
            continue
        _, content = _read_ci_file(ci_repo, f"{WORKFLOWS_DIR}/{name}")
        if content and "/.github/workflows/deploy.yml@" in content:
            names.append(name.rsplit(".", 1)[0])
    return names


# --- ci init ---


def init_ci(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo and install gapp's files in it.

    1. Resolve repo to owner/name
    2. Write to XDG config (ci.yaml)
    3. Tag the repo with gapp-ci topic (unless local_only)
    4. Ensure only one repo has the topic
    5. Install or update the generic deploy and register workflows and
       create the solutions data file (unless local_only)
    6. Report whether the operator-set variables and secret exist

    Needs a GitHub API write (the workflow files). Returns dict describing
    what was done; `ok` is False with `next_step` when the files could not
    be installed from this machine.
    """
    if not local_only and not _gh_available():
        return {"repo": repo, "config_status": None, "topic_status": None, **_gh_missing_outcome()}

    full_name = _resolve_repo(repo)

    result = {
        "ok": True,
        "repo": full_name,
        "config_status": None,
        "topic_status": None,
        "next_step": None,
    }

    # Write to local config
    config = _load_ci_config()
    config["repo"] = full_name
    _save_ci_config(config)
    result["config_status"] = "saved"

    if local_only:
        result["topic_status"] = "skipped"
        return result

    # Check if another repo already has the topic
    existing = _find_ci_repo_by_topic()
    if existing and existing != full_name:
        return {**result, **_outcome("choose one CI repo", (
            f"Another repo already carries the {_CI_TOPIC} topic: {existing}. One CI repo per operator. "
            f"Remove the topic from that repo first, or run `gapp ci init {full_name} --local-only`."
        ))}

    if existing == full_name:
        result["topic_status"] = "already_set"
    else:
        # Ensure repo exists
        check = subprocess.run(
            ["gh", "repo", "view", full_name, "--json", "name"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            # Create it as private
            create = subprocess.run(
                ["gh", "repo", "create", full_name, "--private",
                 "--description", "gapp CI/CD deployment workflows"],
                capture_output=True,
                text=True,
            )
            if create.returncode != 0:
                return {**result, **_outcome("create the CI repo", (
                    f"{full_name} does not exist and could not be created from this machine: "
                    f"{_gh_reason(create.stderr, full_name)}. Create it on GitHub (private), or sign in "
                    f"with `gh auth login` as an account that can create it, then run `gapp ci init {full_name}` again."
                ))}
            result["repo_created"] = True

        # Add topic
        subprocess.run(
            ["gh", "repo", "edit", full_name, "--add-topic", _CI_TOPIC],
            capture_output=True,
            text=True,
        )
        result["topic_status"] = "added"

    # Install the gapp-owned files. A machine whose GitHub access refuses
    # API writes cannot; say so and name the files.
    try:
        result["files"] = _install_operator_files(full_name)
    except RuntimeError as e:
        result["files"] = None
        result.update(_outcome("install operator files", (
            f"gapp's files could not be written to {full_name} from this machine: "
            f"{_gh_reason(str(e), full_name)}. `gapp ci init` needs a `gh` login that can write to "
            f"the repo. Run `gapp ci init {full_name}` from a machine with one; it installs "
            f"{DEPLOY_WORKFLOW_PATH}, {REGISTER_WORKFLOW_PATH} and {SOLUTIONS_FILE}."
        )))

    report = _variables_report(full_name)
    result["variables"] = report["variables"]
    result["variables_checked"] = report["checked"]
    result["variables_note"] = report["note"]
    if result["ok"]:
        result["next_step"] = _variables_next_step(full_name, report)
    return result


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
    """Make the CI repo trusted to deploy to a GCP project. Once per project, idempotent.

    Creates or confirms the Workload Identity pool and provider and the
    `gapp-deploy` service account in the project, binds the pool to the
    CI repo, and records the deploy identity in the CI repo as the GitHub
    Environment named after the project (`GAPP_WIF_PROVIDER`,
    `GAPP_DEPLOY_SERVICE_ACCOUNT`). This is the only `gapp ci` command that
    touches GCP. Requires gcloud signed in as an Owner of the project and
    `gh` signed in as an admin of the CI repo; every failure says which.
    """
    project_id = (project or "").strip()
    result = {
        "ok": True, "project_id": project_id, "ci_repo": None,
        "wif_pool": None, "wif_provider": None, "service_account": None, "binding": None,
        "environment": project_id, "environment_record": None, "next_step": None,
    }
    if not project_id:
        return {**result, **_outcome("pass the project", "Name the GCP project: `gapp ci project setup <project>`.")}
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}
    ci_repo = get_ci_repo()
    if not ci_repo:
        return {**result, **_no_ci_repo_outcome()}
    result["ci_repo"] = ci_repo

    admin, reason = _repo_permission(ci_repo)
    if admin is not True:
        why = reason if admin is None else f"the signed-in `gh` account is not an admin of {ci_repo}"
        return {**result, **_outcome("sign in to gh as an admin", (
            f"Cannot record the project's deploy identity in {ci_repo}: {why}. "
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
         lambda: _ensure_wif_provider(project_id, ci_repo.split("/")[0])),
        ("service_account", "create or confirm the gapp-deploy service account", lambda: _ensure_deploy_sa(project_id)),
        ("binding", "bind the Workload Identity pool to the CI repo", lambda: _ensure_wif_binding(project_id, ci_repo)),
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
            ci_repo, project_id, _wif_provider_path(project_number), _deploy_sa_email(project_id),
        )
    except RuntimeError as e:
        return {**result, **_outcome("sign in to gh as an admin", (
            f"GCP is ready, but the deploy identity could not be recorded in {ci_repo}: could not {e}. "
            f"{PROJECT_SETUP_REQUIREMENTS} Then run `gapp ci project setup {project_id}` again; the GCP "
            f"steps are idempotent."
        ))}
    return result


# --- ci sources ---


def _sources_list(ci_repo: str) -> tuple[list[str] | None, dict | None]:
    """The allowed source owners, or (None, outcome) when they cannot be read."""
    rc, value, err = _actions_variable_value(ci_repo, ALLOWED_SOURCES_VAR)
    if rc != 0 and "404" in err:
        return [], None
    if rc != 0:
        return None, _outcome("sign in to gh with write access", (
            f"Could not read {ALLOWED_SOURCES_VAR} in {ci_repo}: {_gh_reason(err, ci_repo)}. "
            f"`gapp ci sources` needs `gh` signed in with write access to {ci_repo}."
        ))
    return [o.strip() for o in (value or "").split(",") if o.strip()], None


def _sources_write(ci_repo: str, owners: list[str], existed: bool) -> dict | None:
    """Write the allowlist; returns an outcome on refusal, None on success."""
    endpoint = f"repos/{ci_repo}/actions/variables"
    value = ",".join(owners)
    if not owners and existed:
        rc, _, err = _gh_api_json(["-X", "DELETE", f"{endpoint}/{ALLOWED_SOURCES_VAR}"])
    elif existed:
        rc, _, err = _gh_api_json(["-X", "PATCH", f"{endpoint}/{ALLOWED_SOURCES_VAR}"],
                                  {"name": ALLOWED_SOURCES_VAR, "value": value})
    else:
        rc, _, err = _gh_api_json(["-X", "POST", endpoint], {"name": ALLOWED_SOURCES_VAR, "value": value})
    if rc != 0:
        return _outcome("sign in to gh with write access", (
            f"Could not write {ALLOWED_SOURCES_VAR} in {ci_repo}: {_gh_reason(err, ci_repo)}. "
            f"`gapp ci sources` needs `gh` signed in with write access to {ci_repo}; sign in with "
            f"`gh auth login`, or set the variable from a machine that can:\n"
            f"  gh variable set {ALLOWED_SOURCES_VAR} --repo {ci_repo} --body {value or '<owners>'}"
        ))
    return None


def manage_sources(action: str, owner: str | None = None) -> dict:
    """List, add to, or remove from the owners allowed to deploy through the CI repo.

    The list is the CI repo's repository variable `GAPP_ALLOWED_SOURCES`
    (comma-separated GitHub owners); the register workflow refuses a
    solution repo whose owner is not on it. Needs `gh` with write access
    to the CI repo (read access for `list`).
    """
    result = {"ok": True, "action": action, "ci_repo": None, "owner": owner, "owners": None,
              "changed": False, "next_step": None}
    if action not in ("add", "remove", "list"):
        return {**result, **_outcome("choose an action", "Use `gapp ci sources add|remove|list [<owner>]`.")}
    if action != "list" and not (owner or "").strip():
        return {**result, **_outcome("pass the owner", f"Name the GitHub owner: `gapp ci sources {action} <owner>`.")}
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}
    ci_repo = get_ci_repo()
    if not ci_repo:
        return {**result, **_no_ci_repo_outcome()}
    result["ci_repo"] = ci_repo

    owners, outcome = _sources_list(ci_repo)
    if outcome:
        return {**result, **outcome}
    existed = _actions_item_present(ci_repo, "variables", ALLOWED_SOURCES_VAR) is True
    result["owners"] = list(owners)
    if action == "list":
        return result

    owner = owner.strip()
    present = any(o.lower() == owner.lower() for o in owners)
    if action == "add":
        if present:
            result["note"] = f"{owner} is already allowed to deploy through {ci_repo}."
            return result
        new_owners = owners + [owner]
    else:
        if not present:
            result["note"] = f"{owner} is not in the allowed sources of {ci_repo}; nothing to remove."
            return result
        new_owners = [o for o in owners if o.lower() != owner.lower()]
    refusal = _sources_write(ci_repo, new_owners, existed)
    if refusal:
        return {**result, **refusal}
    result["owners"] = new_owners
    result["changed"] = True
    result["note"] = (f"{owner} may now deploy through {ci_repo}." if action == "add"
                      else f"{owner} may no longer deploy through {ci_repo}.")
    return result


# --- workflow runs ---


def _dispatch(ci_repo: str, workflow_file: str, fields: dict[str, str]) -> str | None:
    """Dispatch a workflow; returns None, or the reason in words when refused."""
    cmd = ["gh", "workflow", "run", workflow_file, "--repo", ci_repo]
    for key, value in fields.items():
        cmd += ["-f", f"{key}={value}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        return _gh_reason(res.stderr, ci_repo)
    return None


def _latest_run_id(ci_repo: str, workflow_file: str) -> str | None:
    """The newest dispatch run of a workflow in the CI repo, once GitHub lists it."""
    for _ in range(5):
        time.sleep(2)
        res = subprocess.run(
            ["gh", "api",
             f"repos/{ci_repo}/actions/workflows/{workflow_file}/runs?event=workflow_dispatch&per_page=1",
             "--jq", ".workflow_runs[0].id"],
            capture_output=True, text=True,
        )
        if res.returncode == 0 and res.stdout.strip() and res.stdout.strip() != "null":
            return res.stdout.strip()
    return None


def _wait_for_run(ci_repo: str, run_id: str, timeout: int = 900) -> tuple[str | None, str | None]:
    """Poll a run until it completes; returns (status, conclusion)."""
    deadline = time.monotonic() + timeout
    status = conclusion = None
    while True:
        res = subprocess.run(
            ["gh", "api", f"repos/{ci_repo}/actions/runs/{run_id}", "--jq", ".status + \" \" + (.conclusion // \"\")"],
            capture_output=True, text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            status, _, conclusion = res.stdout.strip().partition(" ")
            conclusion = conclusion or None
            if status == "completed":
                return status, conclusion
        if time.monotonic() >= deadline:
            return status, conclusion
        time.sleep(5)


def _run_messages(ci_repo: str, run_id: str, prefix: str) -> list[str]:
    """The lines a run printed for the operator (those carrying `prefix`)."""
    res = subprocess.run(
        ["gh", "run", "view", run_id, "--repo", ci_repo, "--log"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return []
    messages = []
    for line in res.stdout.splitlines():
        if prefix in line:
            messages.append(line[line.index(prefix) + len(prefix):].strip())
    return messages


def _run_url(ci_repo: str, run_id: str | None) -> str | None:
    return f"https://github.com/{ci_repo}/actions/runs/{run_id}" if run_id else None


# --- ci setup ---


def setup_ci(solution: str | None = None, solution_repo: str | None = None, *,
             project: str | None = None,
             domain: str | None = None,
             watch: bool = True,
             command: str | None = None) -> dict:
    """Register a solution for CI deploys. Never uses gcloud.

    Dispatches the register workflow in the CI repo with the solution's
    name, repo, project and domain, waits for the run and reports its
    result. The run validates (the project's Environment exists, the
    repo's owner is allowed, the repo is reachable, no other project holds
    the solution) and writes the entry in ``gapp/solutions.yaml`` with its
    own token, so this machine needs only a `gh` that can dispatch.

    ``project`` is required: the CI system has no way to find a project
    without GCP, and it never asks GCP. A project the CI repo is not yet
    trusted for stops with the one-time `gapp ci project setup` message; a
    solution repo whose owner is not allowed stops with the one-time
    `gapp ci sources add` message. ``domain`` None keeps the entry's
    domain, "" removes it. ``command`` is the user's exact command line,
    repeated in those messages; the CLI passes it.
    """
    from gapp.admin.sdk.core import GappSDK

    project_id = (project or "").strip()
    result = {
        "ok": True,
        "solution": None, "solution_repo": None, "project_id": project_id or None,
        "ci_repo": None, "domain": None, "environment": project_id or None,
        "workflow": REGISTER_WORKFLOW, "dispatched": False, "run_id": None, "run_url": None,
        "conclusion": None, "registered": None, "result_code": None, "messages": [],
        "next_step": None,
    }
    if not project_id:
        return {**result, **_outcome("pass --project", (
            "The CI system needs the GCP project named: `gapp ci setup --project <project>`. It never "
            "looks the project up in GCP."
        ))}
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}
    ci_repo = get_ci_repo()
    if not ci_repo:
        return {**result, **_no_ci_repo_outcome()}
    result["ci_repo"] = ci_repo

    sdk = GappSDK()
    sol_ctx = sdk.resolve_solution(solution)
    if not sol_ctx or not sol_ctx.get("name"):
        return {**result, **_outcome("name the solution", (
            "No solution named and not inside a solution repo. Run from the solution's checkout, or "
            "pass `--solution <name> --solution-repo <owner>/<repo>`."
        ))}
    solution_name = sol_ctx["name"]
    result["solution"] = solution_name
    try:
        full_solution_repo = _resolve_solution_repo(solution_repo) if solution_repo else sdk.discover_github_repo(sol_ctx)
    except RuntimeError as e:
        return {**result, **_outcome("name the solution repo", str(e))}
    if not full_solution_repo:
        return {**result, **_outcome("name the solution repo", (
            f"Cannot tell which GitHub repo holds '{solution_name}'. Pass `--solution-repo <owner>/<repo>` "
            f"(or a github.com URL), or run from inside the solution repo's checkout so it is read from "
            f"`git remote get-url origin`."
        ))}
    result["solution_repo"] = full_solution_repo
    owner = full_solution_repo.split("/")[0]
    command_line = command or (
        f"gapp ci setup --project {project_id}"
        + (f" --solution {solution_name} --solution-repo {full_solution_repo}" if solution or solution_repo else "")
        + (f" --domain {domain}" if domain else "")
    )

    _, register = _read_ci_file(ci_repo, REGISTER_WORKFLOW_PATH)
    if register is None:
        return {**result, **_outcome("ci init", (
            f"{ci_repo} has no {REGISTER_WORKFLOW_PATH}, so there is nothing to dispatch. Run "
            f"`gapp ci init {ci_repo}` from a machine whose `gh` can write to the repo; it installs "
            f"gapp's workflows and the solutions file."
        ))}

    if not _environment_exists(ci_repo, project_id):
        return {**result, **_outcome("ci project setup", PROJECT_NOT_TRUSTED_MESSAGE.format(
            ci_repo=ci_repo, project=project_id, command=command_line))}

    entries = _load_solutions(ci_repo)
    existing = _find_entry(entries, solution_name)
    if existing and existing.get("project") and existing["project"] != project_id:
        return {**result, **_outcome("remove the old entry", (
            f"'{solution_name}' is already registered in {ci_repo} on project '{existing['project']}', "
            f"and this run targets '{project_id}'. A solution deploys to one project per CI repo. To "
            f"move it, remove its entry from {SOLUTIONS_FILE} first, then run this command again: "
            f"{command_line}"
        ))}
    effective_domain = ((existing or {}).get("domain") or None) if domain is None else (domain.strip() or None)
    result["domain"] = effective_domain

    refused = _dispatch(ci_repo, REGISTER_WORKFLOW, {
        "solution": solution_name,
        "solution-repo": full_solution_repo,
        "project": project_id,
        "domain": effective_domain or "",
    })
    if refused:
        return {**result, **_outcome("dispatch the register workflow", (
            f"The register workflow could not be dispatched in {ci_repo}: {refused}. `gapp ci setup` needs "
            f"a `gh` signed in with access to dispatch workflows in {ci_repo} (`gh auth login`), then run "
            f"this command again: {command_line}"
        ))}
    result["dispatched"] = True
    result["run_id"] = _latest_run_id(ci_repo, REGISTER_WORKFLOW)
    result["run_url"] = _run_url(ci_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Dispatched {REGISTER_WORKFLOW} in {ci_repo}, but its run could not be found yet. Check the "
            f"repo's Actions tab for the register run, then `gapp ci status`."
        ))}
    if not watch:
        return result

    status, conclusion = _wait_for_run(ci_repo, result["run_id"])
    result["conclusion"] = conclusion
    result["messages"] = _run_messages(ci_repo, result["run_id"], REGISTER_LOG_PREFIX)
    codes = [m.split("=", 1)[1].strip() for m in result["messages"] if m.startswith("result=")]
    result["result_code"] = codes[-1] if codes else None
    texts = [m for m in result["messages"] if not m.startswith("result=")]
    if status != "completed":
        return {**result, **_outcome("check the run", (
            f"The register run did not finish within the wait: {result['run_url']}. Check it there; when "
            f"it has registered '{solution_name}', `gapp ci deploy` works. Otherwise run this command again: "
            f"{command_line}"
        ))}
    if result["result_code"] in REGISTER_SUCCESS_CODES and conclusion == "success":
        result["registered"] = True
        return result
    result["registered"] = False
    if result["result_code"] == "source-owner-not-allowed":
        return {**result, **_outcome("ci sources add", SOURCE_NOT_ALLOWED_MESSAGE.format(
            owner=owner, ci_repo=ci_repo, command=command_line))}
    if result["result_code"] == "project-not-trusted":
        return {**result, **_outcome("ci project setup", PROJECT_NOT_TRUSTED_MESSAGE.format(
            ci_repo=ci_repo, project=project_id, command=command_line))}
    said = " ".join(texts) if texts else "it ended with no message"
    return {**result, **_outcome("fix and run ci setup again", (
        f"The register run refused to register '{solution_name}': {said} ({result['run_url']}). Fix what it "
        f"names, then run this command again: {command_line}"
    ))}


# --- ci status ---


def get_ci_status(solution: str | None = None) -> dict:
    """The CI system's complete report. Never touches GCP.

    Returns:
        repo: owner/name of the CI repo, or None if gapp_ci_init has not
              been run.
        files: for each of the deploy workflow, the register workflow and
              the solutions file: present, and (workflows) current against
              this gapp's templates.
        variables: presence of GAPP_ALLOWED_SOURCES, HOSTING_GITHUB_APP_ID
              and HOSTING_GITHUB_APP_PRIVATE_KEY (True/False, or None when
              the Actions API is not readable from this machine).
        solutions: every registered entry with name, repo, project, domain,
              gapp_ref and whether the project's Environment exists.
        environments: project id -> Environment exists.
        legacy_workflows: solutions still deployed by a per-solution
              workflow in the CI repo.
        solution, registered: the current solution and whether it has an
              entry (None when no solution resolves); environment and
              environment_record describe its project's Environment.
        next_step: {action, hint} naming the gap, or None when
              `gapp ci deploy` can run.
    """
    from gapp.admin.sdk.core import GappSDK

    result = {
        "ok": True,
        "repo": None,
        "files": None,
        "variables": None,
        "variables_checked": None,
        "variables_note": None,
        "solutions": [],
        "environments": {},
        "legacy_workflows": [],
        "solution": None,
        "registered": None,
        "solution_repo": None,
        "domain": None,
        "gapp_ref": None,
        "environment": None,
        "environment_record": None,
        "next_step": None,
    }
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}
    result["repo"] = _resolve_ci_repo()
    if not result["repo"]:
        return {**result, **_no_ci_repo_outcome()}
    ci_repo = result["repo"]

    result["files"] = _files_report(ci_repo)
    entries = _load_solutions(ci_repo)
    report = _variables_report(ci_repo)
    result["variables"] = report["variables"]
    result["variables_checked"] = report["checked"]
    result["variables_note"] = report["note"]
    result["legacy_workflows"] = _legacy_workflows(ci_repo)

    for entry in entries or []:
        project = entry.get("project")
        if project and project not in result["environments"]:
            result["environments"][project] = _environment_exists(ci_repo, project)
        result["solutions"].append({
            "name": entry.get("name"), "repo": entry.get("repo"), "project": project,
            "domain": entry.get("domain"), "gapp_ref": entry.get("gapp_ref"),
            "environment_exists": result["environments"].get(project, False),
        })

    def stop(action, hint):
        result["ok"] = False
        result["next_step"] = {"action": action, "hint": hint}
        return result

    missing = [p for p, f in result["files"].items() if not f["present"]]
    outdated = [p for p, f in result["files"].items() if f["current"] is False]
    if missing:
        return stop("ci init", (
            f"Missing in {ci_repo}: {', '.join(missing)}. Run `gapp ci init {ci_repo}` from a machine "
            f"whose `gh` can write to the repo."
        ))
    if outdated:
        result["next_step"] = {"action": "ci init", "hint": (
            f"Not current with this gapp release in {ci_repo}: {', '.join(outdated)}. Run `gapp ci init "
            f"{ci_repo}` from a machine whose `gh` can write to the repo to update them."
        )}
    elif report["checked"] and report["variables"][ALLOWED_SOURCES_VAR] is not True:
        result["next_step"] = _variables_next_step(ci_repo, report)

    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        return result
    solution_name = ctx["name"]
    result["solution"] = solution_name
    entry = _find_entry(entries, solution_name)
    result["registered"] = entry is not None
    if entry is None:
        if result["next_step"] is None:
            hint = f"'{solution_name}' is not registered in {ci_repo}. Run `gapp ci setup --project <project>`"
            if solution_name in result["legacy_workflows"]:
                hint += (f"; it still has its own workflow {WORKFLOWS_DIR}/{solution_name}.yml, which runs "
                         f"from the Actions tab and can be deleted once the solution is registered")
            result["next_step"] = {"action": "ci setup", "hint": hint + "."}
        return result

    result["solution_repo"] = entry.get("repo")
    result["domain"] = entry.get("domain")
    result["gapp_ref"] = entry.get("gapp_ref")
    result["environment"] = entry.get("project")
    record = _environment_record_status(ci_repo, entry["project"]) if entry.get("project") else None
    result["environment_record"] = record
    if result["next_step"] is None and record is not None:
        if not record["exists"]:
            result["next_step"] = {"action": "ci project setup", "hint": (
                f"'{solution_name}' is registered on project '{entry['project']}', but {ci_repo} has no "
                f"Environment of that name, so a deploy cannot authenticate. Run `gapp ci project setup "
                f"{entry['project']}` (gcloud as an Owner of the project, `gh` as an admin of {ci_repo})."
            )}
        elif record["checked"] and not (record["wif_provider_present"] and record["service_account_present"]):
            missing_vars = [v for v, ok in ((ENV_WIF_PROVIDER_VAR, record["wif_provider_present"]),
                                            (ENV_DEPLOY_SA_VAR, record["service_account_present"])) if not ok]
            result["next_step"] = {"action": "ci project setup", "hint": (
                f"The Environment '{entry['project']}' in {ci_repo} is missing {', '.join(missing_vars)}. "
                f"Run `gapp ci project setup {entry['project']}` to record the deploy identity again."
            )}
    return result


# --- ci deploy ---


def deploy_ci(solution: str | None = None, *, ref: str = "main", watch: bool = True) -> dict:
    """Deploy a registered solution through the CI repo's deploy workflow.

    Dispatches `gapp-deploy.yml` with the solution's name and the ref and,
    when `watch` is True (default), streams the run and reports its
    conclusion. Needs only a `gh` that can dispatch workflows in the CI
    repo. A solution with no entry in `gapp/solutions.yaml` is not
    deployed; the outcome names `gapp ci setup`.
    """
    from gapp.admin.sdk.core import GappSDK

    result = {
        "ok": True, "solution": None, "ci_repo": None, "workflow": DEPLOY_WORKFLOW, "ref": ref,
        "run_id": None, "run_url": None, "watched": False, "conclusion": None, "next_step": None,
    }
    if not _gh_available():
        return {**result, **_gh_missing_outcome()}
    ci_repo = get_ci_repo()
    if not ci_repo:
        return {**result, **_no_ci_repo_outcome()}
    result["ci_repo"] = ci_repo

    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        if not solution:
            return {**result, **_outcome("name the solution", (
                "No solution named and not inside a solution repo. Run `gapp ci deploy --solution <name>`."
            ))}
        ctx = {"name": solution}
    solution_name = ctx["name"]
    result["solution"] = solution_name

    entry = _find_entry(_load_solutions(ci_repo), solution_name)
    if entry is None:
        return {**result, **_outcome("ci setup", (
            f"'{solution_name}' is not registered in {ci_repo} ({SOLUTIONS_FILE}), so there is nothing to "
            f"deploy. Register it first: `gapp ci setup --solution {solution_name} --solution-repo "
            f"<owner>/<repo> --project <project>`."
        ))}
    _, deploy = _read_ci_file(ci_repo, DEPLOY_WORKFLOW_PATH)
    if deploy is None:
        return {**result, **_outcome("ci init", (
            f"{ci_repo} has no {DEPLOY_WORKFLOW_PATH}. Run `gapp ci init {ci_repo}` from a machine whose "
            f"`gh` can write to the repo to install it."
        ))}

    refused = _dispatch(ci_repo, DEPLOY_WORKFLOW, {"solution": solution_name, "ref": ref})
    if refused:
        return {**result, **_outcome("dispatch the deploy workflow", (
            f"The deploy workflow could not be dispatched in {ci_repo}: {refused}. `gapp ci deploy` needs a "
            f"`gh` signed in with access to dispatch workflows in {ci_repo} (`gh auth login`)."
        ))}
    result["run_id"] = _latest_run_id(ci_repo, DEPLOY_WORKFLOW)
    result["run_url"] = _run_url(ci_repo, result["run_id"])
    if not result["run_id"]:
        return {**result, **_outcome("check the run", (
            f"Dispatched {DEPLOY_WORKFLOW} in {ci_repo} for '{solution_name}', but its run could not be found "
            f"yet. Check the repo's Actions tab."
        ))}
    if not watch:
        return result

    subprocess.run(["gh", "run", "watch", result["run_id"], "--repo", ci_repo])
    result["watched"] = True
    conclusion_result = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/runs/{result['run_id']}", "--jq", ".conclusion"],
        capture_output=True, text=True,
    )
    if conclusion_result.returncode == 0:
        result["conclusion"] = conclusion_result.stdout.strip() or None
    if result["conclusion"] != "success":
        return {**result, **_outcome("read the run log", (
            f"The deploy run for '{solution_name}' ended with '{result['conclusion'] or 'no conclusion'}': "
            f"{result['run_url']}. Its first steps name a missing credential or Environment variable in "
            f"words; the deploy step's log has gapp's own message otherwise."
        ))}
    return result


def trigger_ci(solution: str | None = None, *, ref: str = "main", watch: bool = True) -> dict:
    """Former name of `deploy_ci`; kept for one release."""
    return deploy_ci(solution, ref=ref, watch=watch)


def watch_ci(run_id: str) -> dict:
    """Watch a CI run to completion.

    Blocks and streams status until the run completes.
    """
    ci_repo = get_ci_repo()
    if not ci_repo:
        return {"run_id": run_id, "ci_repo": None, "conclusion": None, **_no_ci_repo_outcome()}

    subprocess.run(["gh", "run", "watch", run_id, "--repo", ci_repo])

    result = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/runs/{run_id}",
         "--jq", ".conclusion"],
        capture_output=True, text=True,
    )
    conclusion = result.stdout.strip() if result.returncode == 0 else None

    return {
        "ok": True,
        "run_id": run_id,
        "ci_repo": ci_repo,
        "conclusion": conclusion,
    }


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
    """Get the numeric project number from a project ID."""
    result = run_gcloud(
        ["projects", "describe", project_id,
         "--format", "value(projectNumber)"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _ensure_wif_pool(project_id: str) -> str:
    """Create WIF pool if it doesn't exist. Returns pool name."""
    check = run_gcloud(
        ["iam", "workload-identity-pools", "describe", _WIF_POOL_ID,
         "--project", project_id, "--location", "global"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    result = run_gcloud(
        ["iam", "workload-identity-pools", "create", _WIF_POOL_ID,
         "--project", project_id, "--location", "global",
         "--display-name", "GitHub Actions"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "created"


def _ensure_wif_provider(project_id: str, github_owner: str) -> str:
    """Create WIF OIDC provider for GitHub if it doesn't exist."""
    check = run_gcloud(
        ["iam", "workload-identity-pools", "providers", "describe",
         _WIF_PROVIDER_ID,
         "--project", project_id, "--location", "global",
         "--workload-identity-pool", _WIF_POOL_ID],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create — restrict to repos owned by the operator's GitHub account
    result = run_gcloud(
        ["iam", "workload-identity-pools", "providers", "create-oidc",
         _WIF_PROVIDER_ID,
         "--project", project_id, "--location", "global",
         "--workload-identity-pool", _WIF_POOL_ID,
         "--issuer-uri", "https://token.actions.githubusercontent.com",
         "--attribute-mapping",
         "google.subject=assertion.sub,"
         "attribute.actor=assertion.actor,"
         "attribute.repository=assertion.repository,"
         "attribute.repository_owner=assertion.repository_owner",
         "--attribute-condition",
         f"assertion.repository_owner == '{github_owner}'",
         ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "created"


def _ensure_deploy_sa(project_id: str) -> str:
    """Create deploy service account if it doesn't exist."""
    sa_email = _deploy_sa_email(project_id)

    check = run_gcloud(
        ["iam", "service-accounts", "describe", sa_email,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    status = "exists"
    if check.returncode != 0:
        result = run_gcloud(
            ["iam", "service-accounts", "create", _DEPLOY_SA_NAME,
             "--project", project_id,
             "--display-name", "gapp CI/CD deploy"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
        status = "created"

    # Always ensure roles (idempotent — add-iam-policy-binding is a no-op if already bound)
    for role in _DEPLOY_SA_ROLES:
        res = run_gcloud(
            ["projects", "add-iam-policy-binding", project_id,
             "--member", f"serviceAccount:{sa_email}",
             "--role", role,
             "--condition", "None"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip())

    return status


def _ensure_wif_binding(project_id: str, ci_repo: str) -> str:
    """Add IAM binding allowing CI repo to impersonate deploy SA."""
    sa_email = _deploy_sa_email(project_id)
    project_number = _get_project_number(project_id)
    member = (
        f"principalSet://iam.googleapis.com/"
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/{_WIF_POOL_ID}/"
        f"attribute.repository/{ci_repo}"
    )

    result = run_gcloud(
        ["iam", "service-accounts", "add-iam-policy-binding",
         sa_email,
         "--project", project_id,
         "--role", "roles/iam.workloadIdentityUser",
         "--member", member],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return "set"


def _wif_provider_path(project_number: str) -> str:
    return (
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/{_WIF_POOL_ID}/"
        f"providers/{_WIF_PROVIDER_ID}"
    )


def _deploy_sa_email(project_id: str) -> str:
    return f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"


# --- the gapp repo and ref the installed workflows pin ---


def _resolve_gapp_ref(gapp_repo: str) -> str:
    """Pin the reusable workflow to the installed gapp version's release tag.

    gapp tags every release ``vX.Y.Z`` (see CONTRIBUTING), so the installed
    workflows reference ``deploy.yml@vX.Y.Z`` — readable and tied to the
    gapp the operator actually has installed. Falls back to the current
    ``main`` HEAD only when that tag isn't published yet (e.g. an unreleased
    dev build), so `ci init` from a contributor checkout still works.
    """
    import importlib.metadata

    tag = f"v{importlib.metadata.version('gapp')}"
    exists = subprocess.run(
        ["gh", "api", f"repos/{gapp_repo}/git/ref/tags/{tag}"],
        capture_output=True, text=True,
    )
    if exists.returncode == 0:
        return tag
    head = subprocess.run(
        ["gh", "api", f"repos/{gapp_repo}/commits/HEAD", "--jq", ".sha"],
        capture_output=True, text=True,
    )
    fallback = head.stdout.strip() if head.returncode == 0 else "main"
    print(f"  ⚠ gapp tag {tag} not published on {gapp_repo}; pinning CI to {fallback}.")
    return fallback


def _get_gapp_repo() -> str:
    """Determine the gapp repo owner/name for workflow references.

    Reads from the installed package's Project-URL metadata, which is
    set via [project.urls] in pyproject.toml.
    """
    import importlib.metadata
    try:
        urls = importlib.metadata.metadata("gapp").get_all("Project-URL") or []
        for entry in urls:
            # Format is "Label, https://github.com/owner/repo"
            url = entry.split(",", 1)[-1].strip()
            if "github.com" in url:
                parts = url.split("github.com/")[-1].strip("/").split("/")
                if len(parts) >= 2:
                    return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass

    raise RuntimeError(
        "Could not determine the gapp repo. Ensure gapp is installed from a known GitHub source\n"
        "  and that [project.urls] Repository is set in pyproject.toml."
    )
