"""gapp admin install — register gapp's admin MCP server with agent clients."""

import subprocess

CLIENTS = ("claude", "agy")


def install_admin_mcp(client: str, scope: str = "user") -> dict:
    """Register the gapp-admin MCP server with an agent client.

    Args:
        client: "claude" (Claude Code) or "agy" (Antigravity CLI).
        scope: "user" or "project". Claude Code honours it; Antigravity
            registers at user level only (its mcp_config.json has no
            scope), so the value is recorded but not passed through.

    Returns dict with: client, scope, success, command, output
    """
    if client not in CLIENTS:
        raise ValueError(f"Unknown client: {client}. Use one of {', '.join(CLIENTS)}.")
    if scope not in ("user", "project"):
        raise ValueError(f"Unknown scope: {scope}. Use 'user' or 'project'.")

    if client == "claude":
        cmd = ["claude", "mcp", "add", "-s", scope, "gapp-admin", "gapp-mcp"]
    else:
        cmd = ["agy", "mcp", "add", "gapp-admin", "--", "gapp-mcp"]

    result = subprocess.run(cmd, capture_output=True, text=True)

    return {
        "client": client,
        "scope": scope,
        "success": result.returncode == 0,
        "command": " ".join(cmd),
        "output": result.stdout.strip() if result.stdout.strip() else result.stderr.strip(),
    }


def check_admin_mcp_registration(client: str, scope: str = "user") -> bool:
    """Check if gapp-admin is registered with a client at the given scope."""
    if client == "claude":
        cmd = ["claude", "mcp", "list", "-s", scope]
    elif client == "agy":
        cmd = ["agy", "mcp", "list"]
    else:
        return False

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return "gapp-admin" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
