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


def get_ci_status() -> dict:
    """Check CI configuration state.

    Returns dict with:
        repo: owner/name of CI repo (or None)
        source: "local" | "remote" | None
        local_config: bool (ci.yaml exists and has repo)
        remote_config: str | None (repo found via topic)
    """
    result = {
        "repo": None,
        "source": None,
        "local_config": False,
        "remote_config": None,
    }

    # Check local config
    config = _load_ci_config()
    local_repo = config.get("repo")
    if local_repo:
        result["local_config"] = True
        result["repo"] = local_repo
        result["source"] = "local"

    # Check remote (don't fail if gh is not available or not authenticated)
    try:
        remote_repo = _find_ci_repo_by_topic()
        result["remote_config"] = remote_repo
        if not result["repo"] and remote_repo:
            result["repo"] = remote_repo
            result["source"] = "remote"
    except Exception:
        pass

    return result


def get_ci_repo() -> str | None:
    """Get the CI repo name. Local config takes priority over remote."""
    status = get_ci_status()
    return status.get("repo")


# --- WIF and service account ---

_WIF_POOL_ID = "github"
_WIF_PROVIDER_ID = "github"
_DEPLOY_SA_NAME = "gapp-deploy"

_DEPLOY_SA_ROLES = [
    "roles/cloudbuild.builds.editor",
    "roles/run.admin",
    "roles/artifactregistry.admin",
    "roles/secretmanager.admin",
    "roles/storage.admin",
    "roles/iam.serviceAccountUser",
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


def _ensure_wif_provider(project_id: str) -> str:
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

    # Create
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
         f"assertion.repository_owner == '{project_id}'",
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

    # Check if exists
    check = subprocess.run(
        ["gcloud", "iam", "service-accounts", "describe", sa_email,
         "--project", project_id],
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return "exists"

    # Create
    result = subprocess.run(
        ["gcloud", "iam", "service-accounts", "create", _DEPLOY_SA_NAME,
         "--project", project_id,
         "--display-name", "gapp CI/CD deploy"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create service account: {result.stderr.strip()}")

    # Grant roles
    for role in _DEPLOY_SA_ROLES:
        subprocess.run(
            ["gcloud", "projects", "add-iam-policy-binding", project_id,
             "--member", f"serviceAccount:{sa_email}",
             "--role", role,
             "--condition", "None"],
            capture_output=True,
            text=True,
        )

    return "created"


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
    gapp_ref = gapp_sha.stdout.strip()[:12] if gapp_sha.returncode == 0 else "main"

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

    with tempfile.TemporaryDirectory() as tmpdir:
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

        subprocess.run(
            ["git", "commit", "-m", f"Add deploy workflow for {solution_name}"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        push = subprocess.run(
            ["git", "push"],
            capture_output=True, text=True, cwd=tmpdir,
        )
        if push.returncode != 0:
            raise RuntimeError(f"Failed to push to {ci_repo}: {push.stderr.strip()}")
        return "pushed"


def setup_ci(solution_repo: str) -> dict:
    """Wire a solution for CI/CD deployment.

    1. Discover CI repo from local config
    2. Resolve solution context (need project_id)
    3. Create WIF pool + provider (idempotent)
    4. Create deploy service account (idempotent)
    5. Add IAM binding for CI repo (idempotent)
    6. Generate and push workflow file

    Returns dict describing what was done.
    """
    from gapp.admin.sdk.context import resolve_solution

    # 1. Find CI repo
    ci_repo = get_ci_repo()
    if not ci_repo:
        raise RuntimeError(
            "No CI repo configured. Run 'gapp ci init <repo-name>' first."
        )

    # 2. Resolve solution context
    ctx = resolve_solution()
    if not ctx:
        raise RuntimeError(
            "Not inside a gapp solution. Run from a solution repo directory."
        )
    solution_name = ctx["name"]
    project_id = ctx.get("project_id")
    if not project_id:
        raise RuntimeError(
            "No GCP project attached. Run 'gapp setup <project-id>' first."
        )

    # Resolve solution repo to owner/name
    full_solution_repo = _resolve_repo(solution_repo)

    # Determine gapp repo (the repo this code lives in)
    gapp_repo_result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True, text=True,
    )
    # For the gapp repo reference in the workflow, we need owner/gapp
    # Since gapp is installed, we look at where the workflow lives
    # For now, derive from the solution repo's gapp.yaml runtime ref
    # or just use a convention. The gapp repo is wherever this code is from.
    # We'll use the GitHub origin of the current repo if we're in it,
    # otherwise fall back.
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
    result["wif_provider"] = _ensure_wif_provider(project_id)

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
    """Determine the gapp repo owner/name for workflow references."""
    import importlib.metadata
    # Try to get from the installed package's git origin
    # If gapp is installed from git, the URL contains the repo
    try:
        urls = importlib.metadata.metadata("gapp").get_all("Project-URL") or []
        for url in urls:
            if "github.com" in url:
                parts = url.split("github.com/")[-1].strip("/").split("/")
                if len(parts) >= 2:
                    return f"{parts[0]}/{parts[1]}"
    except Exception:
        pass

    # Fallback: check if we're inside the gapp repo
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    raise RuntimeError(
        "Could not determine the gapp repo. Ensure gapp is installed from a known GitHub source."
    )
