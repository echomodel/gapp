"""The two workflows `gapp ci init` installs in the fleet repo, and gapp's
reusable deploy workflow the first one calls.

Their shape is checked from the YAML. Each workflow's lookup step is run
for real with the Go yq pre-installed on GitHub's Ubuntu runners and a stub
`gh` (skipped where that yq is not installed). What runs after gapp is
installed is gapp itself, which `test_ci_runner.py` exercises.
"""

import json
import os
import pathlib
import shutil
import stat
import subprocess
import textwrap

import pytest
import yaml

import gapp
from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import entryfile

GAPP_REPO = "example-org/gapp"
GAPP_REF = "v4.0.0"
FLEET = "acme/fleet"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PIP = 'pip install --quiet "gapp @ git+https://github.com/{repo}.git@${{GAPP_REF}}"'


def _rendered(name):
    return yaml.safe_load(ci_mod._render_template(name, GAPP_REPO, GAPP_REF))


def _reusable():
    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text())


def _runs(steps):
    return [s for s in steps if "run" in s]


# -- shape --


def test_init_installs_exactly_the_deploy_and_status_workflows():
    files = ci_mod._workflow_files(GAPP_REPO, GAPP_REF)
    assert set(files) == {".github/workflows/gapp-deploy.yml", ".github/workflows/gapp-status.yml"}
    assert all("__GAPP_" not in text for text in files.values())


def test_the_deploy_workflow_is_started_with_a_name_and_whether_to_upgrade():
    doc = _rendered(ci_mod.DEPLOY_WORKFLOW)
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"request_id", "deployment", "upgrade"}
    assert doc["run-name"] == "Deploy ${{ inputs.deployment }} ${{ inputs.request_id }}"
    assert doc["permissions"] == {}


