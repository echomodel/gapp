"""gapp ci — CI/CD automation for gapp solutions."""

import json
import os
import re
import subprocess
from pathlib import Path

from gapp.admin.sdk.config import get_config_dir
from gapp.admin.sdk.util import run_gcloud, require_gcloud_account

import yaml


_CI_TOPIC = "gapp-ci"

# Where the caller workflow of a private solution repo finds the hosting
# GitHub App's credentials: an Actions variable and an Actions secret in
# the CI repo. Fixed names, like GITHUB_TOKEN: the credentials belong to
# the CI repo (one pair per operator), so nothing about a solution could
# want different ones.
APP_ID_VAR = "HOSTING_GITHUB_APP_ID"
APP_KEY_SECRET = "HOSTING_GITHUB_APP_PRIVATE_KEY"

# Per-project deploy identity, recorded in the CI repo as a GitHub
# Environment named after the project id, holding these two variables.
# Written by the first `ci setup` on a project, read by every later one,
# so a new solution on an already-set-up project needs no gcloud.
ENV_WIF_PROVIDER_VAR = "GAPP_WIF_PROVIDER"
ENV_DEPLOY_SA_VAR = "GAPP_DEPLOY_SERVICE_ACCOUNT"


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
    # Get authenticated gh user
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


def init_ci(repo: str, local_only: bool = False) -> dict:
    """Designate the operator's CI repo.

    1. Resolve repo to owner/name
    2. Write to XDG config (ci.yaml)
    3. Tag the repo with gapp-ci topic (unless local_only)
    4. Ensure only one repo has the topic

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
        result.update(_hosting_app_report(full_name))
        return result

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

    result.update(_hosting_app_report(full_name))
    return result


def _hosting_app_report(ci_repo: str) -> dict:
    """Whether the CI repo holds the hosting app's variable and secret.

    `ci init` reports them so the operator learns about the one-time step
    before the first private solution repo needs it. It never sets or
    overwrites them: the values are the operator's. Returns hosting_app
    (presence of each) and next_step (the two commands) when either is
    missing.
    """
    present = _hosting_app_presence(ci_repo)
    if not present["checked"]:
        return {"hosting_app": present, "next_step": None}
    if present["app_id_present"] is True and present["private_key_present"] is True:
        return {"hosting_app": present, "next_step": None}
    missing = []
    if present["app_id_present"] is not True:
        missing.append(f"Actions variable {APP_ID_VAR}")
    if present["private_key_present"] is not True:
        missing.append(f"Actions secret {APP_KEY_SECRET}")
    hint = (
        f"Not yet in {ci_repo}: {', '.join(missing)}. "
        f"Only private solution repos need them: CI clones a private repo with a "
        f"GitHub App installed on its owner. When you have one, store its id and "
        f"private key once:\n"
        f"  gh variable set {APP_ID_VAR} --repo {ci_repo} --body <app-id>\n"
        f"  gh secret set {APP_KEY_SECRET} --repo {ci_repo} < key.pem\n"
        f"See docs/CI.md, \"Private solution repos\"."
    )
    return {"hosting_app": present, "next_step": {"action": "configure hosting app", "hint": hint}}


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


UNCHECKED_NOTE = (
    "not checked from this machine (the GitHub Actions variables and secrets "
    "API is not reachable here); the first run's credential step verifies it"
)


def _actions_api_available(ci_repo: str) -> bool:
    """Can this machine read the CI repo's Actions variables and secrets?

    Some sessions reach GitHub through a proxy that refuses the Actions
    variables and secrets API while allowing contents and dispatch. The
    capability is probed once, up front, like gcloud is, so a check this
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

    `kind` is "variables" or "secrets". GitHub returns metadata only, never
    a secret's value. Returns True, False (404), or None when the check
    itself could not be performed from this machine.
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


def _gh_api_json(args: list[str], payload: dict | None = None) -> tuple[int, str, str]:
    """Run `gh api` with an optional JSON body; returns (rc, stdout, stderr)."""
    cmd = ["gh", "api"] + args
    kwargs = {"capture_output": True, "text": True}
    if payload is not None:
        cmd += ["--input", "-"]
        kwargs["input"] = json.dumps(payload)
    res = subprocess.run(cmd, **kwargs)
    return res.returncode, res.stdout or "", res.stderr or ""


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
    and the values come back as None with `checked` False. Never derived
    from workflow files: the Environment is the record.
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
        out[key] = rc == 0 and bool((json.loads(body).get("value") or "").strip()) if (rc == 0 and body.strip()) else False
    return out


