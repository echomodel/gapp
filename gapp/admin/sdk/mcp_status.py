"""gapp MCP operations — tool enumeration, connection info, client config."""

import json
import subprocess
from pathlib import Path

from gapp.admin.sdk.context import resolve_solution
from gapp.admin.sdk.manifest import get_mcp_path, load_manifest
from gapp.admin.sdk.models import (
    ClaudeAiConfig, ClientConfig, ClientConfigs, ClientScope,
    ConnectResult, McpSolution, McpStatusResult, NextStep,
)
from gapp.admin.sdk.solutions import list_solutions
from gapp.admin.sdk.status import (
    _check_health, _get_tf_outputs,
    TerraformNotFoundError, GcloudNotFoundError,
)


def mcp_status(name: str | None = None) -> McpStatusResult:
    """MCP-specific status: health, MCP URL, and tool enumeration."""
    ctx = resolve_solution(name)
    if not ctx:
        return McpStatusResult(
            name=name or "",
            error="not_found",
            next_step=NextStep(action="init", hint="Not inside a gapp solution."),
        )

    result = McpStatusResult(
        name=ctx["name"],
        project_id=ctx.get("project_id"),
    )

    if not ctx.get("project_id"):
        result.next_step = NextStep(action="setup", hint="No GCP project attached.")
        return result

    mcp_path = None
    if ctx.get("repo_path"):
        manifest = load_manifest(Path(ctx["repo_path"]).expanduser())
        mcp_path = get_mcp_path(manifest)

    if not mcp_path:
        result.next_step = NextStep(action="configure", hint="No mcp_path configured in gapp.yaml.")
        return result

    try:
        tf_outputs = _get_tf_outputs(ctx["name"], ctx["project_id"])
    except (TerraformNotFoundError, GcloudNotFoundError) as e:
        result.next_step = NextStep(action="deploy", hint=str(e))
        return result
    if tf_outputs is None:
        result.next_step = NextStep(action="deploy", hint="Not deployed.")
        return result

    result.deployed = True
    service_url = tf_outputs.get("service_url")
    if not service_url:
        return result

    result.url = service_url
    result.mcp_url = f"{service_url}{mcp_path}"
    result.healthy = _check_health(service_url)

    if result.healthy:
        result.tools = _list_mcp_tools(service_url, mcp_path)

    return result


def mcp_list() -> list[McpSolution]:
    """List solutions that have mcp_path configured."""
    all_solutions = list_solutions(include_remote=False)
    results = []
    for s in all_solutions:
        repo_path = s.get("repo_path")
        if repo_path:
            manifest = load_manifest(Path(repo_path).expanduser())
            path = get_mcp_path(manifest)
            if path:
                results.append(McpSolution(
                    name=s["name"],
                    project_id=s.get("project_id"),
                    mcp_path=path,
                    repo_path=repo_path,
                ))
    return results


