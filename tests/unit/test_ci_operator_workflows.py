"""The two workflows `gapp ci init` installs in the CI repo.

Their shape is checked from the rendered YAML. Their shell steps are run
for real, with the Go yq pre-installed on GitHub's Ubuntu runners and a stub
`gh` that plays the GitHub API from a JSON state file, so the register
workflow's refusals and its SHA-checked write, and the deploy workflow's
lookup, are exercised rather than read. Those tests skip where that yq is
not installed.
"""

import base64
import json
import os
import pathlib
import shutil
import stat
import subprocess
import textwrap

import pytest
import yaml

from gapp.admin.sdk import ci as ci_mod

GAPP_REPO = "example-org/gapp"
GAPP_REF = "v3.9.0"
CI_REPO = "acme/ci"


def _rendered(name):
    return ci_mod._render_template(name, GAPP_REPO, GAPP_REF)


# -- shape --


def test_deploy_workflow_resolves_the_entry_then_calls_the_reusable_workflow_in_the_project_environment():
    doc = yaml.safe_load(_rendered(ci_mod.DEPLOY_WORKFLOW))
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"solution", "ref"}
    resolve, deploy = doc["jobs"]["resolve"], doc["jobs"]["deploy"]
    assert set(resolve["outputs"]) == {"repo", "project", "domain", "gapp_ref", "app_id"}
    assert deploy["needs"] == "resolve"
    assert deploy["uses"] == f"{GAPP_REPO}/.github/workflows/deploy.yml@{GAPP_REF}"
    assert deploy["with"]["environment"] == "${{ needs.resolve.outputs.project }}"
    assert deploy["with"]["solution-repo"] == "${{ needs.resolve.outputs.repo }}"
    assert deploy["with"]["gapp-repo"] == GAPP_REPO
    assert deploy["with"]["gapp-ref"] == f"${{{{ needs.resolve.outputs.gapp_ref || '{GAPP_REF}' }}}}"
    assert deploy["with"]["solution-repo-app-id"] == "${{ needs.resolve.outputs.app_id }}"
    assert deploy["secrets"] == {"solution-repo-app-private-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}
    assert deploy["permissions"] == {"id-token": "write", "contents": "read"}
    # Inputs reach the shell through env, never interpolated into run.
    step = resolve["steps"][1]
    assert step["env"]["SOLUTION"] == "${{ inputs.solution }}"
    assert "${{" not in step["run"]


def test_register_workflow_takes_its_inputs_through_env_and_writes_with_contents_write():
    doc = yaml.safe_load(_rendered(ci_mod.REGISTER_WORKFLOW))
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"solution", "solution-repo", "project", "domain", "gapp-ref"}
    job = doc["jobs"]["register"]
    assert job["permissions"] == {"contents": "write", "actions": "read"}
    assert job["env"]["SOLUTION"] == "${{ inputs.solution }}"
    assert job["env"]["ALLOWED_SOURCES"] == "${{ vars.GAPP_ALLOWED_SOURCES }}"
    assert job["env"]["APP_KEY_SET"] == "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY != '' }}"
    for step in job["steps"]:
        assert "${{" not in step["run"]
    assert "__GAPP_" not in _rendered(ci_mod.REGISTER_WORKFLOW)


def test_templates_ship_with_the_package():
    assert "__GAPP_REPO__" not in _rendered(ci_mod.DEPLOY_WORKFLOW)
    files = ci_mod._operator_files(GAPP_REPO, GAPP_REF)
    assert set(files) == {ci_mod.DEPLOY_WORKFLOW_PATH, ci_mod.REGISTER_WORKFLOW_PATH}


# -- the shell steps, run for real --


def _go_yq():
    """The Go yq (v4) prints "version v4.x"; other tools named yq print something else."""
    path = shutil.which("yq")
    if not path:
        return False
    out = subprocess.run([path, "--version"], capture_output=True, text=True).stdout
    return "version v4" in out


needs_yq = pytest.mark.skipif(not _go_yq(), reason="the Go yq pre-installed on GitHub's Ubuntu runners is not installed")

