"""gapp ci — CI/CD automation for gapp solutions.

The operator's CI repo holds three gapp-owned files, installed by `ci init`:
a generic deploy workflow, a register workflow, and the data file
`gapp/solutions.yaml` that maps each solution to its repo, project and
domain. `ci setup` registers a solution by dispatching the register
workflow, which validates and writes the entry with its own token, so the
machine running `ci setup` needs neither gcloud nor a GitHub API write.
`ci trigger` dispatches the deploy workflow with a solution name. The only
step that needs gcloud is the first `ci setup` on a project, which creates
the project's Workload Identity pool, provider and deploy service account
and records them in the CI repo as a GitHub Environment named after the
project id.
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
ALLOWED_OWNERS_VAR = "GAPP_ALLOWED_OWNERS"

# Per-project deploy identity, recorded in the CI repo as a GitHub
# Environment named after the project id, holding these two variables.
# Written by the first `ci setup` on a project, read by every deploy.
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
# trigger) reads it. Each entry has name, repo (owner/name), project (the
# GCP project id; the GitHub Environment of the same name holds the deploy
# identity), and optionally domain and gapp_ref.
solutions: []
"""

UNCHECKED_NOTE = (
    "not checked from this machine (the GitHub Actions variables and secrets "
    "API is not reachable here); the register and deploy runs check it"
)

# The register workflow prefixes every line meant for the operator.
REGISTER_LOG_PREFIX = "gapp-register:"


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