def test_the_deploy_workflow_passes_the_definition_to_the_reusable_workflow():
    doc = _rendered(ci_mod.DEPLOY_WORKFLOW)
    resolve, deploy = doc["jobs"]["resolve"], doc["jobs"]["deploy"]
    assert set(resolve["outputs"]) == {"repo", "project", "ref", "gapp_ref", "app_id"}
    assert resolve["steps"][0]["with"]["sparse-checkout"] == entryfile.FILE_NAME
    assert deploy["needs"] == "resolve" and deploy["permissions"] == {"id-token": "write", "contents": "read"}
    assert deploy["uses"] == f"{GAPP_REPO}/.github/workflows/deploy.yml@{GAPP_REF}"
    assert deploy["with"] == {
        "deployment": "${{ inputs.deployment }}", "upgrade": "${{ inputs.upgrade }}",
        "app-repo": "${{ needs.resolve.outputs.repo }}", "ref": "${{ needs.resolve.outputs.ref }}",
        "environment": "${{ needs.resolve.outputs.project }}", "gapp-repo": GAPP_REPO,
        "gapp-ref": "${{ needs.resolve.outputs.gapp_ref }}", "app-repo-app-id": "${{ needs.resolve.outputs.app_id }}",
        "workflows-version": gapp.__version__,
    }
    assert deploy["secrets"] == {"app-repo-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}
    assert set(deploy["with"]) == set(_reusable()[True]["workflow_call"]["inputs"])


def test_the_reusable_workflow_runs_gapp_with_the_deployments_name_and_nothing_else():
    doc = _reusable()
    job = doc["jobs"]["deploy"]
    assert job["environment"] == "${{ inputs.environment }}" and job["permissions"] == {"id-token": "write", "contents": "read"}
    names = [s.get("name") for s in job["steps"]]
    assert names == ["Check the deploy identity", "Check out the fleet", "Check the app repo credential",
                     "Mint a read-only token for the app repo", "Check out the app", "Sign in to Google Cloud",
                     "Set up gcloud", "Install Terraform", "Install gapp", "Deploy"]
    install, run = job["steps"][-2], job["steps"][-1]
    assert install["run"] == PIP.format(repo="${GAPP_REPO}")
    assert install["env"] == {"GAPP_REPO": "${{ inputs.gapp-repo }}", "GAPP_REF": "${{ inputs.gapp-ref }}"}
    assert run["run"] == 'gapp ci runner deploy "$DEPLOYMENT" $UPGRADE_FLAG' and run["working-directory"] == "app"
    assert run["env"] == {"GAPP_FLEET": "${{ github.workspace }}/fleet/gapp.yaml",
                          "GAPP_WORKFLOWS_VERSION": "${{ inputs.workflows-version }}",
                          "DEPLOYMENT": "${{ inputs.deployment }}",
                          "UPGRADE_FLAG": "${{ inputs.upgrade && '--upgrade' || '' }}"}


def test_the_reusable_workflow_checks_out_the_definitions_ref_of_the_app_beside_the_fleet():
    steps = {s.get("name"): s for s in _reusable()["jobs"]["deploy"]["steps"]}
    fleet, app = steps["Check out the fleet"]["with"], steps["Check out the app"]["with"]
    assert fleet["path"] == "fleet" and fleet["sparse-checkout"] == entryfile.FILE_NAME and fleet["persist-credentials"] is False
    assert app["path"] == "app" and app["repository"] == "${{ inputs.app-repo }}" and app["ref"] == "${{ inputs.ref }}"
    assert app["token"] == "${{ steps.app-token.outputs.token || github.token }}" and app["persist-credentials"] is False


def test_a_private_app_repo_gets_a_read_only_token_for_that_one_repo():
    steps = {s.get("name"): s for s in _reusable()["jobs"]["deploy"]["steps"]}
    mint = steps["Mint a read-only token for the app repo"]
    assert mint["if"] == "inputs.app-repo-app-id != ''" and mint["with"]["permission-contents"] == "read"
    assert mint["with"]["repositories"] == "${{ steps.app-repo.outputs.name }}"
    check = steps["Check the app repo credential"]
    env = {**os.environ, "APP_REPO_KEY": "", "APP_REPO": "me/my-app", "GITHUB_OUTPUT": os.devnull}
    res = subprocess.run(["bash", "-c", check["run"]], env=env, capture_output=True, text=True)
    assert res.returncode == 1 and "the app-repo-key secret is empty" in res.stdout


@pytest.mark.parametrize("load, job", [(_reusable, "deploy"), (lambda: _rendered(ci_mod.STATUS_WORKFLOW), "status")])
def test_a_missing_deploy_identity_is_named_with_the_command_that_records_it(load, job):
    step = next(s for s in load()["jobs"][job]["steps"] if s.get("name") == "Check the deploy identity")
    env = {**os.environ, "ENVIRONMENT": "my-proj", "WIF_PROVIDER": "", "DEPLOY_SA": "sa"}
    res = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert res.returncode == 1 and "has no GAPP_WIF_PROVIDER" in res.stdout
    assert "Run: gapp ci projects connect my-proj" in res.stdout
    env.update(WIF_PROVIDER="projects/1/x")
    assert subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True).returncode == 0


def test_the_status_workflow_is_read_only_and_asks_the_project_as_the_deploy_identity():
    doc = _rendered(ci_mod.STATUS_WORKFLOW)
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"request_id", "deployment"}
    assert doc["run-name"] == "Status ${{ inputs.deployment }} ${{ inputs.request_id }}"
    assert doc["permissions"] == {}
    resolve, status = doc["jobs"]["resolve"], doc["jobs"]["status"]
    assert resolve["permissions"] == {"contents": "read"} and set(resolve["outputs"]) == {"project", "gapp_ref"}
    assert status["permissions"] == {"id-token": "write", "contents": "read"}
    assert status["environment"] == "${{ needs.resolve.outputs.project }}" and status["needs"] == "resolve"
    names = [s.get("name") for s in status["steps"]]
    assert names == ["Check the deploy identity", "Check out the fleet", "Sign in to Google Cloud", "Set up gcloud",
                     "Install Terraform", "Install gapp", "Ask gapp"]
    install, ask = status["steps"][-2], status["steps"][-1]
    assert install["run"] == PIP.format(repo=GAPP_REPO)
    assert install["env"] == {"GAPP_REF": "${{ needs.resolve.outputs.gapp_ref }}"}
    assert ask["run"] == 'gapp ci runner status "$DEPLOYMENT"'
    assert ask["env"] == {"GAPP_FLEET": "${{ github.workspace }}/fleet/gapp.yaml",
                          "GAPP_WORKFLOWS_VERSION": gapp.__version__, "DEPLOYMENT": "${{ inputs.deployment }}"}


