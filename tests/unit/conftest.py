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
        self.environments = {}  # ("owner/repo", env) -> {VAR: value}
        self.calls = []         # every command seen
        self.puts = []          # (repo, path, content) for every write
        self.env_writes = []    # (method, repo, env, VAR or None)
        # Capabilities of the machine's GitHub access. A proxy may refuse
        # every API write and the Actions variables/secrets reads while
        # allowing contents reads and workflow dispatch.
        self.api_writes = True
        self.actions_api = True

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        assert cmd[0] == "gh", f"only gh may be invoked, got {cmd}"
        assert "clone" not in cmd
        if cmd[:2] != ["gh", "api"]:
            return self.other(cmd, kwargs)
        if "-X" in cmd:
            method = cmd[cmd.index("-X") + 1]
            endpoint = cmd[cmd.index("-X") + 2]
            if not self.api_writes:
                return _Result(1, "", "gh: HTTP 403: request blocked: GitHub API writes are not allowed from this session")
            payload = json.loads(kwargs["input"]) if kwargs.get("input") else None
            if "/environments/" in endpoint:
                return self._environment_write(method, endpoint, payload)
            assert method == "PUT", f"unexpected method {method} for {endpoint}"
            return self._put(endpoint, payload)
        endpoint = cmd[2]
        if "/actions/variables" in endpoint or "/actions/secrets" in endpoint or "/variables" in endpoint:
            if not self.actions_api:
                return _Result(1, "", "gh: HTTP 403: request blocked: Actions variables and secrets are not readable from this session")
        return self._get(endpoint, cmd)

    # -- GitHub Environments and their variables --

    def _split_env(self, endpoint):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        env = parts[4]
        var = parts[6] if len(parts) > 6 else None
        return repo, env, var

    def _environment_write(self, method, endpoint, payload):
        repo, env, var = self._split_env(endpoint)
        if method == "PUT":                      # create or update the environment
            self.environments.setdefault((repo, env), {})
            self.env_writes.append(("PUT", repo, env, None))
            return _Result(0, json.dumps({"name": env}))
        if (repo, env) not in self.environments:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        store = self.environments[(repo, env)]
        if method == "POST":                     # create a variable
            if payload["name"] in store:
                return _Result(1, "", "gh: Variable already exists (HTTP 409)")
            store[payload["name"]] = payload["value"]
            self.env_writes.append(("POST", repo, env, payload["name"]))
            return _Result(0, "")
        if method == "PATCH":                    # update a variable
            if var not in store:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            store[var] = payload["value"]
            self.env_writes.append(("PATCH", repo, env, var))
            return _Result(0, "")
        raise AssertionError(f"unexpected method {method} for {endpoint}")

    def _environment_get(self, endpoint):
        repo, env, var = self._split_env(endpoint)
        store = self.environments.get((repo, env))
        if store is None:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        if var is None:
            return _Result(0, json.dumps({"name": env}))
        if var not in store:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        return _Result(0, json.dumps({"name": var, "value": store[var]}))

    def other(self, cmd, kwargs):
        raise AssertionError(f"unexpected gh command {cmd}")

    def _get(self, endpoint, cmd):
        parts = endpoint.split("?")[0].split("/")
        repo = "/".join(parts[1:3])
        rest = parts[3:]
        if not rest:
            if repo not in self.private:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".private":
                return _Result(0, "true\n" if self.private[repo] else "false\n")
            return _Result(0, json.dumps({"private": self.private[repo]}))
        if rest[0] == "environments":
            return self._environment_get(endpoint)
        if rest[0] == "contents":
            path = "/".join(rest[1:])
            content = self.files.get((repo, path))
            if content is None:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            return _Result(0, json.dumps({
                "name": path.rsplit("/", 1)[-1], "sha": f"sha-{len(content)}",
                "content": base64.b64encode(content.encode()).decode(),
            }))
        if rest == ["actions", "variables"]:                  # list variables (capability probe)
            return _Result(0, json.dumps({"total_count": 0, "variables": []}))
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
