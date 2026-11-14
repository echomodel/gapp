"""Unit-test fixtures shared by the `gapp ci` tests.

`gapp ci` talks to GitHub only through `gh api` REST calls, so the tests
swap `subprocess.run` for an in-memory GitHub that answers the handful of
REST shapes the SDK uses: repo visibility, default branch, topics and
creation, the fleet repo's files, its Actions variables and secrets, its
Environments, the repository search by topic, workflow dispatch, the runs
it produces and their job logs. Any other `gh` subcommand (they reach for
GraphQL, which gapp never depends on) and any GraphQL call fail the test.
The fleet file is read and written through `fleetrepo`, which the
`fleet_repo` fixture points at the same in-memory repo, so no git runs.
Nothing touches the network.
"""

import base64
import datetime
import json
import subprocess
import time

import pytest


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def job_log(lines, script=""):
    """A job log as GitHub's REST API returns it: each line an ISO timestamp,
    a space and the text; each step's `run:` script echoed first (coloured,
    under a ##[group] header) before what the step printed."""
    stamp = "2026-01-01T00:00:00.0000000Z"
    out = [f"\ufeff{stamp} Requested labels: ubuntu-latest", f"{stamp} ##[group]Run {script.splitlines()[0] if script else 'step'}"]
    out += [f"{stamp} \x1b[36;1m{s}\x1b[0m" for s in script.splitlines()]
    out += [f"{stamp} shell: /usr/bin/bash -e {{0}}", f"{stamp} ##[endgroup]"]
    out += [f"{stamp} {line}" for line in lines]
    return "\n".join(out)