def test_no_script_interpolates_an_input_and_none_writes_to_github():
    jobs = [*_rendered(ci_mod.DEPLOY_WORKFLOW)["jobs"].values(), *_rendered(ci_mod.STATUS_WORKFLOW)["jobs"].values(),
            *_reusable()["jobs"].values()]
    for step in [s for job in jobs for s in _runs(job.get("steps") or [])]:
        assert "${{" not in step["run"], step.get("name")
        assert "gh api -X" not in step["run"] and "git push" not in step["run"], step.get("name")
    for job in jobs:
        assert "write" not in {v for k, v in (job.get("permissions") or {}).items() if k != "id-token"}


def test_both_runners_install_terraform_and_the_gcloud_beta_component():
    for steps in (_reusable()["jobs"]["deploy"]["steps"], _rendered(ci_mod.STATUS_WORKFLOW)["jobs"]["status"]["steps"]):
        assert [s["uses"] for s in steps if "terraform" in (s.get("uses") or "")] == ["hashicorp/setup-terraform@v3"]
        gcloud = next(s for s in steps if s.get("name") == "Set up gcloud")
        assert gcloud["with"] == {"install_components": "beta"}


# -- the lookup steps, run for real --


def _go_yq():
    """The Go yq (v4) prints "version v4.x"; other tools named yq print something else."""
    path = shutil.which("yq")
    if not path:
        return False
    return "version v4" in subprocess.run([path, "--version"], capture_output=True, text=True).stdout


needs_yq = pytest.mark.skipif(not _go_yq(), reason="the Go yq pre-installed on GitHub's Ubuntu runners is not installed")

STUB_GH = textwrap.dedent('''
    #!/usr/bin/env python3
    import json, os, sys
    state = json.load(open(os.environ["FAKE_GH_STATE"]))
    args = [a for a in sys.argv[1:] if a not in ("api", "--silent")]
    endpoint = args[0]
    parts = endpoint.split("/")
    repo = "/".join(parts[1:3]); tail = parts[3:]
    if tail[:1] == ["environments"]:
        sys.exit(0 if tail[1] in state["environments"] else 1)
    if not tail:
        if repo not in state["repos"]:
            sys.stderr.write("gh: Not Found (HTTP 404)\\n"); sys.exit(1)
        print("true" if state["repos"][repo] else "false"); sys.exit(0)
    sys.stderr.write("unexpected endpoint " + endpoint + "\\n"); sys.exit(1)
''').lstrip()

DEFINITION = {"id": "my-app", "repo": "me/my-app", "project": "my-proj", "gapp": "4.0.0", "settings": {}}
DEPLOY_ENV = {"DEPLOYMENT": "my-app", "APP_ID": "", "GH_TOKEN": "x"}


