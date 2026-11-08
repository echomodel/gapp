"""Shared utilities — gcloud invocation, git introspection."""

import os
import subprocess
from pathlib import Path


def configured_account() -> str | None:
    """The account from `gapp config account`, or None when unset."""
    from gapp.admin.sdk.config import get_active_config
    return get_active_config().get("account") or None


def run_gcloud(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a gcloud command as the configured account when one is set.

    Every gcloud invocation in gapp goes through here (or through the
    provider's equivalent), so `gapp config account <email>` wins over
    gcloud's own active account on every call, not just some.
    """
    account = configured_account()
    if account:
        env = kwargs.get("env") or os.environ.copy()
        env["CLOUDSDK_CORE_ACCOUNT"] = account
        kwargs["env"] = env
    return subprocess.run(["gcloud"] + args, **kwargs)


def require_gcloud_account() -> str | None:
    """Fail early, and by name, when the configured account has no login.

    Returns the configured account (or None when none is configured, in
    which case gcloud's active account is used and nothing is checked).
    Raises RuntimeError naming the account and the login command when it
    is configured but gcloud holds no credential for it.
    """
    account = configured_account()
    if not account:
        return None
    res = run_gcloud(
        ["auth", "list", "--filter", f"account:{account}", "--format", "value(account)"],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or account.lower() not in (res.stdout or "").lower():
        raise RuntimeError(
            f"gapp is configured to use the account {account} (gapp config "
            f"account), but gcloud has no credential for it. Run `gcloud auth login "
            f"{account}`, or point gapp at a logged-in account with `gapp config account <email>`."
        )
    return account


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