# A `gh` that answers from a JSON state file: environments, repo visibility,
# the solutions file (content + sha) and the SHA-checked PUT.
STUB_GH = textwrap.dedent('''
    #!/usr/bin/env python3
    import base64, json, os, sys
    state_path = os.environ["FAKE_GH_STATE"]
    state = json.load(open(state_path))
    args = sys.argv[1:]
    def save():
        json.dump(state, open(state_path, "w"))
    def fail(msg, code=1):
        sys.stderr.write(msg + "\\n"); sys.exit(code)
    if args[0] != "api":
        fail("unexpected gh command: " + " ".join(args))
    fields = {}
    rest = []
    i = 1
    method = "GET"
    while i < len(args):
        a = args[i]
        if a == "-X":
            method = args[i + 1]; i += 2; continue
        if a == "-f":
            k, _, v = args[i + 1].partition("="); fields[k] = v; i += 2; continue
        if a == "--jq":
            fields["--jq"] = args[i + 1]; i += 2; continue
        if a == "--silent":
            i += 1; continue
        rest.append(a); i += 1
    endpoint = rest[0]
    state.setdefault("calls", []).append({"method": method, "endpoint": endpoint, "fields": {k: v for k, v in fields.items() if k != "--jq"}})
    save()
    parts = endpoint.split("/")
    repo = "/".join(parts[1:3])
    tail = parts[3:]
    if method == "PUT" and tail[:1] == ["contents"]:
        path = "/".join(tail[1:])
        f = state["files"].get(path)
        if f is None or f["sha"] != fields.get("sha"):
            fail("gh: " + path + " does not match (HTTP 409)")
        f["content"] = base64.b64decode(fields["content"]).decode()
        f["sha"] = f["sha"] + "+1"
        state.setdefault("puts", []).append({"path": path, "message": fields.get("message")})
        save(); sys.exit(0)
    if tail[:1] == ["environments"]:
        sys.exit(0 if tail[1] in state["environments"] else 1)
    if not tail:
        if repo not in state["repos"]:
            fail("gh: Not Found (HTTP 404)")
        print("true" if state["repos"][repo] else "false"); sys.exit(0)
    if tail[:1] == ["contents"]:
        f = state["files"].get("/".join(tail[1:]))
        if f is None:
            fail("gh: Not Found (HTTP 404)")
        print(json.dumps({"sha": f["sha"], "content": base64.b64encode(f["content"].encode()).decode()})); sys.exit(0)
    fail("unexpected endpoint " + endpoint)
''').lstrip()


class _Runner:
    """Runs a rendered workflow's shell steps against the stub gh."""

    def __init__(self, tmp_path, state):
        self.dir = tmp_path
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(STUB_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.state_path = tmp_path / "state.json"
        self.state_path.write_text(json.dumps(state))
        self.output = tmp_path / "output.txt"
        self.summary = tmp_path / "summary.txt"
        self.output.touch()
        self.summary.touch()
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_GH_STATE": str(self.state_path),
            "GITHUB_REPOSITORY": CI_REPO,
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
        }

    @property
    def state(self):
        return json.loads(self.state_path.read_text())

    def run_job(self, workflow, job, env):
        """Run the job's steps in order, as the runner would; returns (ok, log)."""
        doc = yaml.safe_load(_rendered(workflow))
        job_doc = doc["jobs"][job]
        job_env = {k: ("" if v is None else str(v)) for k, v in (job_doc.get("env") or {}).items()}
        job_env.update(env)
        full_env = {**self.env, **{k: v for k, v in job_env.items() if not v.startswith("${{")}}
        log = []
        for step in job_doc["steps"]:
            if "run" not in step:
                continue
            res = subprocess.run(["bash", "-c", step["run"]], cwd=self.dir, env=full_env,
                                 capture_output=True, text=True)
            log.append(res.stdout + res.stderr)
            if res.returncode != 0:
                return False, "\n".join(log)
        return True, "\n".join(log)

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)


def _state(solutions="solutions: []\n", environments=("my-proj",), repos=None):
    return {
        "files": {"gapp/solutions.yaml": {"sha": "sha0", "content": solutions}},
        "environments": list(environments),
        "repos": repos if repos is not None else {"me/my-app": False},
    }


REGISTER_ENV = {
    "SOLUTION": "my-app", "SOLUTION_REPO": "me/my-app", "PROJECT": "my-proj", "DOMAIN": "", "GAPP_REF": "",
    "ALLOWED_SOURCES": "me,other-org", "APP_ID": "", "APP_KEY_SET": "false", "GH_TOKEN": "x",
}