class _Runner:
    """A runner's workspace: the fleet repo checked out, a stub `gh`, and the step's outputs file."""

    def __init__(self, tmp_path, workflow=None, environments=("my-proj",), repos=None):
        self.dir, self.workflow = tmp_path, workflow or ci_mod.DEPLOY_WORKFLOW
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(STUB_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        state = tmp_path / "state.json"
        state.write_text(json.dumps({"environments": list(environments),
                                     "repos": {"me/my-app": False} if repos is None else repos}))
        self.output = tmp_path / "output.txt"
        self.output.touch()
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_GH_STATE": str(state),
                    "GITHUB_REPOSITORY": FLEET, "GITHUB_OUTPUT": str(self.output)}

    def fleet(self, deployments, **top):
        (self.dir / entryfile.FILE_NAME).write_text(entryfile.dump({"deployments": deployments, **top}))
        return self

    def resolve(self, **env):
        step = _rendered(self.workflow)["jobs"]["resolve"]["steps"][1]
        fixed = {k: str(v) for k, v in step["env"].items() if not str(v).startswith("${{")}
        given = {**(DEPLOY_ENV if self.workflow == ci_mod.DEPLOY_WORKFLOW else {"DEPLOYMENT": "my-app"}), **env}
        res = subprocess.run(["bash", "-c", step["run"]], cwd=self.dir, env={**self.env, **fixed, **given},
                             capture_output=True, text=True)
        return res.returncode == 0, res.stdout + res.stderr

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)


@needs_yq
def test_deploy_reads_the_repo_ref_project_and_gapp_version_from_the_definition(tmp_path):
    runner = _Runner(tmp_path).fleet({"my-app": {**DEFINITION, "ref": "v1.4.0", "gapp": "4.0.0rc15"}})
    ok, log = runner.resolve()
    assert ok, log
    assert runner.outputs() == {"repo": "me/my-app", "project": "my-proj", "ref": "v1.4.0", "gapp_ref": "v4.0.0rc15", "app_id": ""}
    assert "gapp-deploy: my-app -> me/my-app (v1.4.0) on my-proj, with gapp 4.0.0rc15" in log


@needs_yq
def test_deploy_with_no_ref_in_the_definition_builds_the_default_branch(tmp_path):
    runner = _Runner(tmp_path).fleet({"my-app": DEFINITION})
    ok, log = runner.resolve()
    assert ok and runner.outputs()["ref"] == "" and "(the default branch)" in log


@needs_yq
def test_a_repo_with_no_fleet_file_names_fleet_use(tmp_path):
    ok, log = _Runner(tmp_path).resolve()
    assert not ok and "this repo has no gapp.yaml, so it holds no fleet. Keep your fleet here with: gapp fleet use acme/fleet" in log


@needs_yq
@pytest.mark.parametrize("fmt, shown", [(2, "2"), (None, "none")])
def test_a_fleet_file_in_another_format_is_refused_before_anything_else_is_read(tmp_path, fmt, shown):
    runner = _Runner(tmp_path)
    doc = {"deployments": {"my-app": DEFINITION}}
    if fmt is not None:
        doc = {"format": fmt, **doc}
    (tmp_path / entryfile.FILE_NAME).write_text(yaml.safe_dump(doc, sort_keys=False))
    ok, log = runner.resolve()
    assert not ok and runner.outputs() == {}
    assert f"gapp.yaml is in format '{shown}', and this workflow reads format 1, so nothing was deployed." in log
    assert "gapp ci init" in log


@needs_yq
def test_a_name_that_is_not_in_the_fleet_names_add(tmp_path):
    ok, log = _Runner(tmp_path).fleet({"my-app": DEFINITION}).resolve(DEPLOYMENT="nope")
    assert not ok and "'nope' is not a deployment in this fleet. Define it first: gapp add nope --project <project>" in log


@needs_yq
def test_a_name_is_never_evaluated_as_a_yq_expression(tmp_path):
    runner = _Runner(tmp_path).fleet({"my-app": DEFINITION})
    ok, log = runner.resolve(DEPLOYMENT='my-app"] | .x = "1')
    assert not ok and "is not a deployment in this fleet" in log
    assert yaml.safe_load((tmp_path / entryfile.FILE_NAME).read_text())["deployments"] == {"my-app": DEFINITION}


