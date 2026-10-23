"""The CI workflow caller is written through the GitHub Contents API, never
by cloning, committing, or pushing on the operator's machine."""

import base64
import json
import subprocess

import pytest

from gapp.admin.sdk import ci as ci_mod

CONTENT = "name: Deploy demo\n'on': {}\n"


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _runner(calls, existing=None, put_rc=0):
    """Fake subprocess.run: GET returns `existing` (dict or None), PUT records payload."""
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        assert cmd[0] == "gh", f"only gh may be invoked, got {cmd}"
        assert "git" not in cmd and "clone" not in cmd
        if cmd[:2] == ["gh", "api"] and "-X" not in cmd:
            if existing is None:
                return _Result(1, "", "Not Found (HTTP 404)")
            return _Result(0, json.dumps(existing))
        if "PUT" in cmd:
            return _Result(put_rc, "{}", "boom" if put_rc else "")
        raise AssertionError(f"unexpected command {cmd}")
    return run


def test_creates_file_when_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _runner(calls, existing=None))
    assert ci_mod._write_workflow_to_ci_repo("acme/ci", "demo", CONTENT) == "pushed"
    put_cmd, put_kwargs = calls[-1]
    assert put_cmd[2:5] == ["-X", "PUT", "repos/acme/ci/contents/.github/workflows/demo.yml"]
    payload = json.loads(put_kwargs["input"])
    assert base64.b64decode(payload["content"]).decode() == CONTENT
    assert payload["message"] == "Add deploy workflow for demo"
    assert "sha" not in payload


def test_updates_file_when_content_differs(monkeypatch):
    calls = []
    existing = {"sha": "abc123", "content": base64.b64encode(b"old\n").decode()}
    monkeypatch.setattr(subprocess, "run", _runner(calls, existing=existing))
    assert ci_mod._write_workflow_to_ci_repo("acme/ci", "demo", CONTENT) == "updated"
    payload = json.loads(calls[-1][1]["input"])
    assert payload["sha"] == "abc123"
    assert payload["message"] == "Update deploy workflow for demo"


def test_unchanged_file_makes_no_write(monkeypatch):
    calls = []
    # GitHub wraps base64 at 60 columns; the decoder must tolerate that.
    wrapped = "\n".join(base64.b64encode(CONTENT.encode()).decode()[i:i + 60] for i in range(0, 200, 60))
    existing = {"sha": "abc123", "content": wrapped}
    monkeypatch.setattr(subprocess, "run", _runner(calls, existing=existing))
    assert ci_mod._write_workflow_to_ci_repo("acme/ci", "demo", CONTENT) == "unchanged"
    assert len(calls) == 1 and "PUT" not in calls[0][0]


def test_write_failure_is_actionable(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _runner([], existing=None, put_rc=1))
    with pytest.raises(RuntimeError, match="Failed to write .github/workflows/demo.yml to acme/ci"):
        ci_mod._write_workflow_to_ci_repo("acme/ci", "demo", CONTENT)
