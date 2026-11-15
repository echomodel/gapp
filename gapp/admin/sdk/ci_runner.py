"""What the fleet repo's workflows run on a runner.

A workflow checks out the fleet repo and the app's repo, installs the gapp
version the deployment's definition names, signs in to Google Cloud as the
project's deploy identity, and then runs one of these with the
deployment's name and nothing else. Everything else comes from the
definition, read from the fleet file the workflow names in GAPP_FLEET.

Each prints one line a person can read and `gapp ci` can find in the run's
log: `<prefix> result=<json>`, `<prefix> refused=<why>` or `<prefix>
error=<why>`. The exit code is 0, 2 or 1 to match.
"""

from __future__ import annotations

import json

DEPLOY_LOG_PREFIX = "gapp-deploy:"
STATUS_LOG_PREFIX = "gapp-status:"


def _report(prefix: str, call, out) -> int:
    from gapp.admin.sdk.definitions import Refused
    try:
        result = call()
    except Refused as e:
        out(f"{prefix} refused={' '.join(e.message.split())}")
        return 2
    except Exception as e:  # noqa: BLE001 - the run reports in words, never a traceback
        text = str(e).strip()
        out(f"{prefix} error={text.splitlines()[0] if text else type(e).__name__}")
        return 1
    out(f"{prefix} result={json.dumps(result, default=str)}")
    return 0


WORKFLOWS_VERSION_ENV = "GAPP_WORKFLOWS_VERSION"


def _check_workflows() -> None:
    """Refuse workflows older than this gapp's COMPATIBLE_FROM. Workflows
    that do not say their version predate this check and are taken as
    they are."""
    import os
    import gapp
    from gapp.admin.sdk.definitions import Refused
    from gapp.admin.sdk.entryfile import EntryFileRefusal, version_key
    theirs = (os.environ.get(WORKFLOWS_VERSION_ENV) or "").strip()
    if not theirs:
        return
    try:
        too_old = version_key(theirs) < version_key(gapp.COMPATIBLE_FROM)
    except EntryFileRefusal:
        return
    if too_old:
        raise Refused(f"The workflows that started this run are gapp {theirs}'s, and gapp {gapp.__version__} works "
                      f"with workflows from {gapp.COMPATIBLE_FROM} on. Bring them up to date: gapp ci init")


def runner_deploy(deployment: str, upgrade: bool = False, out=print) -> int:
    """Deploy `deployment` from the app repo checkout the run is standing in."""
    from gapp.admin.sdk.core import GappSDK

    def deploy():
        _check_workflows()
        return GappSDK().deploy_definition(deployment, upgrade=upgrade)
    return _report(DEPLOY_LOG_PREFIX, deploy, out)


def runner_status(deployment: str, out=print) -> int:
    """Report what is live for `deployment`."""
    from gapp.admin.sdk.core import GappSDK

    def status():
        _check_workflows()
        return GappSDK().status_definition(deployment)
    return _report(STATUS_LOG_PREFIX, status, out)
