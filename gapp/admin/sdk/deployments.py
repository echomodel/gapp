"""What is deployed, across the fleet and across gapp's projects.

`gapp deployments list` starts from the fleet and reads each deployment's
deploy record; `gapp deployments scan` starts from the cloud, finds the
projects gapp has set up and every deploy record in them, and says which
the fleet does not hold. Both only report: neither refuses a deployment
for its version, and neither changes anything.

The cost of each is bounded before anything is read, and that is what
the options are for (CONTRIBUTING, "Listing and scanning deployments"):

- `list` narrows on what the fleet says (name, repo, project, version),
  which costs nothing, then reads one small record per deployment left,
  at most `limit`, in parallel. It orders by name only: ordering by
  anything deployed would mean reading every record first.
- `scan` finds gapp's projects with one search that Google Cloud filters on its
  side, then lists and reads the records in each. It stops, rather than
  running long, when the search finds more than SCAN_MAX_PROJECTS.
"""

from __future__ import annotations

import fnmatch
from concurrent.futures import ThreadPoolExecutor

import yaml

from gapp import COMPATIBLE_FROM, __version__
from gapp.admin.sdk import definitions, record, versions
from gapp.admin.sdk.cloud.base import CloudAccessError, GcloudNotInstalled
from gapp.admin.sdk.definitions import Refused
from gapp.admin.sdk.entryfile import EntryFileRefusal, version_key

READERS = 8               # records read at once
SCAN_MAX_PROJECTS = 50   # more than this and scan asks for --project
LATEST = "deployments/*/deployed.yaml"


def _standing(defined: str | None, deployed: str | None) -> str:
    """One phrase for where a deployment stands against this gapp."""
    stands = [versions.standing(v) for v in (defined, deployed) if v]
    for state in ("newer", "too-old", "upgrade-required"):
        if state in stands:
            return versions.UPGRADE_WORDS[state] if state != "upgrade-required" else "upgrade required"
    if defined and deployed and version_key(defined) > version_key(deployed):
        return "upgrade pending"
    if "compatible" in stands:
        return "upgrade available"
    return "current"


def _read(provider, project: str, deployment_id: str) -> tuple[dict | None, str | None, bool]:
    """A deployment's last successful deploy, or the reason it could not be
    read, and whether that reason was Google Cloud refusing or not being
    reachable (which `access` then explains once, under the list)."""
    try:
        return record.read_latest(provider, project, deployment_id), None, False
    except GcloudNotInstalled:
        return None, "gcloud isn't installed on this machine", True
    except CloudAccessError as e:
        return None, str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__, True
    except yaml.YAMLError as e:
        return None, str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__, False


def _in_parallel(fn, items: list) -> list:
    with ThreadPoolExecutor(max_workers=READERS) as pool:
        return list(pool.map(fn, items))


@definitions._guard
def list_deployments(provider, *, name: str | None = None, repo: str | None = None, project: str | None = None,
                     below: str | None = None, limit: int | None = None) -> dict:
    """The fleet's deployments with the gapp of each one's last deploy.

    Filters on the fleet (name, a wildcard pattern; repo; project) cost
    nothing. `below` keeps a deployment whose definition or last deploy is
    on an older gapp than that version. `limit` caps how many records are
    read, in name order. A record that cannot be read is reported on its
    row; the rest still are.
    """
    if below is not None:
        version_key(below)
    if limit is not None and limit < 1:
        raise Refused("--limit takes a number of deployments, 1 or more.")
    where, deployments = definitions._load()
    chosen = []
    for dep_name in sorted(deployments):
        d = deployments[dep_name]
        if name and not fnmatch.fnmatchcase(dep_name, name):
            continue
        if repo and (d.get("repo") or "").lower() != repo.lower():
            continue
        if project and d.get("project") != project:
            continue
        chosen.append((dep_name, d))
    limited = limit is not None and len(chosen) > limit
    chosen = chosen[:limit] if limit is not None else chosen
    found = _in_parallel(lambda item: _read(provider, item[1].get("project"), item[1].get("id")), chosen)
    rows = []
    for (dep_name, d), (last, problem, _) in zip(chosen, found):
        deployed = (last or {}).get("gapp")
        if below:
            older = [v for v in (d.get("gapp"), deployed) if v and version_key(v) < version_key(below)]
            if not older:
                continue
        try:
            stands = "couldn't read its deploy record" if problem else _standing(d.get("gapp"), deployed)
        except EntryFileRefusal:
            stands = "not a gapp version"
        rows.append({"deployment": dep_name, "project": d.get("project"), "repo": d.get("repo"),
                     "definition": d.get("gapp"), "deployed": deployed,
                     "deployed_at": (last or {}).get("deployed_at"), "stands": stands, "problem": problem})
    access = provider.identity_hint() if any(refused for _, _, refused in found) else None
    return {"fleet": where.describe(), "this_gapp": __version__, "compatible_from": COMPATIBLE_FROM,
            "read": len(chosen), "limited": limited, "deployments": rows, "access": access}


@definitions._guard
def scan(provider, *, project: str | None = None) -> dict:
    """Every deployment gapp has a deploy record for, in one project or in
    every project gapp has set up that this login can see, and whether the
    fleet holds it. One that it does not comes with the commands that add
    it back."""
    from gapp.admin.sdk.core import HOSTED_LABEL, HOSTED_VALUE
    if project:
        projects = [project]
    else:
        projects = provider.search_projects_by_label(HOSTED_LABEL, HOSTED_VALUE)
        if len(projects) > SCAN_MAX_PROJECTS:
            raise Refused(f"{len(projects)} projects you can see are set up for gapp, more than a scan reads at once "
                          f"({SCAN_MAX_PROJECTS}). Scan one at a time: gapp deployments scan --project <project>")
    where, deployments = definitions._load()
    held = {(d.get("project"), d.get("id")): n for n, d in deployments.items()}

    def one_project(p: str) -> dict:
        try:
            paths = provider.list_object_paths(record.bucket(p), LATEST)
        except CloudAccessError as e:
            return {"project": p, "problem": str(e).strip().splitlines()[0], "deployments": []}
        found = []
        for path in paths:
            deployment_id = path.split("/")[1]
            last, problem, _ = _read(provider, p, deployment_id)
            last = last or {}
            in_fleet = held.get((p, deployment_id))
            definition = last.get("definition") if isinstance(last.get("definition"), dict) else {}
            definition = {**definition, "id": deployment_id, "project": p}
            found.append({"project": p, "id": deployment_id, "deployed": last.get("gapp"),
                          "deployed_at": last.get("deployed_at"), "in_fleet": in_fleet,
                          "add_again": None if in_fleet else definitions.add_again(definition, last.get("deployment")),
                          "problem": problem})
        return {"project": p, "problem": None, "deployments": found}

    scanned = _in_parallel(one_project, projects)
    # An empty search is also what a login that can see none of gapp's projects gets, so say whose it was.
    searched_as = provider.active_account() if project is None and not projects else None
    return {"fleet": where.describe(), "projects": scanned, "searched": project is None, "searched_as": searched_as,
            "found": sum(len(s["deployments"]) for s in scanned),
            "not_in_fleet": sum(1 for s in scanned for d in s["deployments"] if not d["in_fleet"])}
