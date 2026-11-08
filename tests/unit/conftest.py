"""Unit-test fixtures shared by the `gapp ci` tests.

`gapp ci` talks to GitHub only through the `gh` CLI, so the tests swap
`subprocess.run` for an in-memory GitHub that answers the handful of
shapes the SDK uses: repo visibility and creation, the fleet repo's files,
its Actions variables and secrets, its Environments, the repo search by
topic, workflow dispatch and the runs it produces. A dispatched register
run executes the real runner code (`ci.runner_register`) against the same
in-memory GitHub, so what the fake writes is what the workflow writes.
Nothing touches the network.
"""

import base64
import json
import subprocess
import time

import pytest


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class FakeGitHub:
    """In-memory stand-in for GitHub behind the `gh` CLI."""

    def __init__(self):
        self.login = "me"
        self.private = {}       # "owner/repo" -> bool (a repo that exists)
        self.admin = {}         # "owner/repo" -> bool (gh login's admin permission; default True)
        self.topics = {}        # "owner/repo" -> set of topics
        self.files = {}         # ("owner/repo", path) -> content
        self.variables = {}     # ("owner/repo", NAME) -> value
        self.secrets = set()    # ("owner/repo", NAME)
        self.environments = {}  # ("owner/repo", env) -> {VAR: value}
        self.calls = []         # every command seen
        self.puts = []          # (repo, path, content) for every write
        self.deletes = []       # (repo, path)
        self.env_writes = []    # (method, repo, env, VAR or None)
        self.dispatched = []    # (repo, workflow file, {input: value})
        self.created = []       # repos created with `gh repo create`
        self.runs = {}          # run id -> {"repo", "workflow", "status", "conclusion", "log", "name", "created_at"}
        self._next_run = 100
        # Capabilities of the machine's GitHub access. A proxy may refuse
        # every API write and the Actions variables/secrets reads while
        # allowing contents reads and workflow dispatch; or refuse a repo
        # altogether (`unreadable`), or fail with any status (`fail_with`).
        self.api_writes = True
        self.actions_api = True
        self.unreadable = set()     # repos every read of which is refused with 403
        self.fail_with = {}         # endpoint substring -> stderr for GET reads
        # When set, a dispatched register run concludes with this instead
        # of running the real runner code: a callable taking the inputs and
        # returning (conclusion, log lines).
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
        if cmd[:3] == ["gh", "search", "repos"]:
            return self._search(cmd)
        if cmd[:3] == ["gh", "repo", "create"]:
            return self._repo_create(cmd)
        if cmd[:3] == ["gh", "repo", "edit"]:
            self.topics.setdefault(cmd[3], set()).add(cmd[cmd.index("--add-topic") + 1])
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
            if method == "DELETE" and "/contents/" in endpoint:
                return self._delete(endpoint, payload)
            assert method == "PUT", f"unexpected method {method} for {endpoint}"
            return self._put(endpoint, payload)
        endpoint = cmd[2]
        if endpoint == "user":
            return _Result(0, f"{self.login}\n")
        for needle, stderr in self.fail_with.items():
            if needle in endpoint:
                return _Result(1, "", stderr)
        if "/actions/variables" in endpoint or "/actions/secrets" in endpoint or "/variables" in endpoint:
            if not self.actions_api:
                return _Result(1, "", "gh: HTTP 403: request blocked: Actions variables and secrets are not readable from this session")
        return self._get(endpoint, cmd)

    def other(self, cmd, kwargs):
        raise AssertionError(f"unexpected gh command {cmd}")

    # -- repos --

    def _search(self, cmd):
        owner = cmd[cmd.index("--owner") + 1]
        topic = cmd[cmd.index("--topic") + 1]
        owner = self.login if owner == "@me" else owner
        found = sorted(r for r, topics in self.topics.items() if topic in topics and r.split("/")[0] == owner)
        return _Result(0, json.dumps([{"fullName": r} for r in found]))

    def _repo_create(self, cmd):
        repo = cmd[3]
        if repo in self.private:
            return _Result(1, "", "gh: HTTP 422: Repository creation failed: name already exists on this account")
        self.private[repo] = "--private" in cmd
        self.created.append(repo)
        return _Result(0, f"https://github.com/{repo}\n")

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
        if workflow == "gapp-register.yml":
            conclusion, log = self._register(repo, inputs)
            name = f"Register {inputs.get('name')}"
        else:
            conclusion, log = "success", f"deploy\tRun\t2026-01-01T00:00:00Z gapp-deploy: dispatched {inputs}"
            name = f"Deploy {inputs.get('deployment')} ({inputs.get('ref', 'main')})"
        self.runs[run_id] = {"repo": repo, "workflow": workflow, "status": "completed", "conclusion": conclusion,
                             "log": log, "name": name, "created_at": f"2026-01-01T00:00:{int(run_id) % 60:02d}Z"}
        return _Result(0, "")

    def _register(self, repo, inputs):
        if self.register_outcome is not None:
            conclusion, lines = self.register_outcome(inputs)
        else:
            from gapp.admin.sdk import ci as ci_mod
            lines = []
            env = {
                "FLEET_REPO": repo, "OPERATION": inputs.get("operation", "set"), "NAME": inputs.get("name", ""),
                "REPO": inputs.get("repo", ""), "PROJECT": inputs.get("project", ""),
                "CHANGES": inputs.get("changes", "{}"), "SOURCE": inputs.get("source", ""),
                "ALLOWED_SOURCES": self.variables.get((repo, "GAPP_ALLOWED_SOURCES"), ""),
                "APP_ID": self.variables.get((repo, "HOSTING_GITHUB_APP_ID"), ""),
                "APP_KEY_SET": "true" if (repo, "HOSTING_GITHUB_APP_PRIVATE_KEY") in self.secrets else "false",
            }
            # The runner's own token reads and writes its repo whatever this
            # machine's access is.
            saved = self.api_writes, self.actions_api, set(self.unreadable)
            self.api_writes, self.actions_api = True, True
            self.unreadable.discard(repo)
            try:
                rc = ci_mod.runner_register(env, out=lambda line: lines.append(line.split("gapp-register: ", 1)[-1]))
            finally:
                self.api_writes, self.actions_api, self.unreadable = saved[0], saved[1], saved[2]
            conclusion = "success" if rc == 0 else "failure"
        log = "\n".join(f"register\tWrite the deployment\t2026-01-01T00:00:00Z gapp-register: {line}" for line in lines)
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

    def _runs_list(self, repo, workflow, cmd):
        runs = [(i, r) for i, r in sorted(self.runs.items(), key=lambda kv: int(kv[0]), reverse=True)
                if r["repo"] == repo and r["workflow"] == workflow]
        if "--jq" in cmd:
            return _Result(0, f"{runs[0][0]}\n" if runs else "null\n")
        return _Result(0, json.dumps({"total_count": len(runs), "workflow_runs": [
            {"id": int(i), "name": r["name"], "display_title": r["name"], "status": r["status"],
             "conclusion": r["conclusion"], "created_at": r["created_at"],
             "html_url": f"https://github.com/{repo}/actions/runs/{i}"} for i, r in runs]}))

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
        return "/".join(parts[1:3]), parts[4], (parts[6] if len(parts) > 6 else None)

    def _environment_write(self, method, endpoint, payload):
        repo, env, var = self._split_env(endpoint)
        if method == "PUT":
            self.environments.setdefault((repo, env), {})
            self.env_writes.append(("PUT", repo, env, None))
            return _Result(0, json.dumps({"name": env}))
        if (repo, env) not in self.environments:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        store = self.environments[(repo, env)]
        if method == "POST":
            if payload["name"] in store:
                return _Result(1, "", "gh: Variable already exists (HTTP 409)")
            store[payload["name"]] = payload["value"]
            self.env_writes.append(("POST", repo, env, payload["name"]))
            return _Result(0, "")
        if method == "PATCH":
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
        if repo in self.unreadable:
            return _Result(1, "", "gh: HTTP 403: GitHub access to this repository is not enabled for this session")
        if repo not in self.private and not (rest[:1] == ["actions"] and rest[1:2] == ["runs"]):
            return _Result(1, "", "gh: Not Found (HTTP 404)")      # a repo GitHub does not show this login
        if not rest:
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".permissions.admin":
                return _Result(0, "true\n" if self.admin.get(repo, True) else "false\n")
            if repo not in self.private:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".private":
                return _Result(0, "true\n" if self.private[repo] else "false\n")
            return _Result(0, json.dumps({"private": self.private[repo], "permissions": {"admin": self.admin.get(repo, True)}}))
        if rest[0] == "environments":
            return self._environment_get(endpoint)
        if rest[0] == "contents":
            return self._contents(repo, "/".join(rest[1:]))
        if rest == ["actions", "variables"]:
            return _Result(0, json.dumps({"total_count": 0, "variables": []}))
        if rest[0] == "actions" and rest[1] in ("variables", "secrets") and len(rest) == 3:
            if rest[1] == "secrets":
                if (repo, rest[2]) in self.secrets:
                    return _Result(0, json.dumps({"name": rest[2]}))
            elif (repo, rest[2]) in self.variables:
                return _Result(0, json.dumps({"name": rest[2], "value": self.variables[(repo, rest[2])]}))
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        if rest[:2] == ["actions", "workflows"] and rest[3:] == ["runs"]:
            return self._runs_list(repo, rest[2], cmd)
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
        current = self.files.get((repo, path))
        if current is not None and payload.get("sha") != f"sha-{len(current)}":
            return _Result(1, "", "gh: HTTP 409: gapp/deployments.yaml does not match")
        content = base64.b64decode(payload["content"]).decode()
        self.files[(repo, path)] = content
        self.puts.append((repo, path, content))
        return _Result(0, "{}")

    def _delete(self, endpoint, payload):
        parts = endpoint.split("/")
        repo = "/".join(parts[1:3])
        path = "/".join(parts[4:])
        if (repo, path) not in self.files:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        del self.files[(repo, path)]
        self.deletes.append((repo, path))
        return _Result(0, "{}")

    # -- conveniences for tests --

    def install(self, repo, gapp_repo="example-org/gapp", gapp_ref="v4.0.0"):
        """Put gapp's three files in the fleet repo, as `ci init` would, and make the repo exist."""
        from gapp.admin.sdk import ci as ci_mod
        from gapp.admin.sdk import fleet
        self.private.setdefault(repo, True)
        self.topics.setdefault(repo, set()).add("gapp-ci")
        for path, content in ci_mod._operator_files(gapp_repo, gapp_ref).items():
            self.files[(repo, path)] = content
        self.files[(repo, fleet.DEPLOYMENTS_FILE)] = fleet.DEPLOYMENTS_FILE_INITIAL

    def deployments(self, repo):
        from gapp.admin.sdk import fleet
        return fleet.parse(self.files[(repo, fleet.DEPLOYMENTS_FILE)])

    def deploy(self, repo, name, deployed_repo, project, **keys):
        """Write a deployment entry directly, as the register run would."""
        from gapp.admin.sdk import fleet
        doc = self.deployments(repo)
        doc[name] = {"id": keys.pop("id", name), "repo": deployed_repo, "project": project, **keys}
        self.files[(repo, fleet.DEPLOYMENTS_FILE)] = fleet.dump(doc)


@pytest.fixture
def fake_gh(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(subprocess, "run", gh.run)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return gh


@pytest.fixture
def fleet_repo(monkeypatch, fake_gh, tmp_path):
    """A machine whose fleet repo is set to acme/fleet (gapp's files installed)
    and whose gapp config lives in a temp dir; gh is the only tool present."""
    import shutil
    from gapp.admin.sdk import config as config_mod
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    config_mod.set_fleet_repo("acme/fleet", "gapp ci init acme/fleet")
    fake_gh.install("acme/fleet")
    return "acme/fleet"