def _read_workflow_file(ci_repo: str, solution_name: str) -> tuple[str | None, str | None]:
    """Fetch the solution's caller workflow from the CI repo.

    Returns (sha, content), or (None, None) when the file does not exist.
    """
    import base64

    endpoint = f"repos/{ci_repo}/contents/.github/workflows/{solution_name}.yml"
    existing = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if existing.returncode != 0 or not existing.stdout.strip():
        return None, None
    data = json.loads(existing.stdout)
    content = base64.b64decode(data.get("content", "")).decode()
    return data.get("sha"), content


def _parse_workflow(content: str | None) -> dict:
    """Read the values gapp cares about back out of a caller workflow.

    Returns solution_repo, domain, app_id_var, app_key_secret (each None
    when absent). The variable and secret names are recovered from the
    `${{ vars.NAME }}` / `${{ secrets.NAME }}` expressions gapp wrote.
    """
    info = {"solution_repo": None, "domain": None, "environment": None, "project": None,
            "app_id_var": None, "app_key_secret": None}
    if not content:
        return info
    try:
        doc = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        return info
    job = ((doc.get("jobs") or {}).get("deploy") or {})
    with_block = job.get("with") or {}
    info["solution_repo"] = with_block.get("solution-repo") or None
    environment = with_block.get("environment")
    if environment is not None:
        info["environment"] = str(environment).strip() or None
    # The project a workflow is bound to: its Environment, or for a workflow
    # that still carries literals, the project in the deploy service account.
    info["project"] = info["environment"]
    if not info["project"]:
        m = re.search(r"gapp-deploy@([a-z][a-z0-9-]*)\.iam\.gserviceaccount\.com",
                      str(with_block.get("service-account") or ""))
        info["project"] = m.group(1) if m else None
    domain = with_block.get("domain")
    if domain is not None:
        info["domain"] = str(domain).strip() or None
    m = re.search(r"vars\.([A-Za-z_][A-Za-z0-9_]*)", str(with_block.get("solution-repo-app-id") or ""))
    info["app_id_var"] = m.group(1) if m else None
    secrets_block = job.get("secrets") or {}
    m = re.search(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)",
                  str(secrets_block.get("solution-repo-app-private-key") or "")
                  if isinstance(secrets_block, dict) else "")
    info["app_key_secret"] = m.group(1) if m else None
    return info


def _assess_private_readiness(ci_repo: str, solution_name: str, solution_repo: str,
                              private: bool, workflow_info: dict) -> dict:
    """Can a CI deploy of this solution clone its repo?

    Public repo: always. Private repo: only when the caller workflow passes
    the hosting app's credentials and both the Actions variable and secret
    it names exist in the CI repo. Returns ready, app_credentials, next_step.
    """
    if not private:
        return {"ready": True, "app_credentials": None, "next_step": None}

    id_var = workflow_info.get("app_id_var")
    key_secret = workflow_info.get("app_key_secret")
    if not id_var or not key_secret:
        return {
            "ready": False,
            "app_credentials": None,
            "next_step": {
                "action": "ci setup",
                "hint": (
                    f"{solution_repo} is private, but the workflow for '{solution_name}' "
                    f"passes no hosting-app credentials, so the runner cannot clone it. "
                    f"Re-run `gapp ci setup --solution {solution_name}` to add them."
                ),
            },
        }

    if not _actions_api_available(ci_repo):
        creds = {"app_id_var": id_var, "app_key_secret": key_secret,
                 "app_id_present": None, "private_key_present": None,
                 "checked": False, "note": UNCHECKED_NOTE}
        return {"ready": True, "app_credentials": creds, "next_step": None}
    creds = {
        "app_id_var": id_var,
        "app_key_secret": key_secret,
        "app_id_present": _actions_item_present(ci_repo, "variables", id_var),
        "private_key_present": _actions_item_present(ci_repo, "secrets", key_secret),
        "checked": True, "note": None,
    }
    missing = []
    if creds["app_id_present"] is not True:
        missing.append(f"Actions variable {id_var}")
    if creds["private_key_present"] is not True:
        missing.append(f"Actions secret {key_secret}")
    if not missing:
        return {"ready": True, "app_credentials": creds, "next_step": None}
    hint = (
        f"{solution_repo} is private; CI clones it with a GitHub App installed on "
        f"its owner. Missing in {ci_repo}: "
        f"{', '.join(missing)}. Store the app's id and private key there:\n"
        f"  gh variable set {id_var} --repo {ci_repo} --body <app-id>\n"
        f"  gh secret set {key_secret} --repo {ci_repo} < <private-key.pem>\n"
        f"See docs/CI.md, \"Private solution repos\"."
    )
    return {"ready": False, "app_credentials": creds,
            "next_step": {"action": "configure hosting app", "hint": hint}}


