"""gapp status — solution health across infrastructure, service, and MCP layers."""

import json
import os
import shutil
import subprocess
from pathlib import Path

from gapp.sdk.context import resolve_solution
from gapp.sdk.deploy import _get_staging_dir, _get_tf_source_dir
from gapp.sdk.manifest import get_auth_config, get_mcp_path, load_manifest
from gapp.sdk.tokens import create_status_token


def get_status(name: str | None = None) -> dict:
    """Full status check for a solution.

    Returns dict with:
        name, project_id — solution identity
        deployed — whether TF state shows a full deployment
        services — list of {name, url, healthy, tools}
    """
    ctx = resolve_solution(name)
    if not ctx:
        return {"error": "not_found"}

    result = {
        "name": ctx["name"],
        "project_id": ctx.get("project_id"),
        "repo_path": ctx.get("repo_path"),
        "deployed": False,
        "services": [],
    }

    if not ctx.get("project_id"):
        return result

    # Read manifest for mcp_path config
    mcp_path = None
    auth_enabled = False
    if ctx.get("repo_path"):
        manifest = load_manifest(Path(ctx["repo_path"]))
        mcp_path = get_mcp_path(manifest)
        auth_enabled = bool(get_auth_config(manifest))

    # Query Terraform state for deployment info
    tf_outputs = _get_tf_outputs(ctx["name"], ctx["project_id"])
    if tf_outputs is None:
        return result

    result["deployed"] = True

    service_url = tf_outputs.get("service_url")
    if service_url:
        service = {
            "name": ctx["name"],
            "url": service_url,
            "healthy": None,
            "auth_enabled": auth_enabled,
            "tools": None,
        }

        # Health check
        service["healthy"] = _check_health(service_url)

        # MCP tools enumeration (only if mcp_path configured and service healthy)
        if mcp_path and service["healthy"]:
            service["mcp_path"] = mcp_path
            service["tools"] = _list_mcp_tools(
                service_url, mcp_path,
                solution_name=ctx["name"],
                project_id=ctx["project_id"] if auth_enabled else None,
            )

        result["services"].append(service)

    return result


def _get_tf_outputs(solution_name: str, project_id: str) -> dict | None:
    """Read Terraform outputs from remote state without applying.

    Initializes TF in the staging dir (if not already initialized) and
    reads outputs. Returns None if no state exists.
    """
    staging_dir = _get_staging_dir(solution_name)
    bucket_name = f"gapp-{solution_name}-{project_id}"

    # Ensure staging dir has TF files
    if not staging_dir.exists() or not (staging_dir / "main.tf").exists():
        staging_dir.mkdir(parents=True, exist_ok=True)
        tf_source = _get_tf_source_dir()
        for tf_file in tf_source.glob("*.tf"):
            shutil.copy2(tf_file, staging_dir)

    # Get access token
    token_result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
    )
    if token_result.returncode != 0:
        return None
    token = token_result.stdout.strip()
    env = {**os.environ, "GOOGLE_OAUTH_ACCESS_TOKEN": token}

    # Init (needed to connect to remote state)
    init_result = subprocess.run(
        ["terraform", "init",
         f"-backend-config=bucket={bucket_name}",
         "-backend-config=prefix=terraform/state",
         "-input=false",
         "-upgrade"],
        cwd=staging_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if init_result.returncode != 0:
        return None

    # Read outputs as JSON
    output_result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=staging_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    if output_result.returncode != 0:
        return None

    try:
        raw = json.loads(output_result.stdout)
    except json.JSONDecodeError:
        return None

    # terraform output -json returns {"key": {"value": ..., "type": ...}}
    if not raw:
        return None

    return {k: v.get("value") for k, v in raw.items()}


def _check_health(service_url: str) -> bool:
    """Hit /health and return True if 200."""
    try:
        result = subprocess.run(
            ["curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
             f"{service_url}/health"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "200"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _list_mcp_tools(
    service_url: str,
    mcp_path: str,
    *,
    solution_name: str | None = None,
    project_id: str | None = None,
) -> list[str] | None:
    """Call MCP initialize + tools/list to enumerate available tools.

    If project_id is provided, mints a short-lived status JWT to
    authenticate through the credential mediation middleware.
    """
    endpoint = f"{service_url}{mcp_path}"

    # Mint a status token if auth is needed
    auth_headers = []
    if project_id and solution_name:
        try:
            token = create_status_token(solution_name, project_id)
            auth_headers = ["-H", f"Authorization: Bearer {token}"]
        except Exception:
            return None  # Can't reach authenticated endpoint without a token

    # Step 1: initialize — get session ID from response header
    init_payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "gapp-status", "version": "0.1.0"},
        },
    })

    init_result = subprocess.run(
        ["curl", "-sf",
         "-X", "POST",
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         *auth_headers,
         "-D", "-",  # dump headers to stdout
         "--data", init_payload,
         endpoint],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if init_result.returncode != 0:
        return None

    # Parse session ID from response headers
    session_id = None
    header_section, _, body = init_result.stdout.partition("\r\n\r\n")
    for line in header_section.splitlines():
        if line.lower().startswith("mcp-session-id:"):
            session_id = line.split(":", 1)[1].strip()
            break

    if not session_id:
        session_id = _extract_session_from_sse(header_section)

    # Step 2: tools/list
    tools_payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
    })

    tools_headers = ["-H", "Content-Type: application/json",
                     "-H", "Accept: application/json, text/event-stream"]
    if session_id:
        tools_headers += ["-H", f"Mcp-Session-Id: {session_id}"]

    tools_result = subprocess.run(
        ["curl", "-sf",
         "-X", "POST",
         *tools_headers,
         *auth_headers,
         "--data", tools_payload,
         endpoint],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if tools_result.returncode != 0:
        return None

    return _parse_tools_response(tools_result.stdout)


def _extract_session_from_sse(headers: str) -> str | None:
    """Try to extract Mcp-Session-Id from response headers."""
    for line in headers.splitlines():
        lower = line.lower()
        if "mcp-session-id" in lower and ":" in line:
            return line.split(":", 1)[1].strip()
    return None


def _parse_tools_response(body: str) -> list[str] | None:
    """Parse tool names from MCP tools/list response.

    Handles both direct JSON and SSE (text/event-stream) formats.
    """
    # Try direct JSON first
    try:
        data = json.loads(body)
        tools = data.get("result", {}).get("tools", [])
        return [t["name"] for t in tools]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    # Try SSE format: look for "data:" lines containing JSON
    for line in body.splitlines():
        if line.startswith("data:"):
            json_str = line[5:].strip()
            try:
                data = json.loads(json_str)
                tools = data.get("result", {}).get("tools", [])
                return [t["name"] for t in tools]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    return None
