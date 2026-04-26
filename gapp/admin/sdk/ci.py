"""gapp ci — CI/CD automation for gapp solutions."""

import json
import subprocess

from gapp.admin.sdk.config import get_config_dir

import yaml


_CI_TOPIC = "gapp-ci"


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

    return result


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
    """
    from gapp.admin.sdk.core import GappSDK

    result = {
        "repo": None,
        "workflow": False,
    }

    # Resolve CI repo: local config → GitHub topic
    config = _load_ci_config()
    local_repo = config.get("repo")
    if local_repo:
        result["repo"] = local_repo
    else:
        try:
            remote_repo = _find_ci_repo_by_topic()
            if remote_repo:
                result["repo"] = remote_repo
        except Exception:
            pass

    if not result["repo"]:
        return result

    # Check if this solution has a workflow in the CI repo
    ctx = GappSDK().resolve_solution(solution)
    if not ctx:
        return result

    solution_name = ctx["name"]
    workflow_file = f"{solution_name}.yml"
    try:
        check = subprocess.run(
            ["gh", "api",
             f"repos/{result['repo']}/contents/.github/workflows/{workflow_file}",
             "--jq", ".name"],
            capture_output=True, text=True,
        )
        result["workflow"] = check.returncode == 0 and bool(check.stdout.strip())
    except FileNotFoundError:
        pass

    return result


def get_ci_repo() -> str | None:
    """Get the CI repo name. Local config takes priority over remote."""
    status = get_ci_status()
    return status.get("repo")


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
    check = subprocess.run(
        ["gh", "api", f"repos/{ci_repo}/contents/.github/workflows/{workflow_file}",
         "--jq", ".name"],
        capture_output=True, text=True,
    )
    if check.returncode != 0 or not check.stdout.strip():
        raise RuntimeError(
            f"No CI workflow found for '{solution_name}'.\n"
            f"  Run 'gapp ci setup {solution_name}' first to create the workflow."
        )

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
    result = subprocess.run(
        ["gcloud", "projects", "describe", project_id,
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
    check = subprocess.run(
        ["gcloud", "iam", "workload-identity-pools", "describe", _WIF_POOL_ID,
         "--project", project_id, "--location", "global"],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create
    result = subprocess.run(
        ["gcloud", "iam", "workload-identity-pools", "create", _WIF_POOL_ID,
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
    check = subprocess.run(
        ["gcloud", "iam", "workload-identity-pools", "providers", "describe",
         _WIF_PROVIDER_ID,
         "--project", project_id, "--location", "global",
         "--workload-identity-pool", _WIF_POOL_ID],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create — restrict to repos owned by the operator's GitHub account
    result = subprocess.run(
        ["gcloud", "iam", "workload-identity-pools", "providers", "create-oidc",
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
    check = subprocess.run(
        ["gcloud", "iam", "service-accounts", "describe", sa_email,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    status = "exists"
    if check.returncode != 0:
        result = subprocess.run(
            ["gcloud", "iam", "service-accounts", "create", _DEPLOY_SA_NAME,
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
        subprocess.run(
            ["gcloud", "projects", "add-iam-policy-binding", project_id,
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

    result = subprocess.run(
        ["gcloud", "iam", "service-accounts", "add-iam-policy-binding",
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


def _generate_workflow(solution_name: str, solution_repo: str,
                       project_id: str, gapp_repo: str) -> str:
    """Generate the caller workflow YAML for the operator's CI repo."""
    project_number = _get_project_number(project_id)
    sa_email = f"{_DEPLOY_SA_NAME}@{project_id}.iam.gserviceaccount.com"
    wif_provider = (
        f"projects/{project_number}/locations/global/"
        f"workloadIdentityPools/{_WIF_POOL_ID}/"
        f"providers/{_WIF_PROVIDER_ID}"
    )

    # Get current gapp commit for pinning
    gapp_sha = subprocess.run(
        ["gh", "api", f"repos/{gapp_repo}/commits/HEAD", "--jq", ".sha"],
        capture_output=True,
        text=True,
    )
    gapp_ref = gapp_sha.stdout.strip() if gapp_sha.returncode == 0 else "main"

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
                    "workload-identity-provider": wif_provider,
                    "service-account": sa_email,
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
    return yaml.dump(workflow, default_flow_style=False, sort_keys=False)