def _assess_environment_readiness(ci_repo: str, solution_name: str, environment: str | None) -> dict:
    """Is the Environment an Environment-form workflow deploys through complete?

    Returns environment_record (presence of each variable, or None for a
    literal-form workflow) and next_step (None when ready).
    """
    if not environment:
        return {"environment_record": None, "next_step": None}
    record = _environment_record_status(ci_repo, environment)
    if not record["exists"]:
        return {
            "environment_record": record,
            "next_step": {
                "action": "ci setup",
                "hint": (
                    f"The workflow for '{solution_name}' deploys through the GitHub "
                    f"Environment '{environment}' in {ci_repo}, but that Environment does "
                    f"not exist. Re-run `gapp ci setup --solution {solution_name}` with "
                    f"gcloud logged in to record the deploy identity."
                ),
            },
        }
    if not record["checked"] or (record["wif_provider_present"] and record["service_account_present"]):
        return {"environment_record": record, "next_step": None}
    missing = [v for v, ok in ((ENV_WIF_PROVIDER_VAR, record["wif_provider_present"]),
                               (ENV_DEPLOY_SA_VAR, record["service_account_present"])) if not ok]
    return {
        "environment_record": record,
        "next_step": {
            "action": "ci setup",
            "hint": (
                f"The workflow for '{solution_name}' deploys through the GitHub "
                f"Environment '{environment}' in {ci_repo}, but that Environment is "
                f"missing {', '.join(missing)}. Re-run `gapp ci setup --solution "
                f"{solution_name}` with gcloud logged in to record the deploy identity again."
            ),
        },
    }