def mcp_connect(name: str | None = None) -> ConnectResult:
    """Generate MCP client connection info.

    The token placeholder `<YOUR_PAT>` is emitted in command strings —
    obtaining an actual token for an auth-gated solution is the solution
    framework's responsibility, not gapp's.
    """
    ctx = resolve_solution(name)
    if not ctx:
        return ConnectResult(
            name=name or "",
            error="not_found",
            next_step=NextStep(action="init", hint="Not inside a gapp solution."),
        )

    result = ConnectResult(
        name=ctx["name"],
        project_id=ctx.get("project_id"),
    )

    if not ctx.get("project_id"):
        result.next_step = NextStep(action="setup", hint="No GCP project attached.")
        return result

    mcp_path = None
    if ctx.get("repo_path"):
        manifest = load_manifest(Path(ctx["repo_path"]).expanduser())
        mcp_path = get_mcp_path(manifest)

    if not mcp_path:
        result.next_step = NextStep(action="configure", hint="No mcp_path configured in gapp.yaml.")
        return result

    try:
        tf_outputs = _get_tf_outputs(ctx["name"], ctx["project_id"])
    except (TerraformNotFoundError, GcloudNotFoundError) as e:
        result.next_step = NextStep(action="deploy", hint=str(e))
        return result
    if tf_outputs is None:
        result.next_step = NextStep(action="deploy", hint="Not deployed.")
        return result

    result.deployed = True
    service_url = tf_outputs.get("service_url")
    if not service_url:
        return result

    result.url = service_url
    result.mcp_url = f"{service_url}{mcp_path}"
    result.healthy = _check_health(service_url)

    if result.healthy:
        result.tools = _list_mcp_tools(service_url, mcp_path)

    token_display = "<YOUR_PAT>"
    result.token_masked = token_display

    mcp_url = result.mcp_url
    solution_name = ctx["name"]

    claude_user = _check_claude_registration(solution_name, "user")
    claude_project = _check_claude_registration(solution_name, "project")
    gemini_user = _check_gemini_registration(solution_name, "user")
    gemini_project = _check_gemini_registration(solution_name, "project")

    result.clients = ClientConfigs(
        claude_code=ClientConfig(
            user=ClientScope(
                registered=claude_user,
                command=(
                    f'claude mcp add --transport http --header '
                    f'"Authorization: Bearer {token_display}" '
                    f'-s user {solution_name} {mcp_url}'
                ),
            ),
            project=ClientScope(
                registered=claude_project,
                command=(
                    f'claude mcp add --transport http --header '
                    f'"Authorization: Bearer {token_display}" '
                    f'-s project {solution_name} {mcp_url}'
                ),
            ),
        ),
        gemini_cli=ClientConfig(
            user=ClientScope(
                registered=gemini_user,
                command=(
                    f'gemini mcp add {solution_name} {mcp_url} '
                    f'--scope user --transport http '
                    f'--header "Authorization: Bearer {token_display}"'
                ),
            ),
            project=ClientScope(
                registered=gemini_project,
                command=(
                    f'gemini mcp add {solution_name} {mcp_url} '
                    f'--scope project --transport http '
                    f'--header "Authorization: Bearer {token_display}"'
                ),
            ),
        ),
        claude_ai=ClaudeAiConfig(
            url=f"{mcp_url}?token={token_display}",
        ),
    )

    return result


def _check_claude_registration(name: str, scope: str) -> bool:
    try:
        result = subprocess.run(
            ["claude", "mcp", "list", "-s", scope],
            capture_output=True, text=True, timeout=5,
        )
        return name in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_gemini_registration(name: str, scope: str) -> bool:
    try:
        result = subprocess.run(
            ["gemini", "mcp", "list", "--scope", scope],
            capture_output=True, text=True, timeout=5,
        )
        return name in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _list_mcp_tools(service_url: str, mcp_path: str) -> list[str] | None:
    """Call MCP initialize + tools/list to enumerate available tools.

    Probes unauthenticated — returns None if the service requires auth.
    """
    endpoint = f"{service_url}{mcp_path}"
    auth_headers: list[str] = []

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
        ["curl", "-sf", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-H", "Accept: application/json, text/event-stream",
         *auth_headers, "-D", "-",
         "--data", init_payload, endpoint],
        capture_output=True, text=True, timeout=15,
    )
    if init_result.returncode != 0:
        return None

    session_id = None
    header_section, _, body = init_result.stdout.partition("\r\n\r\n")
    for line in header_section.splitlines():
        if line.lower().startswith("mcp-session-id:"):
            session_id = line.split(":", 1)[1].strip()
            break

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
        ["curl", "-sf", "-X", "POST",
         *tools_headers, *auth_headers,
         "--data", tools_payload, endpoint],
        capture_output=True, text=True, timeout=15,
    )
    if tools_result.returncode != 0:
        return None

    return _parse_tools_response(tools_result.stdout)


def _parse_tools_response(body: str) -> list[str] | None:
    try:
        data = json.loads(body)
        tools = data.get("result", {}).get("tools", [])
        return [t["name"] for t in tools]
    except (json.JSONDecodeError, KeyError, TypeError):
        pass

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