@needs_yq
@pytest.mark.parametrize("repo, said", [
    (None, "'my-app' has no repo, so it can only be deployed from a clone, with gapp deploy."),
    ("git.example.com/team/app", "'my-app' runs git.example.com/team/app, which is not a repo on GitHub."),
])
def test_a_definition_a_runner_cannot_check_out_is_refused(tmp_path, repo, said):
    definition = {k: v for k, v in DEFINITION.items() if k != "repo"}
    if repo:
        definition["repo"] = repo
    ok, log = _Runner(tmp_path).fleet({"my-app": definition}).resolve()
    assert not ok and said in log


@needs_yq
@pytest.mark.parametrize("version", [None, "main", "4.0", "v4.0.0", "4.0.0; echo x", "4.0.0-rc1"])
def test_only_a_gapp_version_is_installed(tmp_path, version):
    definition = {k: v for k, v in DEFINITION.items() if k != "gapp"}
    if version:
        definition["gapp"] = version
    runner = _Runner(tmp_path).fleet({"my-app": definition})
    ok, log = runner.resolve()
    assert not ok and runner.outputs() == {}
    assert f"'my-app' names gapp '{version or 'none'}', which is not a gapp version, so nothing was deployed." in log


@needs_yq
def test_a_repo_of_any_owner_is_deployed(tmp_path):
    runner = _Runner(tmp_path, repos={"other-org/app": False}).fleet({"my-app": {**DEFINITION, "repo": "other-org/app"}})
    ok, log = runner.resolve()
    assert ok, log
    assert runner.outputs()["repo"] == "other-org/app"
    assert "ALLOWED_SOURCES" not in ci_mod._render_template(ci_mod.DEPLOY_WORKFLOW, GAPP_REPO, GAPP_REF)


@needs_yq
def test_a_project_runners_are_not_connected_to_names_connect(tmp_path):
    ok, log = _Runner(tmp_path, environments=()).fleet({"my-app": DEFINITION}).resolve()
    assert not ok and ("runners in this repo are not connected to my-proj, so 'my-app' was not deployed. "
                       "Run: gapp ci projects connect my-proj") in log


@needs_yq
def test_a_private_repo_is_cloned_with_the_hosting_app(tmp_path):
    runner = _Runner(tmp_path, repos={"me/my-app": True}).fleet({"my-app": DEFINITION})
    ok, log = runner.resolve(APP_ID="12345")
    assert ok, log
    assert runner.outputs()["app_id"] == "12345"


@needs_yq
@pytest.mark.parametrize("repos", [{"me/my-app": True}, {}])
def test_a_repo_the_run_cannot_read_without_the_hosting_app_is_refused(tmp_path, repos):
    ok, log = _Runner(tmp_path, repos=repos).fleet({"my-app": DEFINITION}).resolve()
    assert not ok and "me/my-app is not readable with the run's token (private, or it does not exist)" in log
    assert "HOSTING_GITHUB_APP_ID" in log and "gapp show --deployment my-app" in log


@needs_yq
def test_status_reads_the_project_and_the_gapp_version(tmp_path):
    runner = _Runner(tmp_path, ci_mod.STATUS_WORKFLOW).fleet({"my-app": {k: v for k, v in DEFINITION.items() if k != "repo"}})
    ok, log = runner.resolve()
    assert ok, log
    assert runner.outputs() == {"project": "my-proj", "gapp_ref": "v4.0.0"}


@needs_yq
def test_status_refuses_what_deploy_refuses_about_the_file(tmp_path):
    runner = _Runner(tmp_path, ci_mod.STATUS_WORKFLOW)
    assert "this repo has no gapp.yaml" in runner.resolve()[1]
    runner.fleet({"my-app": {**DEFINITION, "gapp": "main"}})
    assert "'my-app' names gapp 'main', which is not a gapp version." in runner.resolve()[1]
    assert "'nope' is not a deployment in this fleet" in runner.resolve(DEPLOYMENT="nope")[1]


# -- the runner hand-off: what the workflows give `gapp ci runner` --