@needs_yq
def test_register_adds_an_entry_with_a_sha_checked_write(tmp_path):
    runner = _Runner(tmp_path, _state())
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", {**REGISTER_ENV, "DOMAIN": "mcp.example.com"})
    assert ok, log
    assert "gapp-register: result=added" in log
    assert "gapp-register: added 'my-app' -> me/my-app on project 'my-proj', domain mcp.example.com." in log
    state = runner.state
    assert yaml.safe_load(state["files"]["gapp/solutions.yaml"]["content"]) == {
        "solutions": [{"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com"}],
    }
    put = [c for c in state["calls"] if c["method"] == "PUT"]
    assert len(put) == 1 and put[0]["fields"]["sha"] == "sha0"
    assert "added: my-app" in runner.summary.read_text()


@needs_yq
def test_register_updates_an_entry_and_keeps_the_file_sorted(tmp_path):
    existing = yaml.dump({"solutions": [
        {"name": "zeta", "repo": "me/zeta", "project": "my-proj"},
        {"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "old.example.com"},
    ]})
    runner = _Runner(tmp_path, _state(solutions=existing))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert ok, log
    assert "gapp-register: result=updated" in log and "gapp-register: updated 'my-app'" in log
    entries = yaml.safe_load(runner.state["files"]["gapp/solutions.yaml"]["content"])["solutions"]
    assert [e["name"] for e in entries] == ["my-app", "zeta"]
    assert "domain" not in entries[0]                      # an empty domain input removes it


@needs_yq
def test_register_writes_nothing_when_the_entry_is_unchanged(tmp_path):
    existing = "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: my-proj\n"
    runner = _Runner(tmp_path, _state(solutions=existing))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert ok, log
    assert "gapp-register: result=unchanged" in log
    assert not [c for c in runner.state["calls"] if c["method"] == "PUT"]


@needs_yq
def test_register_refuses_an_unknown_owner(tmp_path):
    runner = _Runner(tmp_path, _state())
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", {**REGISTER_ENV, "ALLOWED_SOURCES": "other-org"})
    assert not ok
    assert "gapp-register: result=source-owner-not-allowed" in log
    assert "gapp-register: Source repos owned by me are not allowed to deploy through this CI repo; allow the owner with: gapp ci sources add me" in log
    assert runner.state["files"]["gapp/solutions.yaml"]["content"] == "solutions: []\n"


@needs_yq
def test_register_refuses_without_an_allowlist(tmp_path):
    runner = _Runner(tmp_path, _state())
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", {**REGISTER_ENV, "ALLOWED_SOURCES": ""})
    assert not ok and "gapp-register: result=source-owner-not-allowed" in log


@needs_yq
def test_register_refuses_a_project_without_environment(tmp_path):
    runner = _Runner(tmp_path, _state(environments=()))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert not ok
    assert "gapp-register: result=project-not-trusted" in log
    assert "not yet trusted to deploy to GCP project my-proj" in log and "gapp ci project setup my-proj" in log


@needs_yq
def test_register_refuses_a_conflicting_entry(tmp_path):
    existing = "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: other-proj\n"
    runner = _Runner(tmp_path, _state(solutions=existing))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert not ok
    assert "gapp-register: result=conflict" in log
    assert "'my-app' is already registered on project 'other-proj'" in log
    assert not [c for c in runner.state["calls"] if c["method"] == "PUT"]


@needs_yq
def test_register_refuses_an_unreadable_repo_without_the_hosting_app(tmp_path):
    runner = _Runner(tmp_path, _state(repos={}))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert not ok
    assert "gapp-register: result=repo-unreachable" in log
    assert "not readable with this run's token" in log and "HOSTING_GITHUB_APP_PRIVATE_KEY" in log


@needs_yq
def test_register_accepts_a_private_repo_when_the_hosting_app_is_present(tmp_path):
    runner = _Runner(tmp_path, _state(repos={}))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", {**REGISTER_ENV, "APP_ID": "1", "APP_KEY_SET": "true"})
    assert ok, log
    assert "me/my-app is private; the hosting app's credential is present" in log


@needs_yq
def test_register_refuses_malformed_inputs_before_touching_anything(tmp_path):
    runner = _Runner(tmp_path, _state())
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", {**REGISTER_ENV, "SOLUTION": "my app; rm -rf"})
    assert not ok and "gapp-register: result=invalid-input" in log and "is not a valid solution name" in log
    assert runner.state.get("calls", []) == []


@needs_yq
def test_register_fails_cleanly_when_the_file_changed_during_the_run(tmp_path):
    """The write carries the SHA read at the start; a stale SHA is rejected."""
    state = _state()
    state["files"]["gapp/solutions.yaml"]["sha"] = "sha-stale"
    runner = _Runner(tmp_path, state)
    # Make the stub reject by changing the stored sha after the read: simplest
    # is a stub state where the GET returns one sha and the PUT expects another.
    gh = tmp_path / "bin" / "gh"
    gh.write_text(STUB_GH.replace('f["sha"] != fields.get("sha")', 'True'))
    ok, log = runner.run_job(ci_mod.REGISTER_WORKFLOW, "register", REGISTER_ENV)
    assert not ok
    assert "gapp-register: result=write-rejected" in log
    assert "the write to gapp/solutions.yaml was rejected by GitHub" in log and "HTTP 409" not in log


DEPLOY_ENV = {"SOLUTION": "my-app", "APP_ID": "", "GH_TOKEN": "x", "DATA_FILE": "gapp/solutions.yaml"}


def _with_data_file(tmp_path, content):
    """The checkout the resolve job sees: gapp/solutions.yaml under the working directory."""
    (tmp_path / "gapp").mkdir(exist_ok=True)
    (tmp_path / "gapp" / "solutions.yaml").write_text(content)


@needs_yq
def test_deploy_resolves_a_registered_solution(tmp_path):
    _with_data_file(tmp_path, yaml.dump({"solutions": [
        {"name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com", "gapp_ref": "v3.9.1"},
    ]}))
    runner = _Runner(tmp_path, _state())
    ok, log = runner.run_job(ci_mod.DEPLOY_WORKFLOW, "resolve", DEPLOY_ENV)
    assert ok, log
    assert runner.outputs() == {"repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com",
                                "gapp_ref": "v3.9.1", "app_id": ""}


@needs_yq
def test_deploy_passes_the_hosting_app_for_a_repo_the_token_cannot_read(tmp_path):
    _with_data_file(tmp_path, "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: my-proj\n")
    runner = _Runner(tmp_path, _state(repos={}))
    ok, log = runner.run_job(ci_mod.DEPLOY_WORKFLOW, "resolve", {**DEPLOY_ENV, "APP_ID": "4242"})
    assert ok, log
    out = runner.outputs()
    assert out["app_id"] == "4242" and out["domain"] == "" and out["gapp_ref"] == ""


@needs_yq
def test_deploy_refuses_an_unregistered_solution_and_a_private_repo_without_app(tmp_path):
    _with_data_file(tmp_path, "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: my-proj\n")
    runner = _Runner(tmp_path, _state(repos={}))
    ok, log = runner.run_job(ci_mod.DEPLOY_WORKFLOW, "resolve", {**DEPLOY_ENV, "SOLUTION": "nope"})
    assert not ok and "'nope' is not registered" in log and "gapp ci setup --solution nope" in log
    ok, log = runner.run_job(ci_mod.DEPLOY_WORKFLOW, "resolve", DEPLOY_ENV)
    assert not ok and "HOSTING_GITHUB_APP_ID variable is not set" in log


@needs_yq
def test_deploy_refuses_a_project_without_environment_in_words(tmp_path):
    _with_data_file(tmp_path, "solutions:\n  - name: my-app\n    repo: me/my-app\n    project: my-proj\n")
    runner = _Runner(tmp_path, _state(environments=()))
    ok, log = runner.run_job(ci_mod.DEPLOY_WORKFLOW, "resolve", DEPLOY_ENV)
    assert not ok
    assert "no Environment named 'my-proj'" in log and "gapp ci project setup my-proj" in log


# -- the reusable workflow's first step names a missing deploy identity --


def _identity_check_step():
    doc = yaml.safe_load(pathlib.Path(".github/workflows/deploy.yml").read_text())
    return next(s for s in doc["jobs"]["deploy"]["steps"] if s.get("name") == "Check the deploy identity")


def test_reusable_workflow_checks_the_identity_first_and_names_the_missing_variable(tmp_path):
    step = _identity_check_step()
    assert step["if"].startswith("inputs.environment != ''")
    env = {**os.environ, "ENVIRONMENT": "my-proj", "WIF_PROVIDER": "", "DEPLOY_SA": "sa"}
    res = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert res.returncode == 1
    assert "no GAPP_WIF_PROVIDER variable" in res.stdout and "gapp ci project setup my-proj" in res.stdout
    env.update(WIF_PROVIDER="", DEPLOY_SA="")
    res = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert "GAPP_WIF_PROVIDER and GAPP_DEPLOY_SERVICE_ACCOUNT" in res.stdout
    env.update(WIF_PROVIDER="projects/1/x", DEPLOY_SA="sa")
    assert subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True).returncode == 0