def _is_repo_private(repo: str) -> bool:
    """Whether a GitHub repo is private, from the API (`.private`)."""
    res = subprocess.run(
        ["gh", "api", f"repos/{repo}", "--jq", ".private"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Could not read the visibility of {repo}: {res.stderr.strip()}\n"
            f"  The gh login needs read access to the solution repo."
        )
    return res.stdout.strip().lower() == "true"


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


# --- Actions variables and secrets (presence only; GitHub never returns a secret) ---


def _actions_api_available(ci_repo: str) -> bool:
    """Can this machine read the CI repo's Actions variables and secrets?

    Some sessions reach GitHub through a proxy that refuses the Actions
    variables and secrets API while allowing contents reads and workflow
    dispatch. The capability is probed once, up front, like gcloud is, so a
    check this machine cannot perform is reported as unchecked rather than
    as a failure or a missing value.
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


def _actions_variable_value(ci_repo: str, name: str) -> str | None:
    """The value of an Actions variable, or None when absent or unreadable."""
    rc, out, _ = _gh_api_json([f"repos/{ci_repo}/actions/variables/{name}"])
    if rc != 0 or not out.strip():
        return None
    return (json.loads(out).get("value") or "").strip() or None


def _variables_report(ci_repo: str) -> dict:
    """Presence of the three operator-set values in the CI repo.

    `variables` maps each name to True, False, or None (unchecked). The
    hosting app's pair matters only for private solution repos; the owner
    allowlist is needed before any solution can be registered. Never sets
    or overwrites them: the values are the operator's.
    """
    names = {ALLOWED_OWNERS_VAR: "variables", APP_ID_VAR: "variables", APP_KEY_SECRET: "secrets"}
    if not _actions_api_available(ci_repo):
        return {"variables": {n: None for n in names}, "checked": False, "note": UNCHECKED_NOTE}
    return {
        "variables": {n: _actions_item_present(ci_repo, kind, n) for n, kind in names.items()},
        "checked": True, "note": None,
    }


def _variables_next_step(ci_repo: str, report: dict) -> dict | None:
    """The `gh` commands for whichever operator-set values are missing."""
    if not report["checked"]:
        return None
    present = report["variables"]
    lines = []
    if present[ALLOWED_OWNERS_VAR] is not True:
        lines.append(
            f"Repository variable {ALLOWED_OWNERS_VAR} is not set in {ci_repo}. The register "
            f"workflow accepts only solution repos whose owner it lists (comma-separated):\n"
            f"  gh variable set {ALLOWED_OWNERS_VAR} --repo {ci_repo} --body <owner1,owner2>"
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


def _hosting_app_presence(ci_repo: str) -> dict:
    """Presence of the hosting app's variable and secret, or unchecked."""
    if not _actions_api_available(ci_repo):
        return {
            "app_id_var": APP_ID_VAR, "app_key_secret": APP_KEY_SECRET,
            "app_id_present": None, "private_key_present": None,
            "checked": False, "note": UNCHECKED_NOTE,
        }
    return {
        "app_id_var": APP_ID_VAR, "app_key_secret": APP_KEY_SECRET,
        "app_id_present": _actions_item_present(ci_repo, "variables", APP_ID_VAR),
        "private_key_present": _actions_item_present(ci_repo, "secrets", APP_KEY_SECRET),
        "checked": True, "note": None,
    }


def _hosting_app_next_step(ci_repo: str, solution_repo: str, creds: dict) -> dict | None:
    """For a private solution repo: the missing credential, if known."""
    if not creds["checked"]:
        return None
    missing = []
    if creds["app_id_present"] is not True:
        missing.append(f"Actions variable {APP_ID_VAR}")
    if creds["private_key_present"] is not True:
        missing.append(f"Actions secret {APP_KEY_SECRET}")
    if not missing:
        return None
    return {
        "action": "configure hosting app",
        "hint": (
            f"{solution_repo} is private; CI clones it with a GitHub App installed on "
            f"its owner. Missing in {ci_repo}: {', '.join(missing)}. Store the app's id and "
            f"private key there:\n"
            f"  gh variable set {APP_ID_VAR} --repo {ci_repo} --body <app-id>\n"
            f"  gh secret set {APP_KEY_SECRET} --repo {ci_repo} < key.pem\n"
            f"See docs/CI.md, \"Private solution repos\"."
        ),
    }


# --- the per-project Environment record ---


def _environment_exists(ci_repo: str, project_id: str) -> bool:
    """Whether the CI repo has a GitHub Environment named after the project.

    Uses the repo environments endpoint, which is readable wherever the
    repo itself is, unlike the Actions variables API.
    """
    rc, out, _ = _gh_api_json([f"repos/{ci_repo}/environments/{project_id}"])
    return rc == 0 and bool(out.strip())


def _read_environment_record(ci_repo: str, project_id: str) -> dict | None:
    """The deploy identity recorded for a project in the CI repo.

    The record is the GitHub Environment named after the project id. Its
    existence is the check (repo environments endpoint). When the Actions
    variables API is readable from this machine, the two variables are
    read too and an incomplete record is reported as None so the gcloud
    path can repair it; when it is not, the record is trusted as written
    and the values come back as None with `checked` False.
    """
    if not _environment_exists(ci_repo, project_id):
        return None
    if not _actions_api_available(ci_repo):
        return {"wif_provider": None, "service_account": None,
                "checked": False, "note": UNCHECKED_NOTE}
    values = {}
    for var in (ENV_WIF_PROVIDER_VAR, ENV_DEPLOY_SA_VAR):
        rc, out, _ = _gh_api_json([f"repos/{ci_repo}/environments/{project_id}/variables/{var}"])
        if rc != 0 or not out.strip():
            return None
        value = (json.loads(out).get("value") or "").strip()
        if not value:
            return None
        values[var] = value
    return {
        "wif_provider": values[ENV_WIF_PROVIDER_VAR],
        "service_account": values[ENV_DEPLOY_SA_VAR],
        "checked": True, "note": None,
    }


def _write_environment_record(ci_repo: str, project_id: str,
                              wif_provider: str, service_account: str) -> str:
    """Record a project's deploy identity in the CI repo.

    Creates the Environment named after the project id if needed and sets
    the two variables, creating or updating each only when its value
    differs. Returns "created", "updated", or "unchanged".
    """
    rc, _, err = _gh_api_json(["-X", "PUT", f"repos/{ci_repo}/environments/{project_id}"])
    if rc != 0:
        raise RuntimeError(
            f"Could not create the GitHub Environment '{project_id}' in {ci_repo}: {err.strip()}"
        )
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
            raise RuntimeError(
                f"Could not write Environment variable {var} on '{project_id}' in {ci_repo}: {err.strip()}"
            )
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

    Returns dict describing what was done.
    """
    full_name = _resolve_repo(repo)

    result = {
        "repo": full_name,
        "config_status": None,
        "topic_status": None,
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
        raise RuntimeError(
            f"Another repo already has the {_CI_TOPIC} topic: {existing}\n"
            f"  Remove the topic from that repo first, or use --local-only."
        )

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
                raise RuntimeError(
                    f"Failed to create repo {full_name}: {create.stderr.strip()}"
                )
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
        result["next_step"] = None
    except RuntimeError as e:
        result["files"] = None
        result["next_step"] = {
            "action": "install operator files",
            "hint": (
                f"The GitHub API refused to write gapp's files to {full_name} from this machine "
                f"({str(e).splitlines()[0]}). Run `gapp ci init {full_name}` from a machine whose "
                f"gh login can write to the repo; it installs {DEPLOY_WORKFLOW_PATH}, "
                f"{REGISTER_WORKFLOW_PATH} and {SOLUTIONS_FILE}."
            ),
        }

    report = _variables_report(full_name)
    result["variables"] = report["variables"]
    result["variables_checked"] = report["checked"]
    result["variables_note"] = report["note"]
    if result["next_step"] is None:
        result["next_step"] = _variables_next_step(full_name, report)
    return result


# --- workflow runs ---


def _dispatch(ci_repo: str, workflow_file: str, fields: dict[str, str]) -> None:
    cmd = ["gh", "workflow", "run", workflow_file, "--repo", ci_repo]
    for key, value in fields.items():
        cmd += ["-f", f"{key}={value}"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"Failed to dispatch {workflow_file} in {ci_repo}:\n  {res.stderr.strip()}"
        )


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


# --- ci setup ---


def setup_ci(solution: str | None = None, solution_repo: str | None = None, *,
             domain: str | None = None,
             project: str | None = None,
             watch: bool = True) -> dict:
    """Register a solution for CI deploys.

    Dispatches the register workflow in the CI repo with the solution's
    name, repo, project and domain, waits for the run, and reports its
    result. The run validates (the project's Environment exists, the repo's
    owner is allowed, the repo is reachable, no other project already holds
    the solution) and writes the entry in ``gapp/solutions.yaml`` with its
    own token, so this machine needs neither gcloud nor a GitHub API write:
    `gh` that can dispatch is enough.

    ``solution_repo`` (``owner/name``, a bare name, or a github.com URL) is
    the GitHub repo to deploy. When omitted, it is read from the current
    checkout's ``origin`` remote. ``domain`` is the custom domain the deploy
    maps, overriding gapp.yaml at deploy time; ``None`` keeps the domain the
    entry already has, ``""`` removes it.

    ``project`` names the GCP project. Without it, the project is found by
    label when gcloud is available; otherwise the command stops and names
    ``--project``. The first setup on a project (no Environment named after
    it in the CI repo yet) is the one step that needs gcloud: it creates or
    verifies the Workload Identity pool, provider, deploy service account
    and binding and records them as the Environment. That never runs from
    a machine without gcloud; such a machine stops and says which machine
    to run it from.

    ``watch`` False returns right after the dispatch, with ``run_id`` set.

    The result's ``dispatched`` says whether the register run was started,
    ``registered`` whether it succeeded (None when not watched), ``messages``
    what the run reported, and ``next_step`` what to do when it did not.
    """
    from gapp.admin.sdk.core import GappSDK

    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError(
            "No CI repo configured. Run 'gapp ci init <repo-name>' first."
        )

    sdk = GappSDK()
    sol_ctx = sdk.resolve_solution(solution)
    if not sol_ctx or not sol_ctx.get("name"):
        raise RuntimeError(
            "No solution specified and not inside a solution repo.\n"
            "  Run: gapp ci setup --solution <solution-name>"
        )
    solution_name = sol_ctx["name"]

    if solution_repo:
        full_solution_repo = _resolve_solution_repo(solution_repo)
    else:
        full_solution_repo = sdk.discover_github_repo(sol_ctx)
    if not full_solution_repo:
        raise RuntimeError(
            f"Cannot determine the GitHub repo for '{solution_name}'.\n"
            f"  Pass it explicitly: --solution-repo owner/name (or a github.com URL),\n"
            f"  or run from inside the solution repo's working tree so it can be read\n"
            f"  from `git remote get-url origin`."
        )

    result = {
        "solution": solution_name,
        "solution_repo": full_solution_repo,
        "solution_repo_private": None,
        "project_id": project.strip() if project else None,
        "ci_repo": ci_repo,
        "domain": None,
        "environment": None,
        "environment_record": None,
        "gcloud_used": False,
        "wif_pool": None,
        "wif_provider": None,
        "service_account": None,
        "binding": None,
        "app_credentials": None,
        "workflow": REGISTER_WORKFLOW,
        "dispatched": False,
        "run_id": None,
        "run_url": None,
        "conclusion": None,
        "registered": None,
        "messages": [],
        "next_step": None,
    }

    def stop(action, hint):
        result["next_step"] = {"action": action, "hint": hint}
        return result

    # The register workflow must be installed before anything can be dispatched.
    _, register = _read_ci_file(ci_repo, REGISTER_WORKFLOW_PATH)
    if register is None:
        return stop("ci init", (
            f"{ci_repo} has no {REGISTER_WORKFLOW_PATH}. Run `gapp ci init {ci_repo}` from a "
            f"machine whose gh login can write to the repo; it installs gapp's workflows and "
            f"the solutions file."
        ))

    gcloud_here = shutil.which("gcloud") is not None

    # The project: given, or found by label when gcloud is here.
    project_id = result["project_id"]
    if not project_id:
        if not gcloud_here:
            return stop("pass --project", (
                f"No project given and gcloud is not available on this machine to find the "
                f"project by label. Re-run with --project <project-id> (gapp_ci_setup(project=...)); "
                f"gapp status on a machine with gcloud shows it."
            ))
        require_gcloud_account()
        try:
            project_id = sdk.resolve_project_for_solution(solution_name)["project_id"]
        except RuntimeError as e:
            raise RuntimeError(
                f"No GCP project found for '{solution_name}': {e}\n"
                f"  Run 'gapp setup --project <project-id>' first, or pass --project."
            )
        result["project_id"] = project_id
    result["environment"] = project_id

    # First-time project work: the Environment record. Only a machine with
    # gcloud can create it; any machine can see whether it exists.
    record = _read_environment_record(ci_repo, project_id)
    if record is None:
        if not gcloud_here:
            if _environment_exists(ci_repo, project_id):
                record = {"checked": False}
            else:
                return stop("ci setup with gcloud", (
                    f"{ci_repo} has no Environment '{project_id}', so this project has not been "
                    f"set up for CI yet. The first `gapp ci setup --project {project_id}` on a "
                    f"project runs from a machine with gcloud logged in as a project admin: it "
                    f"creates the Workload Identity pool, provider and gapp-deploy service "
                    f"account and records them as that Environment. Then this command works "
                    f"from any machine."
                ))
        else:
            require_gcloud_account()
            try:
                sdk.resolve_project_for_solution(solution_name, project=project_id)
            except RuntimeError as e:
                raise RuntimeError(
                    f"No GCP project found for '{solution_name}': {e}\n"
                    f"  Run 'gapp setup --project <project-id>' first."
                )
            result["gcloud_used"] = True
            result["wif_pool"] = _ensure_wif_pool(project_id)
            github_owner = ci_repo.split("/")[0]
            result["wif_provider"] = _ensure_wif_provider(project_id, github_owner)
            result["service_account"] = _ensure_deploy_sa(project_id)
            result["binding"] = _ensure_wif_binding(project_id, ci_repo)
            result["environment_record"] = _write_environment_record(
                ci_repo, project_id,
                _wif_provider_path(_get_project_number(project_id)),
                _deploy_sa_email(project_id),
            )
    if result["environment_record"] is None:
        result["environment_record"] = "exists" if record.get("checked") else "exists (variables not checked from this machine)"

    # Keep the entry's domain unless the caller said otherwise.
    entries = _load_solutions(ci_repo)
    existing = _find_entry(entries, solution_name)
    if domain is None:
        effective_domain = (existing or {}).get("domain") or None
    else:
        effective_domain = domain.strip() or None
    result["domain"] = effective_domain

    # What the register run will refuse, when this machine can already tell.
    if existing and existing.get("project") and existing["project"] != project_id:
        return stop("remove the old entry", (
            f"'{solution_name}' is already registered in {ci_repo} on project "
            f"'{existing['project']}', but this run targets '{project_id}'. A solution deploys "
            f"to one project per CI repo. To move it, remove its entry from {SOLUTIONS_FILE} "
            f"first, then re-run setup."
        ))
    private = _is_repo_private(full_solution_repo)
    result["solution_repo_private"] = private
    if private:
        creds = _hosting_app_presence(ci_repo)
        result["app_credentials"] = creds
        step = _hosting_app_next_step(ci_repo, full_solution_repo, creds)
        if step:
            return stop(step["action"], step["hint"])
    if _actions_api_available(ci_repo):
        allowed = _actions_variable_value(ci_repo, ALLOWED_OWNERS_VAR)
        owner = full_solution_repo.split("/")[0].lower()
        if not allowed or owner not in [o.strip().lower() for o in allowed.split(",")]:
            return stop("configure variables", (
                f"The register workflow accepts only solution repos whose owner is listed in the "
                f"repository variable {ALLOWED_OWNERS_VAR} of {ci_repo}"
                f"{' (' + allowed + ')' if allowed else ', which is not set'}. Add '{owner}':\n"
                f"  gh variable set {ALLOWED_OWNERS_VAR} --repo {ci_repo} --body "
                f"{(allowed + ',' if allowed else '') + owner}"
            ))

    # Dispatch the register workflow and report what it did.
    _dispatch(ci_repo, REGISTER_WORKFLOW, {
        "solution": solution_name,
        "solution-repo": full_solution_repo,
        "project": project_id,
        "domain": effective_domain or "",
    })
    result["dispatched"] = True
    result["run_id"] = _latest_run_id(ci_repo, REGISTER_WORKFLOW)
    if result["run_id"]:
        result["run_url"] = f"https://github.com/{ci_repo}/actions/runs/{result['run_id']}"
    if not watch or not result["run_id"]:
        if not result["run_id"]:
            result["next_step"] = {"action": "check the run", "hint": (
                f"Dispatched {REGISTER_WORKFLOW} in {ci_repo}, but its run id could not be read; "
                f"check the repo's Actions tab."
            )}
        return result
    _, conclusion = _wait_for_run(ci_repo, result["run_id"])
    result["conclusion"] = conclusion
    result["messages"] = _run_messages(ci_repo, result["run_id"], REGISTER_LOG_PREFIX)
    result["registered"] = conclusion == "success"
    if not result["registered"]:
        reasons = [m for m in result["messages"] if m.startswith("refused")] or result["messages"]
        result["next_step"] = {"action": "fix and re-run ci setup", "hint": (
            f"The register run {result['run_url']} ended with '{conclusion or 'no conclusion'}'. "
            + (" ".join(reasons) if reasons else "See the run's log.")
        )}
    return result


# --- ci status ---


def get_ci_status(solution: str | None = None) -> dict:
    """Where CI stands for the operator and, when resolvable, the current solution.

    Returns:
        repo: owner/name of the CI repo, or None if gapp_ci_init has not
              been run. Resolved from local config first, then GitHub topic.
        files: presence of the deploy workflow, the register workflow and
              the solutions file in the CI repo.
        variables: presence of GAPP_ALLOWED_OWNERS, HOSTING_GITHUB_APP_ID and
              HOSTING_GITHUB_APP_PRIVATE_KEY (True/False, or None when the
              Actions API is not readable from this machine).
        solutions: every registered entry, each with name, repo, project,
              domain, gapp_ref and whether the project's Environment exists.
        legacy_workflows: solutions still deployed by a per-solution
              workflow in the CI repo, to register and then delete.
        solution: the current solution's name when one resolves.
        registered: whether it has an entry (None when no solution resolves).
        workflow: kept for callers that read it: True when the solution is
              registered or has a per-solution workflow.
        next_step: {action, hint} naming the gap, or None when
              `gapp ci trigger` can run.
    """
    from gapp.admin.sdk.core import GappSDK

    result = {
        "repo": None,
        "files": None,
        "variables": None,
        "variables_checked": None,
        "variables_note": None,
        "solutions": [],
        "legacy_workflows": [],
        "solution": None,
        "registered": None,
        "workflow": False,
        "solution_repo": None,
        "solution_repo_private": None,
        "domain": None,
        "environment": None,
        "environment_record": None,
        "app_credentials": None,
        "next_step": None,
    }

    result["repo"] = _resolve_ci_repo()
    if not result["repo"]:
        result["next_step"] = {"action": "ci init", "hint": "Run `gapp ci init <owner>/<repo>` to designate the CI repo."}
        return result
    ci_repo = result["repo"]

    _, deploy = _read_ci_file(ci_repo, DEPLOY_WORKFLOW_PATH)
    _, register = _read_ci_file(ci_repo, REGISTER_WORKFLOW_PATH)
    entries = _load_solutions(ci_repo)
    result["files"] = {
        DEPLOY_WORKFLOW_PATH: deploy is not None,
        REGISTER_WORKFLOW_PATH: register is not None,
        SOLUTIONS_FILE: entries is not None,
    }
    report = _variables_report(ci_repo)
    result["variables"] = report["variables"]
    result["variables_checked"] = report["checked"]
    result["variables_note"] = report["note"]
    result["legacy_workflows"] = _legacy_workflows(ci_repo)

    environments = {}
    for entry in entries or []:
        project = entry.get("project")
        if project and project not in environments:
            environments[project] = _environment_exists(ci_repo, project)
        result["solutions"].append({
            "name": entry.get("name"), "repo": entry.get("repo"), "project": project,
            "domain": entry.get("domain"), "gapp_ref": entry.get("gapp_ref"),
            "environment_exists": environments.get(project, False),
        })

    missing_files = [p for p, ok in result["files"].items() if not ok]
    if missing_files:
        result["next_step"] = {"action": "ci init", "hint": (
            f"Missing in {ci_repo}: {', '.join(missing_files)}. Run `gapp ci init {ci_repo}` "
            f"from a machine whose gh login can write to the repo."
        )}
    elif (step := _variables_next_step(ci_repo, report)) and report["variables"][ALLOWED_OWNERS_VAR] is not True:
        result["next_step"] = step

    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        return result
    solution_name = ctx["name"]
    result["solution"] = solution_name
    entry = _find_entry(entries, solution_name)
    result["registered"] = entry is not None
    result["workflow"] = entry is not None or solution_name in result["legacy_workflows"]
    if entry is None:
        if result["next_step"] is None:
            hint = f"'{solution_name}' is not registered in {ci_repo}. Run `gapp ci setup --project <project-id>`"
            if solution_name in result["legacy_workflows"]:
                hint += (f"; it still has its own workflow {WORKFLOWS_DIR}/{solution_name}.yml, which "
                         f"keeps working and can be deleted once the solution is registered")
            result["next_step"] = {"action": "ci setup", "hint": hint + "."}
        return result

    result["solution_repo"] = entry.get("repo")
    result["domain"] = entry.get("domain")
    result["environment"] = entry.get("project")
    record = _environment_record_status(ci_repo, entry["project"]) if entry.get("project") else None
    result["environment_record"] = record
    if result["next_step"] is None and record is not None:
        if not record["exists"]:
            result["next_step"] = {"action": "ci setup with gcloud", "hint": (
                f"'{solution_name}' is registered on project '{entry['project']}', but {ci_repo} has no "
                f"Environment of that name. Run `gapp ci setup --project {entry['project']}` from a "
                f"machine with gcloud to record the project's deploy identity."
            )}
        elif record["checked"] and not (record["wif_provider_present"] and record["service_account_present"]):
            missing = [v for v, ok in ((ENV_WIF_PROVIDER_VAR, record["wif_provider_present"]),
                                       (ENV_DEPLOY_SA_VAR, record["service_account_present"])) if not ok]
            result["next_step"] = {"action": "ci setup with gcloud", "hint": (
                f"The Environment '{entry['project']}' in {ci_repo} is missing {', '.join(missing)}. "
                f"Run `gapp ci setup --project {entry['project']}` from a machine with gcloud to "
                f"record the deploy identity again."
            )}
    if entry.get("repo"):
        try:
            private = _is_repo_private(entry["repo"])
        except RuntimeError as e:
            result["solution_repo_private"] = None
            if result["next_step"] is None:
                result["next_step"] = {"action": "check solution repo", "hint": str(e)}
            return result
        result["solution_repo_private"] = private
        if private:
            creds = _hosting_app_presence(ci_repo)
            result["app_credentials"] = creds
            step = _hosting_app_next_step(ci_repo, entry["repo"], creds)
            if step and result["next_step"] is None:
                result["next_step"] = step
    return result


# --- ci trigger ---


def trigger_ci(solution: str | None = None, *, ref: str = "main", watch: bool = True) -> dict:
    """Trigger a CI deployment for a solution.

    Dispatches the generic deploy workflow in the CI repo with the
    solution's name (and the ref), after checking what would make the run
    fail at once: the solution is registered, its project's Environment
    exists, and a private repo's hosting-app credential is present where
    that can be read from here. A solution that is not registered but still
    has its own per-solution workflow is dispatched through that workflow.
    If watch=True (default), blocks and streams status until completion.
    """
    from gapp.admin.sdk.core import GappSDK

    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError(
            "No CI repo configured. Run 'gapp ci init <repo-name>' first."
        )

    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        if not solution:
            raise RuntimeError(
                "No solution specified and not inside a solution repo.\n"
                "  Run: gapp ci trigger --solution <solution-name>"
            )
        ctx = {"name": solution}
    solution_name = ctx["name"]

    entry = _find_entry(_load_solutions(ci_repo), solution_name)
    legacy = False
    if entry is None:
        _, leaf = _read_ci_file(ci_repo, f"{WORKFLOWS_DIR}/{solution_name}.yml")
        if leaf is None:
            raise RuntimeError(
                f"'{solution_name}' is not registered in {ci_repo} ({SOLUTIONS_FILE}).\n"
                f"  Run 'gapp ci setup --solution {solution_name} --project <project-id>' first."
            )
        legacy = True
        workflow_file = f"{solution_name}.yml"
        fields = {"ref": ref}
    else:
        _, deploy = _read_ci_file(ci_repo, DEPLOY_WORKFLOW_PATH)
        if deploy is None:
            raise RuntimeError(
                f"{ci_repo} has no {DEPLOY_WORKFLOW_PATH}. Run 'gapp ci init {ci_repo}' to install it."
            )
        project = entry.get("project")
        if not project or not _environment_exists(ci_repo, project):
            raise RuntimeError(
                f"'{solution_name}' is registered on project '{project}', but {ci_repo} has no "
                f"Environment of that name. Run 'gapp ci setup --project {project}' from a machine "
                f"with gcloud to record the project's deploy identity."
            )
        if entry.get("repo") and _is_repo_private(entry["repo"]):
            step = _hosting_app_next_step(ci_repo, entry["repo"], _hosting_app_presence(ci_repo))
            if step:
                raise RuntimeError(step["hint"])
        workflow_file = DEPLOY_WORKFLOW
        fields = {"solution": solution_name, "ref": ref}

    _dispatch(ci_repo, workflow_file, fields)
    run_id = _latest_run_id(ci_repo, workflow_file)
    run_url = f"https://github.com/{ci_repo}/actions/runs/{run_id}" if run_id else None

    trigger_result = {
        "solution": solution_name,
        "ci_repo": ci_repo,
        "workflow": workflow_file,
        "legacy_workflow": legacy,
        "ref": ref,
        "run_id": run_id,
        "run_url": run_url,
        "watched": False,
        "conclusion": None,
        "next_step": None,
    }
    if legacy:
        trigger_result["next_step"] = {"action": "ci setup", "hint": (
            f"'{solution_name}' still deploys through its own workflow {workflow_file}. Register it "
            f"with `gapp ci setup --solution {solution_name} --project <project-id>` and delete "
            f"that file when convenient."
        )}

    if watch and run_id:
        subprocess.run(["gh", "run", "watch", run_id, "--repo", ci_repo])
        trigger_result["watched"] = True
        conclusion_result = subprocess.run(
            ["gh", "api", f"repos/{ci_repo}/actions/runs/{run_id}", "--jq", ".conclusion"],
            capture_output=True, text=True,
        )
        if conclusion_result.returncode == 0:
            trigger_result["conclusion"] = conclusion_result.stdout.strip()

    return trigger_result


def watch_ci(run_id: str) -> dict:
    """Watch a CI run to completion.

    Blocks and streams status until the run completes.
    """
    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError("No CI repo configured.")

    subprocess.run(["gh", "run", "watch", run_id, "--repo", ci_repo])

    result = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/runs/{run_id}",
         "--jq", ".conclusion"],
        capture_output=True, text=True,
    )
    conclusion = result.stdout.strip() if result.returncode == 0 else None

    return {
        "run_id": run_id,
        "ci_repo": ci_repo,
        "conclusion": conclusion,
    }


# --- WIF and service account ---

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
        raise RuntimeError(f"Failed to get project number for {project_id}: {result.stderr.strip()}")
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
        raise RuntimeError(f"Failed to create WIF pool: {result.stderr.strip()}")
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
        raise RuntimeError(f"Failed to create WIF provider: {result.stderr.strip()}")
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
            raise RuntimeError(f"Failed to create service account: {result.stderr.strip()}")
        status = "created"

    # Always ensure roles (idempotent — add-iam-policy-binding is a no-op if already bound)
    for role in _DEPLOY_SA_ROLES:
        run_gcloud(
            ["projects", "add-iam-policy-binding", project_id,
             "--member", f"serviceAccount:{sa_email}",
             "--role", role,
             "--condition", "None"],
            capture_output=True,
            text=True,
        )

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
        raise RuntimeError(f"Failed to add WIF binding: {result.stderr.strip()}")
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
