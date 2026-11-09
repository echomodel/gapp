"""What the fleet repo's status workflow runs on the runner.

Kept apart from `ci.py` because it is the one place the CI system touches
the direct system's code: on the runner, authenticated as the project's
deploy identity, it asks `gapp status` about the deployment's id and
prints the answer as one line for `gapp ci status --live` to read. Nothing
is stored; live state is asked of the project, never written to the fleet
repo.
"""

from __future__ import annotations

import json
import os

STATUS_LOG_PREFIX = "gapp-status:"


def runner_status(env: dict | None = None, out=print) -> int:
    """Print `gapp-status: result=<json>`: the root `gapp status` result for
    SOLUTION_ID on PROJECT, the same dict `gapp_status` returns."""
    from gapp.admin.sdk.core import GappSDK

    env = dict(os.environ if env is None else env)
    solution_id = (env.get("SOLUTION_ID") or "").strip()
    project = (env.get("PROJECT") or "").strip() or None
    if not solution_id:
        out(f"{STATUS_LOG_PREFIX} error=no SOLUTION_ID was given to the status run.")
        return 1
    try:
        data = GappSDK().status(name=solution_id, project=project).model_dump()
    except Exception as e:  # noqa: BLE001 - the run reports in words, never a traceback
        out(f"{STATUS_LOG_PREFIX} error={str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__}")
        return 1
    out(f"{STATUS_LOG_PREFIX} result={json.dumps(data, default=str)}")
    return 0
