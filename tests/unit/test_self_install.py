"""Client registration commands for the admin MCP server."""

import subprocess

import pytest

from gapp.admin.sdk import self_install


def _capture(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="gapp-admin: gapp-mcp\n", stderr="")

    monkeypatch.setattr(self_install.subprocess, "run", fake_run)
    return calls


def test_claude_registration_passes_scope(monkeypatch):
    calls = _capture(monkeypatch)
    res = self_install.install_admin_mcp("claude", scope="project")
    assert calls == [["claude", "mcp", "add", "-s", "project", "gapp-admin", "gapp-mcp"]]
    assert res["success"] is True and res["client"] == "claude"


def test_antigravity_registration_uses_agy_without_scope(monkeypatch):
    calls = _capture(monkeypatch)
    res = self_install.install_admin_mcp("agy")
    assert calls == [["agy", "mcp", "add", "gapp-admin", "--", "gapp-mcp"]]
    assert res["command"] == "agy mcp add gapp-admin -- gapp-mcp"


def test_antigravity_check_lists_servers(monkeypatch):
    calls = _capture(monkeypatch)
    assert self_install.check_admin_mcp_registration("agy") is True
    assert calls == [["agy", "mcp", "list"]]


def test_unknown_client_rejected():
    with pytest.raises(ValueError, match="agy"):
        self_install.install_admin_mcp("gemini")
