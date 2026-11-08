"""Unit-test fixtures shared by the `gapp ci` tests.

`gapp ci` talks to GitHub only through the `gh` CLI, so the tests swap
`subprocess.run` for an in-memory GitHub that answers the handful of API
shapes the SDK uses: repo visibility, the CI repo's workflow files, and
its Actions variables and secrets. Nothing touches the network.
"""

import base64
import json
import subprocess

import pytest


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeGitHub:
    """In-memory stand-in for the GitHub REST API behind `gh api`."""

    def __init__(self):
        self.private = {}       # "owner/repo" -> bool
        self.files = {}         # ("owner/repo", path) -> content
        self.variables = set()  # ("owner/repo", NAME)
        self.secrets = set()    # ("owner/repo", NAME)
        self.calls = []         # every command seen
        self.puts = []          # (repo, path, content) for every write

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        assert cmd[0] == "gh", f"only gh may be invoked, got {cmd}"
        assert "clone" not in cmd
        if cmd[:2] != ["gh", "api"]:
            return self.other(cmd, kwargs)
        if "-X" in cmd and "PUT" in cmd:
            endpoint = cmd[cmd.index("PUT") + 1]
            return self._put(endpoint, json.loads(kwargs["input"]))
        endpoint = cmd[2]
        return self._get(endpoint, cmd)

    def other(self, cmd, kwargs):
        raise AssertionError(f"unexpected gh command {cmd}")

    def _get(self, endpoint, cmd):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        rest = parts[3:]
        if not rest:
            if repo not in self.private:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".private":
                return _Result(0, "true\n" if self.private[repo] else "false\n")
            return _Result(0, json.dumps({"private": self.private[repo]}))
        if rest[0] == "contents":
            path = "/".join(rest[1:])
            content = self.files.get((repo, path))
            if content is None:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            return _Result(0, json.dumps({
                "name": path.rsplit("/", 1)[-1], "sha": f"sha-{len(content)}",
                "content": base64.b64encode(content.encode()).decode(),
            }))
        if rest[0] == "actions" and len(rest) == 3:
            store = self.variables if rest[1] == "variables" else self.secrets
            if (repo, rest[2]) in store:
                return _Result(0, json.dumps({"name": rest[2]}))
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        return self.other(cmd, {})

    def _put(self, endpoint, payload):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        path = "/".join(parts[4:])
        content = base64.b64decode(payload["content"]).decode()
        self.files[(repo, path)] = content
        self.puts.append((repo, path, content))
        return _Result(0, "{}")


@pytest.fixture
def fake_gh(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(subprocess, "run", gh.run)
    return gh
