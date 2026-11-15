"""What gapp keeps in a project's own bucket, `gapp-<project>`.

    deployments/<id>/terraform/     the deployment's Terraform state
    deployments/<id>/deployed.yaml  the last deploy that succeeded
    deployments/<id>/history/       one file per deploy, failed ones included

`gapp project setup` makes the bucket. Only identities that deploy can
reach it: no app's service account is granted anything on it, so an app
cannot change its own state or the record of what was deployed.

The record is a snapshot. No command reads a definition from it; the
fleet stays the one record of what is meant to run. It is there so that
what is running can be traced to a definition, a repo and a commit when
the fleet no longer says.
"""

import os
from datetime import datetime, timezone

import yaml

FORMAT = 1


def bucket(project: str) -> str:
    """The project's gapp bucket."""
    return f"gapp-{project}".lower()


def folder(deployment_id: str) -> str:
    return f"deployments/{deployment_id}"


def state_prefix(deployment_id: str) -> str:
    return f"{folder(deployment_id)}/terraform/state"


def latest_path(deployment_id: str) -> str:
    return f"{folder(deployment_id)}/deployed.yaml"


def history_path(deployment_id: str, at: datetime, commit: str) -> str:
    return f"{folder(deployment_id)}/history/{at.strftime('%Y%m%dT%H%M%SZ')}-{commit[:12]}.yaml"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def where_from() -> dict:
    """Where this deploy runs: a machine, or a GitHub Actions run of a fleet repo."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return {"kind": "machine"}
    server, repo, run = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    found = {"kind": "runner"}
    if repo:
        found["fleet_repo"] = repo
    if server and repo and run:
        found["run"] = f"{server}/{repo}/actions/runs/{run}"
    return found


def build(*, name: str, definition: dict, commit: str, image: str, at: datetime, by: str | None,
          installed: str, upgraded_from: str | None, error: str | None = None) -> dict:
    """One deploy, as it is written down. No secret's value is in a definition, so none is here."""
    entry = {
        "format": FORMAT,
        "result": "failed" if error else "deployed",
        "deployed_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployed_by": by,
        "from": where_from(),
        "deployment": name,
        "commit": commit,
        "image": image,
        "gapp": definition.get("gapp"),
        "gapp_installed": installed,
        "upgraded_from": upgraded_from,
        "definition": definition,
    }
    if error:
        entry["error"] = error
    return entry


def dump(entry: dict) -> str:
    return yaml.safe_dump(entry, sort_keys=False, default_flow_style=False)


def read_latest(provider, project: str, deployment_id: str) -> dict | None:
    """The last successful deploy's record, or None when there is none."""
    text = provider.read_object(bucket(project), latest_path(deployment_id))
    if not text or not text.strip():
        return None
    found = yaml.safe_load(text)
    return found if isinstance(found, dict) else None