def _push_workflow_to_ci_repo(ci_repo: str, solution_name: str,
                               workflow_content: str) -> str:
    """Push a workflow file to the CI repo via gh."""
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as parent:
        tmpdir = os.path.join(parent, "repo")
        # Clone the CI repo
        clone = subprocess.run(
            ["gh", "repo", "clone", ci_repo, tmpdir, "--", "--depth", "1"],
            capture_output=True,
            text=True,
        )
        if clone.returncode != 0:
            raise RuntimeError(f"Failed to clone {ci_repo}: {clone.stderr.strip()}")

        # Write workflow file
        workflows_dir = os.path.join(tmpdir, ".github", "workflows")
        os.makedirs(workflows_dir, exist_ok=True)
        workflow_path = os.path.join(workflows_dir, f"{solution_name}.yml")
        with open(workflow_path, "w") as f:
            f.write(workflow_content)

        # Commit and push
        subprocess.run(
            ["git", "add", ".github/"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        if not status.stdout.strip():
            return "unchanged"

        commit = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "commit",
             "-m", f"Add deploy workflow for {solution_name}"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        if commit.returncode != 0:
            raise RuntimeError(f"Failed to commit workflow: {commit.stderr.strip()}")
        push = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        if push.returncode != 0:
            raise RuntimeError(f"Failed to push to {ci_repo}: {push.stderr.strip()}")
        return "pushed"


def setup_ci(solution: str | None = None) -> dict:
    """Wire a solution for CI/CD deployment.

    No local clone of the solution repo is required. Resolves everything
    from local config, GCP labels, and GitHub.
    """
    from gapp.admin.sdk.core import GappSDK

    # 1. Find CI repo
    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError(
            "No CI repo configured. Run 'gapp ci init <repo-name>' first."
        )

    # 2. Resolve solution + project
    sdk = GappSDK()
    sol_ctx = sdk.resolve_solution(solution)
    if not sol_ctx or not sol_ctx.get("name"):
        raise RuntimeError(
            "No solution specified and not inside a solution repo.\n"
            "  Run: gapp ci setup <solution-name>"
        )
    solution_name = sol_ctx["name"]

    try:
        res = sdk.resolve_project_for_solution(solution_name)
        project_id = res["project_id"]
    except RuntimeError as e:
        raise RuntimeError(
            f"No GCP project found for '{solution_name}': {e}\n"
            f"  Run 'gapp setup --project <project-id>' first."
        )

    full_solution_repo = sol_ctx.get("github_repo")
    if not full_solution_repo:
        raise RuntimeError(
            f"Could not determine GitHub repo for '{solution_name}'.\n"
            f"  Ensure the solution repo exists on GitHub with the gapp-solution topic."
        )

    gapp_repo = _get_gapp_repo()

    result = {
        "solution": solution_name,
        "solution_repo": full_solution_repo,
        "project_id": project_id,
        "ci_repo": ci_repo,
        "wif_pool": None,
        "wif_provider": None,
        "service_account": None,
        "binding": None,
        "workflow": None,
    }

    # 3. WIF pool
    result["wif_pool"] = _ensure_wif_pool(project_id)

    # 4. WIF provider
    github_owner = ci_repo.split("/")[0] if "/" in ci_repo else ci_repo
    result["wif_provider"] = _ensure_wif_provider(project_id, github_owner)

    # 5. Deploy SA
    result["service_account"] = _ensure_deploy_sa(project_id)

    # 6. IAM binding
    result["binding"] = _ensure_wif_binding(project_id, ci_repo)

    # 7. Generate and push workflow
    workflow_content = _generate_workflow(
        solution_name, full_solution_repo, project_id, gapp_repo,
    )
    result["workflow"] = _push_workflow_to_ci_repo(ci_repo, solution_name, workflow_content)

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
