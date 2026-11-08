"""Unit-test fixtures shared by the `gapp ci` tests.

`gapp ci` talks to GitHub only through the `gh` CLI, so the tests swap
`subprocess.run` for an in-memory GitHub that answers the handful of
shapes the SDK uses: repo visibility, the CI repo's files, its Actions
variables and secrets, its Environments, workflow dispatch and the runs
it produces. Nothing touches the network.
"""

import base64
import json
import subprocess
import time

import pytest


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _registration(inputs, content):
    """What the register workflow does to gapp/solutions.yaml, in Python."""
    import yaml

    doc = yaml.safe_load(content) or {}
    entries = [e for e in (doc.get("solutions") or []) if e.get("name") != inputs["solution"]]
    entry = {"name": inputs["solution"], "repo": inputs["solution-repo"], "project": inputs["project"]}
    if inputs.get("domain"):
        entry["domain"] = inputs["domain"]
    if inputs.get("gapp-ref"):
        entry["gapp_ref"] = inputs["gapp-ref"]
    entries.append(entry)
    doc["solutions"] = sorted(entries, key=lambda e: e["name"])
    return yaml.dump(doc, sort_keys=False)


class FakeGitHub:
    """In-memory stand-in for GitHub behind the `gh` CLI."""

    def __init__(self):
        self.private = {}       # "owner/repo" -> bool
        self.admin = {}         # "owner/repo" -> bool (gh login's admin permission; default True)
        self.files = {}         # ("owner/repo", path) -> content
        self.variables = {}     # ("owner/repo", NAME) -> value
        self.secrets = set()    # ("owner/repo", NAME)
        self.environments = {}  # ("owner/repo", env) -> {VAR: value}
        self.calls = []         # every command seen
        self.puts = []          # (repo, path, content) for every write
        self.env_writes = []    # (method, repo, env, VAR or None)
        self.dispatched = []    # (repo, workflow file, {input: value})
        self.runs = {}          # run id -> {"repo", "workflow", "status", "conclusion", "log"}
        self._next_run = 100
        # Capabilities of the machine's GitHub access. A proxy may refuse
        # every API write and the Actions variables/secrets reads while
        # allowing contents reads and workflow dispatch.
        self.api_writes = True
        self.actions_api = True
        # What a dispatched register run concludes: a callable taking the
        # inputs and returning (conclusion, log lines); the default behaves
        # like the real workflow on a repo whose checks all pass.
        self.register_outcome = None
        # When set, every `gh workflow run` fails with this stderr.
        self.dispatch_refusal = None

    # -- entry point --

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        assert cmd[0] == "gh", f"only gh may be invoked, got {cmd}"
        assert "clone" not in cmd
        if cmd[:3] == ["gh", "workflow", "run"]:
            return self._dispatch(cmd)
        if cmd[:3] == ["gh", "run", "view"]:
            run = self.runs.get(cmd[3])
            return _Result(0, run["log"]) if run else _Result(1, "", "run not found")
        if cmd[:3] == ["gh", "run", "watch"]:
            return _Result(0, "")
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
            if "/actions/variables" in endpoint:
                return self._variable_write(method, endpoint, payload)
            assert method == "PUT", f"unexpected method {method} for {endpoint}"
            return self._put(endpoint, payload)
        endpoint = cmd[2]
        if "/actions/variables" in endpoint or "/actions/secrets" in endpoint or "/variables" in endpoint:
            if not self.actions_api:
                return _Result(1, "", "gh: HTTP 403: request blocked: Actions variables and secrets are not readable from this session")
        return self._get(endpoint, cmd)

    def other(self, cmd, kwargs):
        raise AssertionError(f"unexpected gh command {cmd}")

    # -- workflow dispatch and runs --

    def _dispatch(self, cmd):
        workflow = cmd[3]
        repo = cmd[cmd.index("--repo") + 1]
        inputs = {}
        for i, arg in enumerate(cmd):
            if arg == "-f":
                key, _, value = cmd[i + 1].partition("=")
                inputs[key] = value
        if self.dispatch_refusal:
            return _Result(1, "", self.dispatch_refusal)
        if (repo, f".github/workflows/{workflow}") not in self.files:
            return _Result(1, "", f"could not find any workflows named {workflow}")
        self.dispatched.append((repo, workflow, inputs))
        run_id = str(self._next_run)
        self._next_run += 1
        conclusion, log = "success", f"deploy\tRun\t2026-01-01T00:00:00Z gapp-deploy: dispatched {inputs}"
        if workflow == "gapp-register.yml":
            conclusion, log = self._register(repo, inputs)
        self.runs[run_id] = {"repo": repo, "workflow": workflow, "status": "completed",
                             "conclusion": conclusion, "log": log}
        return _Result(0, "")

    def _register(self, repo, inputs):
        if self.register_outcome is not None:
            conclusion, lines = self.register_outcome(inputs)
        else:
            conclusion, lines = "success", ["result=added", f"added '{inputs['solution']}' -> {inputs['solution-repo']} on project '{inputs['project']}'."]
            key = (repo, "gapp/solutions.yaml")
            self.files[key] = _registration(inputs, self.files.get(key, "solutions: []\n"))
        log = "\n".join(f"register\tWrite the entry\t2026-01-01T00:00:00Z gapp-register: {line}" for line in lines)
        return conclusion, log

    def _run_get(self, run_id, cmd):
        run = self.runs.get(run_id)
        if not run:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        jq = cmd[cmd.index("--jq") + 1] if "--jq" in cmd else ""
        if ".status" in jq:
            return _Result(0, f"{run['status']} {run['conclusion'] or ''}\n")
        if ".conclusion" in jq:
            return _Result(0, f"{run['conclusion']}\n")
        return _Result(0, json.dumps(run))

    # -- repository variables --

    def _variable_write(self, method, endpoint, payload):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        name = parts[5] if len(parts) > 5 else (payload or {}).get("name")
        if method == "POST":
            if (repo, name) in self.variables:
                return _Result(1, "", "gh: Variable already exists (HTTP 409)")
            self.variables[(repo, name)] = payload["value"]
            return _Result(0, "")
        if (repo, name) not in self.variables:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        if method == "PATCH":
            self.variables[(repo, name)] = payload["value"]
            return _Result(0, "")
        if method == "DELETE":
            del self.variables[(repo, name)]
            return _Result(0, "")
        raise AssertionError(f"unexpected method {method} for {endpoint}")

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

    # -- reads --

    def _get(self, endpoint, cmd):
        path_part, _, query = endpoint.partition("?")
        parts = path_part.split("/")
        repo = "/".join(parts[1:3])
        rest = parts[3:]
        if not rest:
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".permissions.admin":
                return _Result(0, "true\n" if self.admin.get(repo, True) else "false\n")
            if repo not in self.private:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".private":
                return _Result(0, "true\n" if self.private[repo] else "false\n")
            return _Result(0, json.dumps({"private": self.private[repo]}))
        if rest[0] == "environments":
            return self._environment_get(endpoint)
        if rest[0] == "contents":
            return self._contents(repo, "/".join(rest[1:]))
        if rest == ["actions", "variables"]:                  # list variables (capability probe)
            return _Result(0, json.dumps({"total_count": 0, "variables": []}))
        if rest[0] == "actions" and rest[1] in ("variables", "secrets") and len(rest) == 3:
            if rest[1] == "secrets":
                if (repo, rest[2]) in self.secrets:
                    return _Result(0, json.dumps({"name": rest[2]}))
            elif (repo, rest[2]) in self.variables:
                return _Result(0, json.dumps({"name": rest[2], "value": self.variables[(repo, rest[2])]}))
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        if rest[:2] == ["actions", "workflows"] and rest[3:] == ["runs"]:
            ids = [i for i, r in sorted(self.runs.items(), key=lambda kv: int(kv[0]), reverse=True)
                   if r["repo"] == repo and r["workflow"] == rest[2]]
            return _Result(0, f"{ids[0]}\n" if ids else "null\n")
        if rest[:2] == ["actions", "runs"] and len(rest) == 3:
            return self._run_get(rest[2], cmd)
        return self.other(cmd, {})

    def _contents(self, repo, path):
        content = self.files.get((repo, path))
        if content is not None:
            return _Result(0, json.dumps({
                "type": "file", "name": path.rsplit("/", 1)[-1], "sha": f"sha-{len(content)}",
                "content": base64.b64encode(content.encode()).decode(),
            }))
        prefix = path.rstrip("/") + "/"
        listing = [{"type": "file", "name": p[len(prefix):], "path": p}
                   for (r, p) in self.files if r == repo and p.startswith(prefix) and "/" not in p[len(prefix):]]
        if listing:
            return _Result(0, json.dumps(listing))
        return _Result(1, "", "gh: Not Found (HTTP 404)")

    def _put(self, endpoint, payload):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        path = "/".join(parts[4:])
        content = base64.b64decode(payload["content"]).decode()
        self.files[(repo, path)] = content
        self.puts.append((repo, path, content))
        return _Result(0, "{}")

    # -- conveniences for tests --

    def install(self, repo, gapp_repo="example-org/gapp", gapp_ref="v3.9.0"):
        """Put gapp's three files in the CI repo, as `ci init` would."""
        from gapp.admin.sdk import ci as ci_mod
        for path, content in ci_mod._operator_files(gapp_repo, gapp_ref).items():
            self.files[(repo, path)] = content
        self.files[(repo, ci_mod.SOLUTIONS_FILE)] = ci_mod.SOLUTIONS_FILE_INITIAL

    def solutions(self, repo):
        import yaml
        return (yaml.safe_load(self.files[(repo, "gapp/solutions.yaml")]) or {}).get("solutions") or []


@pytest.fixture
def fake_gh(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(subprocess, "run", gh.run)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return gh