class FakeGitHub:
    """In-memory stand-in for GitHub behind the `gh` CLI."""

    def __init__(self):
        self.login = "me"
        self.private = {}       # "owner/repo" -> bool (a repo that exists)
        self.admin = {}         # "owner/repo" -> bool (gh login's admin permission; default True)
        self.topics = {}        # "owner/repo" -> set of topics
        self.files = {}         # ("owner/repo", path) -> content (the default branch)
        self.ref_files = {}     # ("owner/repo", ref) -> {path: content}: a branch, tag or commit other than the default
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
        # What a dispatched deploy run reports: a callable taking the inputs
        # and returning (conclusion, log lines). The default reports a
        # deploy of the definition as the fleet file holds it.
        self.deploy_outcome = None
        # When set, every workflow dispatch fails with this stderr.
        self.dispatch_refusal = None
        self.default_branch = "main"
        # How many run listings a newly dispatched run stays invisible for
        # (GitHub lists a run some seconds after the dispatch), and whether
        # a dispatch creates a run at all.
        self.run_appears_after = 0
        self.dispatch_creates_run = True
        self.request_ids = []   # the request id each dispatch carried
        # What a dispatched status run reports: a callable taking the inputs
        # and returning (conclusion, log lines); the default reports a
        # healthy service for the deployment.
        self.status_outcome = None

    # -- entry point --

    def run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        assert cmd[0] == "gh", f"only gh may be invoked, got {cmd}"
        assert cmd[:2] == ["gh", "api"], f"only REST through `gh api` (other gh subcommands use GraphQL): {cmd}"
        assert "graphql" not in cmd, f"gapp uses REST only: {cmd}"
        if "-X" in cmd and cmd[cmd.index("-X") + 1] == "GET":
            endpoint = cmd[cmd.index("-X") + 2]
            if endpoint == "search/repositories":
                return self._search(cmd)
            return self._get(endpoint, cmd)
        if "-X" in cmd:
            method = cmd[cmd.index("-X") + 1]
            endpoint = cmd[cmd.index("-X") + 2]
            payload = json.loads(kwargs["input"]) if kwargs.get("input") else None
            # GitHub access may refuse API writes but allow workflow dispatch.
            if endpoint.endswith("/dispatches"):
                return self._dispatch(endpoint, payload)
            if not self.api_writes:
                return _Result(1, "", "gh: HTTP 403: request blocked: GitHub API writes are not allowed from this session")
            if endpoint == "user/repos" or (endpoint.startswith("orgs/") and endpoint.endswith("/repos")):
                return self._repo_create(endpoint, payload)
            if endpoint.endswith("/topics"):
                repo = "/".join(endpoint.split("/")[1:3])
                self.topics[repo] = set(payload["names"])
                return _Result(0, json.dumps({"names": payload["names"]}))
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
        fields = dict(cmd[i + 1].partition("=")[::2] for i, a in enumerate(cmd) if a == "-f")
        terms = dict(term.partition(":")[::2] for term in fields["q"].split())
        topic, owner = terms["topic"], terms["user"]
        found = sorted(r for r, topics in self.topics.items() if topic in topics and r.split("/")[0] == owner)
        return _Result(0, json.dumps({"total_count": len(found), "items": [{"full_name": r} for r in found]}))

    def _repo_create(self, endpoint, payload):
        owner = self.login if endpoint == "user/repos" else endpoint.split("/")[1]
        repo = f"{owner}/{payload['name']}"
        if repo in self.private:
            return _Result(1, "", "gh: HTTP 422: Repository creation failed: name already exists on this account")
        self.private[repo] = bool(payload.get("private"))
        self.created.append(repo)
        return _Result(0, json.dumps({"full_name": repo}))

    # -- workflow dispatch and runs --

    def _dispatch(self, endpoint, payload):
        parts = endpoint.split("/")
        repo, workflow = "/".join(parts[1:3]), parts[5]
        inputs = dict(payload.get("inputs") or {})
        request_id = inputs.pop("request_id", "")
        assert payload.get("ref") == self.default_branch, f"dispatch must name the default branch, got {payload}"
        if self.dispatch_refusal:
            return _Result(1, "", self.dispatch_refusal)
        if (repo, f".github/workflows/{workflow}") not in self.files:
            return _Result(1, "", "gh: Not Found (HTTP 404)")
        self.dispatched.append((repo, workflow, inputs))
        self.request_ids.append(request_id)
        if not self.dispatch_creates_run:
            return _Result(0, "")
        run_id = str(self._next_run)
        self._next_run += 1
        if workflow == "gapp-status.yml":
            conclusion, lines = self._status(repo, inputs)
            log = job_log([f"gapp-status: {line}" for line in lines], script='gapp ci runner status "$DEPLOYMENT"')
            name = f"Status {inputs.get('deployment')} {request_id}"
        else:
            conclusion, lines = self._deploy(repo, inputs)
            log = job_log([f"gapp-deploy: {line}" for line in lines],
                          script='gapp ci runner deploy "$DEPLOYMENT" $UPGRADE_FLAG')
            name = f"Deploy {inputs.get('deployment')} {request_id}"
        self.add_run(run_id, repo, workflow, name, conclusion, log, hidden_polls=self.run_appears_after)
        return _Result(0, "")

    def _deploy(self, repo, inputs):
        if self.deploy_outcome is not None:
            return self.deploy_outcome(inputs)
        name = inputs.get("deployment")
        definition = self.definitions(repo)[name]
        done = {"deployment": name, "project": definition["project"], "ref": definition.get("ref"),
                "commit": "abc1234", "gapp": definition["gapp"], "service_url": f"https://{name}-abc-uc.a.run.app"}
        return "success", [f"result={json.dumps(done)}"]

    def _status(self, repo, inputs):
        if self.status_outcome is not None:
            return self.status_outcome(inputs)
        name = inputs.get("deployment")
        definition = self.definitions(repo)[name]
        live = {"deployment": name, "project": definition["project"], "deployed": True,
                "url": f"https://{name}-abc-uc.a.run.app", "healthy": True,
                "last_deployed_by": definition["gapp"], "domain": None, "next_step": None}
        return "success", [f"result={json.dumps(live)}"]

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

    def add_run(self, run_id, repo, workflow, name, conclusion, log="", hidden_polls=0, created_at=None):
        """A run GitHub knows of; `created_at` defaults to now, `hidden_polls` delays its listing."""
        created = created_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.runs[str(run_id)] = {"repo": repo, "workflow": workflow, "status": "completed", "conclusion": conclusion,
                                  "log": log, "name": name, "created_at": created, "hidden_polls": hidden_polls}
        self._next_run = max(self._next_run, int(run_id) + 1)

    def _runs_list(self, repo, workflow, cmd):
        runs = []
        for i, r in sorted(self.runs.items(), key=lambda kv: int(kv[0]), reverse=True):
            if r["repo"] != repo or r["workflow"] != workflow:
                continue
            if r.get("hidden_polls", 0) > 0:
                r["hidden_polls"] -= 1
                continue
            runs.append((i, r))
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
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".default_branch":
                return _Result(0, f"{self.default_branch}\n")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".permissions.admin":
                return _Result(0, "true\n" if self.admin.get(repo, True) else "false\n")
            if repo not in self.private:
                return _Result(1, "", "gh: Not Found (HTTP 404)")
            if "--jq" in cmd and cmd[cmd.index("--jq") + 1] == ".private":
                return _Result(0, "true\n" if self.private[repo] else "false\n")
            return _Result(0, json.dumps({"private": self.private[repo], "permissions": {"admin": self.admin.get(repo, True)}}))
        if rest == ["environments"]:
            names = sorted(env for (r, env) in self.environments if r == repo)
            return _Result(0, json.dumps({"total_count": len(names), "environments": [{"name": n} for n in names]}))
        if rest[0] == "environments":
            return self._environment_get(path_part)
        if rest[0] == "contents":
            ref = dict(kv.partition("=")[::2] for kv in query.split("&") if kv).get("ref")
            return self._contents(repo, "/".join(rest[1:]), ref)
        if rest[0] == "commits" and len(rest) == 2:
            ref = rest[1]
            if ref == self.default_branch or (repo, ref) in self.ref_files:
                return _Result(0, f"sha-{ref}\n")
            return _Result(1, "", f"gh: No commit found for SHA: {ref} (HTTP 422)")
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
        if rest[:2] == ["actions", "runs"] and rest[3:] == ["jobs"]:
            run = self.runs.get(rest[2])
            return _Result(0, f"{int(rest[2]) * 10}\n") if run else _Result(1, "", "gh: Not Found (HTTP 404)")
        if rest[:2] == ["actions", "jobs"] and rest[3:] == ["logs"]:
            run = self.runs.get(str(int(rest[2]) // 10))
            return _Result(0, run["log"]) if run else _Result(1, "", "gh: Not Found (HTTP 404)")
        if rest == ["topics"]:
            return _Result(0, json.dumps({"names": sorted(self.topics.get(repo, set()))}))
        return self.other(cmd, {})

    def _contents(self, repo, path, ref=None):
        ref = ref[4:] if ref and ref.startswith("sha-") else ref
        if ref and ref != self.default_branch:
            files = {(repo, p): c for p, c in self.ref_files.get((repo, ref), {}).items()}
        else:
            files = self.files
        content = files.get((repo, path))
        if content is not None:
            return _Result(0, json.dumps({
                "type": "file", "name": path.rsplit("/", 1)[-1], "sha": f"sha-{len(content)}",
                "content": base64.b64encode(content.encode()).decode(),
            }))
        prefix = path.rstrip("/") + "/" if path else ""
        listing = [{"type": "file", "name": p[len(prefix):], "path": p}
                   for (r, p) in files if r == repo and p.startswith(prefix) and "/" not in p[len(prefix):]]
        if listing or (not path and repo in self.private):
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
        """Make the fleet repo exist, holding an empty fleet and the workflows `gapp ci init` installs."""
        from gapp.admin.sdk import ci as ci_mod
        from gapp.admin.sdk import entryfile
        self.private.setdefault(repo, True)
        for path, content in ci_mod._workflow_files(gapp_repo, gapp_ref).items():
            self.files[(repo, path)] = content
        self.files[(repo, entryfile.FILE_NAME)] = entryfile.dump(entryfile.empty())

    def definitions(self, repo):
        from gapp.admin.sdk import entryfile
        return entryfile.parse(self.files[(repo, entryfile.FILE_NAME)])["deployments"]

    def define(self, repo, name, app_repo, project, **keys):
        """Add a deployment's definition to the fleet file."""
        from gapp import __version__
        from gapp.admin.sdk import entryfile
        whole = entryfile.parse(self.files[(repo, entryfile.FILE_NAME)])
        definition = entryfile.new_definition(id=keys.pop("id", name), project=project, repo=app_repo, ref=keys.pop("ref", None))
        definition.setdefault("gapp", __version__)
        definition.update(keys)
        whole["deployments"][name] = definition
        self.files[(repo, entryfile.FILE_NAME)] = entryfile.dump(whole)

    def connect(self, repo, project):
        """Record a project's deploy identity in the fleet repo, as `gapp ci projects connect` does."""
        self.environments[(repo, project)] = {
            "GAPP_WIF_PROVIDER": f"projects/1/locations/global/workloadIdentityPools/github/providers/github",
            "GAPP_DEPLOY_SERVICE_ACCOUNT": "gapp-deploy@" + f"{project}.iam.gserviceaccount.com",
        }


@pytest.fixture
def fake_gh(monkeypatch):
    gh = FakeGitHub()
    monkeypatch.setattr(subprocess, "run", gh.run)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    return gh


@pytest.fixture
def fleet_repo(monkeypatch, fake_gh, tmp_path):
    """A machine whose fleet lives in acme/fleet (an empty fleet, gapp's
    workflows installed) and whose gapp config lives in a temp dir; gh is
    the only tool present."""
    import shutil
    from gapp.admin.sdk import config as config_mod
    from gapp.admin.sdk import entryfile, fleetrepo
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("GAPP_FLEET_FILE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    monkeypatch.setattr(fleetrepo, "read_text", lambda repo: fake_gh.files.get((repo, entryfile.FILE_NAME)))

    def write_text(repo, text, message):
        fake_gh.files[(repo, entryfile.FILE_NAME)] = text
        return True
    monkeypatch.setattr(fleetrepo, "write_text", write_text)
    config_mod.set_fleet_repo("acme/fleet")
    fake_gh.install("acme/fleet")
    return "acme/fleet"


@pytest.fixture
def ci(monkeypatch, fleet_repo, fake_gh):
    """The fleet repo with one deployment, my-app (the public repo
    me/my-app, on my-proj), runners connected to my-proj, and `me` an
    allowed owner. gcloud must never be called."""
    from gapp.admin.sdk import ci as ci_mod

    def no_gcloud(*args, **kwargs):
        raise AssertionError("this command must never call gcloud")
    monkeypatch.setattr(ci_mod, "run_gcloud", no_gcloud)
    monkeypatch.setattr(ci_mod, "_get_gapp_repo", lambda: "example-org/gapp")
    monkeypatch.setattr(ci_mod, "_resolve_gapp_ref", lambda gapp_repo: "v4.0.0")
    fake_gh.define(fleet_repo, "my-app", "me/my-app", "my-proj")
    fake_gh.connect(fleet_repo, "my-proj")
    fake_gh.private["me/my-app"] = False
    fake_gh.variables[(fleet_repo, "GAPP_ALLOWED_SOURCES")] = "me"
    return fake_gh
