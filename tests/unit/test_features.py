"""Tests for the bundled feature-flag loader."""

from gapp.admin.sdk import features


def test_one_step_deploy_default_off():
    """Shipped default: the one-step deploy path is gated off."""
    assert features.is_enabled("allow_one_step_deploy_tool") is False


def test_unknown_flag_defaults_false():
    """Unknown flags fail safe — new behavior stays off by default."""
    assert features.is_enabled("never_defined_flag") is False


def test_mcp_gapp_deploy_requires_build_ref_when_flag_off(monkeypatch):
    """With the flag off (shipped default), gapp_deploy(MCP) without a
    build_ref must return an error payload rather than invoking the
    blocking build+deploy path."""
    from gapp.admin.mcp import server

    called = []
    def fake_deploy(**kw):
        called.append(kw)
        return {"ok": True}
    monkeypatch.setattr("gapp.admin.sdk.deploy.deploy_solution", fake_deploy)

    result = server.gapp_deploy(solution="whatever")

    assert called == []
    assert result.get("error") == "one_step_deploy_disabled"
    assert "gapp_build" in result["message"]


def test_mcp_gapp_deploy_passes_through_with_build_ref(monkeypatch):
    """With a build_ref supplied, the flag is irrelevant — request passes through."""
    from gapp.admin.mcp import server

    called = []
    def fake_deploy(**kw):
        called.append(kw)
        return {"ok": True, "terraform_status": "applied"}
    monkeypatch.setattr("gapp.admin.sdk.deploy.deploy_solution", fake_deploy)

    result = server.gapp_deploy(solution="whatever", build_ref="abc123")

    assert len(called) == 1
    assert called[0]["build_ref"] == "abc123"
    assert result == {"ok": True, "terraform_status": "applied"}