# What the workflows hand gapp, per minimum (gapp.COMPATIBLE_FROM): the
# variables gapp reads, the command, and the folder it runs in. A change to
# the hand-off either keeps all of this (adding to `reads` is fine) or is a
# new minimum with its own entry here; see CONTRIBUTING, "The runner hand-off".
HANDOFF = {
    "4.0.0rc15": {
        "deploy": {"reads": {"GAPP_FLEET"}, "run": 'gapp ci runner deploy "$DEPLOYMENT" $UPGRADE_FLAG',
                   "working-directory": "app"},
        "status": {"reads": {"GAPP_FLEET"}, "run": 'gapp ci runner status "$DEPLOYMENT"',
                   "working-directory": None},
    },
}
SHELL_ONLY = {"DEPLOYMENT", "UPGRADE_FLAG"}   # build the command line; gapp never reads them


def _handoff_step(steps, command):
    found = [s for s in steps if "gapp ci runner " + command in (s.get("run") or "")]
    assert len(found) == 1
    step = found[0]
    return {"reads": set(step.get("env") or {}) - SHELL_ONLY, "run": step["run"].strip(),
            "working-directory": step.get("working-directory")}


def test_the_hand_off_is_the_one_its_minimum_promises():
    recorded = HANDOFF.get(gapp.COMPATIBLE_FROM)
    assert recorded, (f"The minimum is {gapp.COMPATIBLE_FROM} but HANDOFF has no entry for it: "
                      f"record what the workflows hand gapp from that version on.")
    now = {"deploy": _handoff_step(_reusable()["jobs"]["deploy"]["steps"], "deploy"),
           "status": _handoff_step(_rendered(ci_mod.STATUS_WORKFLOW)["jobs"]["status"]["steps"], "status")}
    for command, promised in recorded.items():
        current = now[command]
        assert promised["reads"] <= current["reads"], (
            f"The {command} workflow no longer gives gapp {sorted(promised['reads'] - current['reads'])}, which gapp "
            f"{gapp.COMPATIBLE_FROM} reads. Keep it as well, or raise COMPATIBLE_FROM.")
        assert (current["run"], current["working-directory"]) == (promised["run"], promised["working-directory"]), (
            f"The {command} workflow runs gapp differently from what gapp {gapp.COMPATIBLE_FROM} expects. "
            f"Keep the old form, or raise COMPATIBLE_FROM.")


def _with_gapp(tmp_path, workflow, version):
    return _Runner(tmp_path, workflow=workflow).fleet({"my-app": {**DEFINITION, "gapp": version}})


@needs_yq
@pytest.mark.parametrize("workflow, prefix, what", [
    (ci_mod.DEPLOY_WORKFLOW, "gapp-deploy", "nothing was deployed"),
    (ci_mod.STATUS_WORKFLOW, "gapp-status", "it was not asked"),
])
def test_a_definition_on_a_gapp_older_than_the_minimum_is_refused_before_anything_is_installed(
        tmp_path, workflow, prefix, what):
    runner = _with_gapp(tmp_path, workflow, "4.0.0rc14")
    ok, log = runner.resolve()
    assert not ok and runner.outputs() == {}
    assert (f"{prefix}: 'my-app' is defined with gapp 4.0.0rc14, and these workflows need "
            f"{gapp.COMPATIBLE_FROM} or later, so {what}. Upgrade it first: gapp upgrade --deployment my-app, "
            f"then gapp ci deploy --deployment my-app --upgrade") in log


@needs_yq
@pytest.mark.parametrize("workflow", [ci_mod.DEPLOY_WORKFLOW, ci_mod.STATUS_WORKFLOW])
@pytest.mark.parametrize("version", ["4.0.0rc15", "4.0.0", "4.0.10", "4.1.0rc2", "10.0.0"])
def test_the_minimum_and_anything_later_is_run(tmp_path, workflow, version):
    ok, log = _with_gapp(tmp_path, workflow, version).resolve()
    assert ok, log
