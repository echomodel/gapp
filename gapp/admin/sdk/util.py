"""Shared utilities — gcloud invocation, git introspection."""

import os
import subprocess
from pathlib import Path


def run_gcloud(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a gcloud command, forcing the configured account if set."""
    from gapp.admin.sdk.config import get_active_config
    account = get_active_config().get("account")
    if account:
        env = kwargs.get("env") or os.environ.copy()
        env["CLOUDSDK_CORE_ACCOUNT"] = account
        kwargs["env"] = env
    return subprocess.run(["gcloud"] + args, **kwargs)


def get_git_root(path: Path | None = None) -> Path | None:
    """Find the git root directory from the given path or cwd."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=path or Path.cwd(),
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except FileNotFoundError:
        pass
    return None


def get_staging_dir(name: str) -> Path:
    """Return the local staging directory for a service's terraform state."""
    return Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "gapp" / name / "terraform"