def get_ci_status(solution: str | None = None) -> dict:
    """Check CI readiness for the current solution.

    Returns:
        repo: owner/name of the CI repo, or None if gapp_ci_init
              has not been run. Resolved from local config first,
              then GitHub topic.
        workflow: True if a GitHub Actions workflow exists in the
                  CI repo for this solution (meaning gapp_ci_setup
                  has been run for it). False if the workflow is
                  missing or the CI repo is not configured. Only
                  checked when repo is set and a solution can be
                  resolved.
        solution_repo: the GitHub repo the workflow deploys, when the
                  workflow exists.
        solution_repo_private: visibility of that repo (None when unknown).
        domain: the `domain:` input the workflow passes, if any.
        environment: the project id whose GitHub Environment the workflow
                  deploys through, when it uses the Environment form;
                  None for a workflow that still carries literals.
        environment_record: for an Environment-form workflow, whether the
                  Environment's two variables exist in the CI repo.
        app_credentials: for a private repo, the Actions variable and
                  secret the workflow names and whether each exists in
                  the CI repo. None for a public repo.
        next_step: {action, hint} when a CI deploy would fail as things
                  stand; None when `gapp ci trigger` can run.
    """
    from gapp.admin.sdk.core import GappSDK

    result = {
        "repo": None,
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
        return result

    # Check if this solution has a workflow in the CI repo
    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        return result

    solution_name = ctx["name"]
    try:
        _, content = _read_workflow_file(result["repo"], solution_name)
    except FileNotFoundError:
        return result
    if not content:
        return result
    result["workflow"] = True

    info = _parse_workflow(content)
    result["solution_repo"] = info["solution_repo"]
    result["domain"] = info["domain"]
    result["environment"] = info["environment"]
    env_ready = _assess_environment_readiness(result["repo"], solution_name, info["environment"])
    result["environment_record"] = env_ready["environment_record"]
    if env_ready["next_step"]:
        result["next_step"] = env_ready["next_step"]
        return result
    if not info["solution_repo"]:
        return result
    try:
        private = _is_repo_private(info["solution_repo"])
    except RuntimeError as e:
        result["next_step"] = {"action": "check solution repo", "hint": str(e)}
        return result
    result["solution_repo_private"] = private
    readiness = _assess_private_readiness(
        result["repo"], solution_name, info["solution_repo"], private, info,
    )
    result["app_credentials"] = readiness["app_credentials"]
    result["next_step"] = readiness["next_step"]
    return result


def get_ci_repo() -> str | None:
    """Get the CI repo name. Local config takes priority over remote."""
    return _resolve_ci_repo()


def trigger_ci(solution: str | None = None, *, ref: str = "main", watch: bool = True) -> dict:
    """Trigger a CI deployment for a solution.

    Dispatches the solution's workflow in the CI repo via gh CLI.
    If watch=True (default), blocks and streams status until completion.
    """
    import time
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
                "  Run: gapp ci trigger <solution-name>"
            )
        ctx = {"name": solution}

    solution_name = ctx["name"]
    workflow_file = f"{solution_name}.yml"

    # Verify the workflow exists in the CI repo
    _, content = _read_workflow_file(ci_repo, solution_name)
    if not content:
        raise RuntimeError(
            f"No CI workflow found for '{solution_name}'.\n"
            f"  Run 'gapp ci setup {solution_name}' first to create the workflow."
        )

    # A private solution repo needs the hosting app's credentials in place,
    # or the run fails at checkout with "Repository not found". Refuse now.
    info = _parse_workflow(content)
    env_ready = _assess_environment_readiness(ci_repo, solution_name, info["environment"])
    if env_ready["next_step"]:
        raise RuntimeError(env_ready["next_step"]["hint"])
    if info["solution_repo"]:
        private = _is_repo_private(info["solution_repo"])
        readiness = _assess_private_readiness(
            ci_repo, solution_name, info["solution_repo"], private, info,
        )
        if not readiness["ready"]:
            raise RuntimeError(readiness["next_step"]["hint"])

    result = subprocess.run(
        ["gh", "workflow", "run", workflow_file,
         "--repo", ci_repo,
         "-f", f"ref={ref}"],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to trigger workflow {workflow_file} in {ci_repo}:\n"
            f"  {result.stderr.strip()}"
        )

    # Brief delay for GitHub to register the run
    time.sleep(2)

    # Find the run ID
    run_id = None
    list_result = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/actions/runs",
         "--jq", ".workflow_runs[0].id"],
        capture_output=True, text=True,
    )
    if list_result.returncode == 0 and list_result.stdout.strip():
        run_id = list_result.stdout.strip()

    run_url = f"https://github.com/{ci_repo}/actions/runs/{run_id}" if run_id else None

    trigger_result = {
        "solution": solution_name,
        "ci_repo": ci_repo,
        "workflow": workflow_file,
        "ref": ref,
        "run_id": run_id,
        "run_url": run_url,
        "watched": False,
        "conclusion": None,
    }

    if watch and run_id:
        watch_result = subprocess.run(
            ["gh", "run", "watch", run_id, "--repo", ci_repo],
        )
        trigger_result["watched"] = True
        # Get conclusion
        conclusion_result = subprocess.run(
            ["gh", "api", f"repos/{ci_repo}/actions/runs/{run_id}",
             "--jq", ".conclusion"],
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

    # Get conclusion
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
    pool_name = f"projects/{project_id}/locations/global/workloadIdentityPools/{_WIF_POOL_ID}"

    # Check if exists
    check = run_gcloud(
        ["iam", "workload-identity-pools", "describe", _WIF_POOL_ID,
         "--project", project_id, "--location", "global"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create
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
    # Check if exists
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
    sa_email = f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"

    # Create if needed
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
    sa_email = f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"
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


def _resolve_gapp_ref(gapp_repo: str) -> str:
    """Pin the reusable workflow to the installed gapp version's release tag.

    gapp tags every release ``vX.Y.Z`` (see CONTRIBUTING), so the generated
    CI workflow references ``deploy.yml@vX.Y.Z`` — readable and tied to the
    gapp the operator actually has installed. Falls back to the current
    ``main`` HEAD only when that tag isn't published yet (e.g. an unreleased
    dev build), so `ci setup` from a contributor checkout still works.
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


def _wif_provider_path(project_number: str) -> str:
    return (
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/{_WIF_POOL_ID}/"
        f"providers/{_WIF_PROVIDER_ID}"
    )


def _deploy_sa_email(project_id: str) -> str:
    return f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"


def _generate_workflow(solution_name: str, solution_repo: str,
                       project_id: str, gapp_repo: str, *,
                       domain: str | None = None,
                       private: bool = False) -> str:
    """Generate the caller workflow YAML for the operator's CI repo.

    The job passes `environment: <project-id>`: the reusable workflow runs
    its job in that GitHub Environment of the CI repo and reads the deploy
    identity (`GAPP_WIF_PROVIDER`, `GAPP_DEPLOY_SERVICE_ACCOUNT`) from the
    Environment's variables, so the workflow carries no per-project
    literals and this function needs nothing from GCP.

    domain: written as the reusable workflow's `domain:` input, which
        overrides the solution's gapp.yaml at deploy time.
    private: the solution repo is private, so the job passes the hosting
        GitHub App's id and private key from the CI repo's fixed Actions
        variable and secret.
    """
    # Pin the reusable workflow to the installed gapp version's release tag.
    gapp_ref = _resolve_gapp_ref(gapp_repo)

    workflow = {
        "name": f"Deploy {solution_name}",
        "on": {
            "workflow_dispatch": {
                "inputs": {
                    "ref": {
                        "description": "Version/tag/SHA to deploy",
                        "default": "main",
                    }
                }
            }
        },
        "jobs": {
            "deploy": {
                "uses": f"{gapp_repo}/.github/workflows/deploy.yml@{gapp_ref}",
                "with": {
                    "solution-repo": solution_repo,
                    "ref": "${{ inputs.ref }}",
                    "environment": project_id,
                    "gapp-repo": gapp_repo,
                    "gapp-ref": gapp_ref,
                    "solution-name": solution_name,
                },
                "permissions": {
                    "id-token": "write",
                    "contents": "read",
                },
            }
        },
    }
    with_block = workflow["jobs"]["deploy"]["with"]
    if domain:
        with_block["domain"] = domain
    if private:
        with_block["solution-repo-app-id"] = f"${{{{ vars.{APP_ID_VAR} }}}}"
        workflow["jobs"]["deploy"]["secrets"] = {
            "solution-repo-app-private-key": f"${{{{ secrets.{APP_KEY_SECRET} }}}}",
        }
    return yaml.dump(workflow, default_flow_style=False, sort_keys=False)


def _write_workflow_to_ci_repo(ci_repo: str, solution_name: str,
                                workflow_content: str) -> str:
    """Create or update the workflow file in the CI repo via the GitHub
    Contents API.

    gapp is not a git client for the operator's repos (CONTRIBUTING.md,
    principle 16): the file is generated configuration, so it is written as a
    config-store update. GitHub makes the commit server-side; nothing is
    cloned, committed, or pushed on the operator's machine, so local git hooks
    are neither triggered nor bypassed. Idempotent: an identical file is
    reported as "unchanged".

    Returns "pushed" (created), "updated", or "unchanged".
    """
    import base64
    import json

    path = f".github/workflows/{solution_name}.yml"
    endpoint = f"repos/{ci_repo}/contents/{path}"

    sha = None
    existing = subprocess.run(
        ["gh", "api", endpoint], capture_output=True, text=True,
    )
    if existing.returncode == 0 and existing.stdout.strip():
        data = json.loads(existing.stdout)
        sha = data.get("sha")
        current = base64.b64decode(data.get("content", "")).decode()
        if current == workflow_content:
            return "unchanged"

    payload = {
        "message": f"{'Update' if sha else 'Add'} deploy workflow for {solution_name}",
        "content": base64.b64encode(workflow_content.encode()).decode(),
    }
    if sha:
        payload["sha"] = sha
    result = subprocess.run(
        ["gh", "api", "-X", "PUT", endpoint, "--input", "-"],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to write {path} to {ci_repo}: {result.stderr.strip()}"
        )
    return "updated" if sha else "pushed"


def _default_workflow_out(solution_name: str) -> Path:
    """Where a rendered workflow lands when it cannot be written through the API."""
    cache = Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")))
    return cache / "gapp" / "ci" / f"{solution_name}.yml"


def _write_workflow_to_file(path: Path, content: str) -> str:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return str(path)


def setup_ci(solution: str | None = None, solution_repo: str | None = None, *,
             domain: str | None = None,
             project: str | None = None,
             out: str | None = None) -> dict:
    """Wire a solution for CI/CD deployment.

    No local clone of the solution repo is required when ``solution`` and
    ``solution_repo`` are both given — CI setup is then fully deterministic
    and works from anywhere (Claude Desktop, a remote/MCP caller, etc.).

    ``solution_repo`` (``owner/name``, a bare name, or a github.com URL) is
    the GitHub repo the standing pipeline deploys. When omitted, it falls
    back to discovering the repo from the current checkout's ``origin``
    remote — convenient for a human running this from inside the repo, but
    cwd-dependent. Prefer the explicit form for standing pipelines.

    ``domain`` is written into the workflow as the reusable workflow's
    ``domain:`` input, which overrides gapp.yaml at deploy time. ``None``
    (the default) keeps whatever ``domain:`` the existing workflow already
    carries, so re-running setup never drops a domain; ``""`` removes it.

    ``project`` names the GCP project. When the CI repo already holds that
    project's deploy identity (a GitHub Environment named after the project
    id with ``GAPP_WIF_PROVIDER`` and ``GAPP_DEPLOY_SERVICE_ACCOUNT`` set),
    setup makes no GCP call at all: it only writes the workflow, so a new
    solution on an already-set-up project needs ``gh`` and nothing else.
    Otherwise (no ``project``, or the record is incomplete) the gcloud path
    runs: it finds or verifies the project, creates or verifies the pool,
    provider, service account and binding, and records the Environment so
    the next solution does not need gcloud.

    A private solution repo cannot be cloned with the run's built-in token,
    so its workflow passes the hosting GitHub App's credentials from the CI
    repo's Actions variable ``HOSTING_GITHUB_APP_ID`` and secret
    ``HOSTING_GITHUB_APP_PRIVATE_KEY``. Setup checks that both exist and
    names them in ``next_step`` when not. A machine that cannot read the
    Actions variables API reports the check as not performed; the first
    run's credential step verifies it.

    ``out`` writes the rendered workflow to that path instead of through
    the GitHub API. The same happens, to a path under gapp's cache
    directory, when the API refuses the write; ``next_step`` then names
    the target path in the CI repo, ``.github/workflows/<solution>.yml``,
    which the operator or their agent commits. gapp still generates the
    file; nothing is hand-edited.
    """
    from gapp.admin.sdk.core import GappSDK

    # 1. Find CI repo
    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError(
            "No CI repo configured. Run 'gapp ci init <repo-name>' first."
        )

    # 2. Resolve the solution (local only, no cloud call)
    sdk = GappSDK()
    sol_ctx = sdk.resolve_solution(solution)
    if not sol_ctx or not sol_ctx.get("name"):
        raise RuntimeError(
            "No solution specified and not inside a solution repo.\n"
            "  Run: gapp ci setup <solution-name>"
        )
    solution_name = sol_ctx["name"]

    # 3. Decide whether GCP is needed at all. With --project and a complete
    #    Environment record, everything below is GitHub API only.
    project_id = project.strip() if project else None
    record = _read_environment_record(ci_repo, project_id) if project_id else None
    needs_gcloud = record is None

    if needs_gcloud:
        require_gcloud_account()
        try:
            res = sdk.resolve_project_for_solution(solution_name, project=project_id)
            project_id = res["project_id"]
        except RuntimeError as e:
            raise RuntimeError(
                f"No GCP project found for '{solution_name}': {e}\n"
                f"  Run 'gapp setup --project <project-id>' first."
            )

    # Explicit solution-repo wins (deterministic, no checkout needed);
    # otherwise discover it from the current checkout's origin remote.
    if solution_repo:
        full_solution_repo = _resolve_solution_repo(solution_repo)
    else:
        full_solution_repo = sdk.discover_github_repo(sol_ctx)
    if not full_solution_repo:
        raise RuntimeError(
            f"Cannot determine the GitHub repo for '{solution_name}'.\n"
            f"  Pass it explicitly: solution_repo='owner/name' (or a\n"
            f"  github.com URL) — this is the deterministic path and needs\n"
            f"  no local checkout. Or run from inside the solution repo's\n"
            f"  working tree so it can be read from `git remote get-url origin`."
        )

    gapp_repo = _get_gapp_repo()

    # Private repos need the hosting app; decide that before touching GCP.
    private = _is_repo_private(full_solution_repo)

    # Keep an existing `domain:` unless the caller said otherwise, and refuse
    # to move a solution to another project behind the operator's back.
    _, existing_content = _read_workflow_file(ci_repo, solution_name)
    existing = _parse_workflow(existing_content)
    if existing["project"] and existing["project"] != project_id:
        raise RuntimeError(
            f"The CI repo already holds a workflow for '{solution_name}' bound to project "
            f"'{existing['project']}', but this run targets '{project_id}'. A solution deploys "
            f"to one project per CI repo. To move it, remove .github/workflows/"
            f"{solution_name}.yml from {ci_repo} first, then re-run setup."
        )
    if domain is None:
        effective_domain = existing["domain"]
    else:
        effective_domain = domain.strip() or None

    result = {
        "solution": solution_name,
        "solution_repo": full_solution_repo,
        "solution_repo_private": private,
        "project_id": project_id,
        "ci_repo": ci_repo,
        "domain": effective_domain,
        "environment": project_id,
        "environment_record": None,
        "gcloud_used": needs_gcloud,
        "workflow_path": None,
        "app_credentials": None,
        "wif_pool": None,
        "wif_provider": None,
        "service_account": None,
        "binding": None,
        "workflow": None,
        "next_step": None,
    }

    if needs_gcloud:
        # 4. WIF pool
        result["wif_pool"] = _ensure_wif_pool(project_id)

        # 5. WIF provider
        github_owner = ci_repo.split("/")[0] if "/" in ci_repo else ci_repo
        result["wif_provider"] = _ensure_wif_provider(project_id, github_owner)

        # 6. Deploy SA
        result["service_account"] = _ensure_deploy_sa(project_id)

        # 7. IAM binding
        result["binding"] = _ensure_wif_binding(project_id, ci_repo)

        # 8. Record the deploy identity in the CI repo so the next solution
        #    on this project needs no gcloud.
        result["environment_record"] = _write_environment_record(
            ci_repo, project_id,
            _wif_provider_path(_get_project_number(project_id)),
            _deploy_sa_email(project_id),
        )
    else:
        result["wif_pool"] = result["wif_provider"] = "recorded"
        result["service_account"] = result["binding"] = "recorded"
        result["environment_record"] = "used" if record.get("checked") else "used (variables not checked from this machine)"

    # 9. Generate the workflow, then deliver it: through the API, or as a
    #    file when `out` is given or the API refuses the write.
    workflow_content = _generate_workflow(
        solution_name, full_solution_repo, project_id, gapp_repo,
        domain=effective_domain, private=private,
    )
    target = f".github/workflows/{solution_name}.yml"
    file_step = None
    if out:
        result["workflow_path"] = _write_workflow_to_file(Path(out), workflow_content)
        result["workflow"] = "written to file"
        file_step = f"Written to {result['workflow_path']} as requested."
    else:
        try:
            result["workflow"] = _write_workflow_to_ci_repo(ci_repo, solution_name, workflow_content)
        except RuntimeError as e:
            result["workflow_path"] = _write_workflow_to_file(
                _default_workflow_out(solution_name), workflow_content,
            )
            result["workflow"] = "write refused; written to file"
            file_step = (
                f"The GitHub API refused the write from this machine ({str(e).splitlines()[0]}); "
                f"the rendered workflow is at {result['workflow_path']}."
            )
    if file_step:
        result["next_step"] = {
            "action": "commit workflow",
            "hint": (
                f"{file_step} Commit it to {ci_repo} as {target} (the operator or their "
                f"agent, through their normal commit process); gapp does not commit to the "
                f"operator repo. Then `gapp ci trigger` deploys."
            ),
        }

    # 10. For a private repo, confirm the CI repo holds the app's credentials.
    readiness = _assess_private_readiness(
        ci_repo, solution_name, full_solution_repo, private,
        {"app_id_var": APP_ID_VAR, "app_key_secret": APP_KEY_SECRET} if private else {},
    )
    result["app_credentials"] = readiness["app_credentials"]
    if readiness["next_step"]:
        if result["next_step"]:
            result["next_step"]["hint"] += "\nAlso: " + readiness["next_step"]["hint"]
        else:
            result["next_step"] = readiness["next_step"]

    return result



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
