"""How a command gets the deployment it works on, version-checked.

Two groups of commands, two ways in, and no other:

- `load_definition`: commands that read or change only the definition in
  the fleet (show, set, unset, env, apis, apply, rename, remove, add
  --copy-of). Checks the definition's version. Never touches a cloud.
- `load_deployment`: commands that touch the deployment's infrastructure
  (deploy, status, secrets, and the same on a runner). Checks the
  definition's version and the version of the gapp that last deployed it,
  read from the deploy record in the project.

`gapp upgrade` is the one command that reads a definition below
COMPATIBLE_FROM on purpose, and it applies UPGRADES_FROM itself.
CONTRIBUTING, "Commands that touch a deployment's infrastructure", lists
the second group; tests/unit/test_loading.py checks every command in it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gapp.admin.sdk import definitions, fleetstore, record, versions


@dataclass
class Loaded:
    """A definition, where its fleet lives, and the whole fleet it came from."""
    where: fleetstore.Location
    deployments: dict
    name: str
    definition: dict


@dataclass
class LoadedDeployment(Loaded):
    """A definition with what the project says about it: its gapp bucket
    (None when the project has none yet), the project's buckets, and its
    last successful deploy (None when there is none)."""
    project_bucket: str | None
    buckets: dict
    last_deploy: dict | None

    @property
    def last_deployed_by(self) -> str | None:
        return (self.last_deploy or {}).get("gapp")

    @property
    def upgrade_pending(self) -> bool:
        return versions.upgrade_pending(self.definition, self.last_deployed_by)


def load_definition(deployment: str | None, cwd: Path | None, command: str, to_do: str,
                    *, read_only: bool = False) -> Loaded:
    """The definition a command means, refused unless this gapp works with
    its version. `read_only` (show): any version but a newer one, so a
    definition that needs an upgrade can still be looked at."""
    where, deployments = definitions._load()
    name = definitions.which(deployments, deployment, cwd, command)
    definition = deployments[name]
    if read_only:
        versions.check_readable(definition, name, to_do)
    else:
        versions.check_definition(definition, name, to_do)
    return Loaded(where, deployments, name, definition)


def load_deployment(provider, deployment: str | None, cwd: Path | None, command: str, to_do: str,
                    *, upgrading: bool = False) -> LoadedDeployment:
    """The deployment a command means, with its deploy record, refused
    unless this gapp works with both its definition's and its
    infrastructure's versions. `upgrading`: a deploy with --upgrade, which
    may carry infrastructure from UPGRADES_FROM on. Raises CloudAccessError
    when the project cannot be read."""
    return with_infrastructure(provider, load_definition(deployment, cwd, command, to_do), to_do,
                               upgrading=upgrading)


def with_infrastructure(provider, loaded: Loaded, to_do: str, *, upgrading: bool = False) -> LoadedDeployment:
    """`load_deployment` in two steps, for a command that checks what it can
    on this machine before reading the project (a deploy checks the clone
    first): the project's side of an already loaded definition."""
    project, deployment_id = loaded.definition["project"], loaded.definition["id"]
    buckets = provider.list_bucket_labels(project)
    project_bucket = record.bucket(project) if record.bucket(project) in buckets else None
    last = record.read_latest(provider, project, deployment_id) if project_bucket else None
    versions.check_infrastructure((last or {}).get("gapp"), loaded.name, to_do, upgrading=upgrading)
    return LoadedDeployment(loaded.where, loaded.deployments, loaded.name, loaded.definition,
                            project_bucket, buckets, last)
