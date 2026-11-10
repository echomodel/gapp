"""The two workflows `gapp ci init` installs in the fleet repo, and gapp's
reusable deploy workflow they call.

Their shape is checked from the YAML. The deploy workflow's lookup step is
run for real with the Go yq pre-installed on GitHub's Ubuntu runners and a
stub `gh` (skipped where that yq is not installed); the register workflow
runs gapp itself, whose code `test_ci_rules.py` exercises directly.
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

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import fleet

GAPP_REPO = "example-org/gapp"
GAPP_REF = "v4.0.0"
FLEET = "acme/fleet"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _rendered(name):
    return ci_mod._render_template(name, GAPP_REPO, GAPP_REF)


# -- shape --


def test_the_reusable_workflow_deploys_the_default_branch_when_no_ref_is_given():
    import pathlib
    doc = yaml.safe_load((pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy.yml").read_text())
    assert doc[True]["workflow_call"]["inputs"]["ref"]["default"] == ""
    steps = doc["jobs"]["deploy"]["steps"]
    clone = next(s for s in steps if s.get("name") == "Clone solution repo")
    assert clone["with"]["ref"] == "${{ inputs.ref }}"               # empty: actions/checkout takes the default branch
    run = next(s for s in steps if s.get("name") == "Setup and deploy")
    assert run["env"]["REF_FLAG"] == "${{ inputs.ref != '' && format('--ref {0}', inputs.ref) || '' }}"
    assert "gapp deploy $REF_FLAG $PROJECT_FLAG" in run["run"]        # no bare --ref swallowing the next flag


def test_deploy_workflow_resolves_the_entry_then_calls_the_reusable_workflow_in_the_project_environment():
    doc = yaml.safe_load(_rendered(ci_mod.DEPLOY_WORKFLOW))
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"request_id", "deployment", "ref"}
    assert doc["run-name"] == "Deploy ${{ inputs.deployment }} (${{ inputs.ref || 'default branch' }}) ${{ inputs.request_id }}"
    assert doc[True]["workflow_dispatch"]["inputs"]["ref"]["default"] == ""        # empty: the app repo's default branch
    resolve, deploy = doc["jobs"]["resolve"], doc["jobs"]["deploy"]
    assert set(resolve["outputs"]) == {"repo", "project", "id", "overlay", "app_id"}
    assert deploy["needs"] == "resolve"
    assert deploy["uses"] == f"{GAPP_REPO}/.github/workflows/deploy.yml@{GAPP_REF}"
    assert deploy["with"]["environment"] == "${{ needs.resolve.outputs.project }}"
    assert deploy["with"]["solution-repo"] == "${{ needs.resolve.outputs.repo }}"
    assert deploy["with"]["solution-name"] == "${{ needs.resolve.outputs.id }}"
    assert deploy["with"]["overlay"] == "${{ needs.resolve.outputs.overlay }}"
    assert deploy["with"]["gapp-repo"] == GAPP_REPO and deploy["with"]["gapp-ref"] == GAPP_REF
    assert deploy["secrets"] == {"solution-repo-app-private-key": "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}"}
    assert deploy["permissions"] == {"id-token": "write", "contents": "read"}
    step = resolve["steps"][1]
    assert step["env"]["DEPLOYMENT"] == "${{ inputs.deployment }}" and "${{" not in step["run"]
    assert resolve["steps"][0]["with"]["sparse-checkout"] == fleet.DEPLOYMENTS_FILE


def test_register_workflow_installs_gapp_and_runs_the_runner_with_inputs_through_env():
    doc = yaml.safe_load(_rendered(ci_mod.REGISTER_WORKFLOW))
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"request_id", "operation", "name", "repo", "project", "changes", "source"}
    assert doc["run-name"] == "Register ${{ inputs.name }} ${{ inputs.request_id }}"
    job = doc["jobs"]["register"]
    assert job["permissions"] == {"contents": "write", "actions": "read"}
    for key in ("OPERATION", "NAME", "REPO", "PROJECT", "CHANGES", "SOURCE"):
        assert job["env"][key].startswith("${{ inputs."), key
    assert job["env"]["ALLOWED_SOURCES"] == "${{ vars.GAPP_ALLOWED_SOURCES }}"
    assert job["env"]["APP_KEY_SET"] == "${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY != '' }}"
    assert job["env"]["FLEET_REPO"] == "${{ github.repository }}"
    runs = [s["run"] for s in job["steps"]]
    assert runs[0] == f'pip install --quiet "gapp @ git+https://github.com/{GAPP_REPO}.git@{GAPP_REF}"'
    assert runs[1] == "gapp ci runner register"
    for step in job["steps"]:
        assert "${{" not in step["run"]
    assert "__GAPP_" not in _rendered(ci_mod.REGISTER_WORKFLOW)


def test_status_workflow_is_read_only_and_asks_the_project_as_the_deploy_identity():
    doc = yaml.safe_load(_rendered(ci_mod.STATUS_WORKFLOW))
    assert set(doc[True]["workflow_dispatch"]["inputs"]) == {"request_id", "deployment"}
    assert doc["run-name"] == "Status ${{ inputs.deployment }} ${{ inputs.request_id }}"
    assert doc["permissions"] == {}
    resolve, status = doc["jobs"]["resolve"], doc["jobs"]["status"]
    assert resolve["permissions"] == {"contents": "read"}
    assert status["permissions"] == {"id-token": "write", "contents": "read"}      # read-only: no contents write
    assert status["environment"] == "${{ needs.resolve.outputs.project }}"
    assert status["needs"] == "resolve"
    names = [s.get("name") for s in status["steps"]]
    assert names == ["Check the deploy identity", "Authenticate to Google Cloud", "Set up gcloud", "Install Terraform",
                     "Install gapp", "Ask gapp status"]
    auth = status["steps"][1]["with"]
    assert auth == {"workload_identity_provider": "${{ vars.GAPP_WIF_PROVIDER }}", "service_account": "${{ vars.GAPP_DEPLOY_SERVICE_ACCOUNT }}"}
    ask = status["steps"][-1]
    assert ask["run"] == "gapp ci runner status"
    assert ask["env"]["SOLUTION_ID"] == "${{ needs.resolve.outputs.id }}" and ask["env"]["PROJECT"] == "${{ needs.resolve.outputs.project }}"
    assert status["steps"][4]["run"] == f'pip install --quiet "gapp @ git+https://github.com/{GAPP_REPO}.git@{GAPP_REF}"'
    for step in [s for s in resolve["steps"] + status["steps"] if "run" in s]:
        assert "${{" not in step["run"]
    assert not any("PUT" in (s.get("run") or "") or "gh api -X" in (s.get("run") or "") for s in status["steps"])


def test_status_workflow_installs_terraform_like_the_deploy_workflow():
    """`gapp status` reads the deployment's Terraform outputs, so the runner needs terraform."""
    status_steps = yaml.safe_load(_rendered(ci_mod.STATUS_WORKFLOW))["jobs"]["status"]["steps"]
    deploy_steps = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text())["jobs"]["deploy"]["steps"]
    uses = lambda steps: [s.get("uses") for s in steps if "terraform" in (s.get("uses") or "")]
    assert uses(status_steps) == uses(deploy_steps) == ["hashicorp/setup-terraform@v3"]
    names = [s.get("name") for s in status_steps]
    assert names.index("Install Terraform") < names.index("Ask gapp status")


def test_templates_ship_with_the_package():
    assert "__GAPP_REPO__" not in _rendered(ci_mod.DEPLOY_WORKFLOW)
    files = ci_mod._operator_files(GAPP_REPO, GAPP_REF)
    assert set(files) == {ci_mod.DEPLOY_WORKFLOW_PATH, ci_mod.REGISTER_WORKFLOW_PATH, ci_mod.STATUS_WORKFLOW_PATH}


def test_reusable_workflow_takes_an_overlay_and_applies_it_with_gapp_after_installing_it():
    doc = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text())
    inputs = doc[True]["workflow_call"]["inputs"]
    assert "overlay" in inputs and inputs["overlay"]["required"] is False and "domain" in inputs
    steps = doc["jobs"]["deploy"]["steps"]
    names = [s.get("name") for s in steps]
    install, overlay, deploy = names.index("Install gapp"), names.index("Apply the deployment's gapp.yaml keys"), names.index("Setup and deploy")
    assert install < overlay < deploy
    step = steps[overlay]
    assert step["run"] == "gapp ci runner overlay gapp.yaml"
    assert step["env"] == {"OVERLAY": "${{ inputs.overlay }}", "DOMAIN": "${{ inputs.domain }}"}
    assert step["if"] == "inputs.overlay != '' || inputs.domain != ''"
    assert step["working-directory"] == "${{ inputs.solution-name }}"
    assert not any("yq" in (s.get("run") or "") for s in steps)


def test_reusable_workflow_checks_the_identity_first_and_names_the_missing_variable():
    doc = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text())
    step = next(s for s in doc["jobs"]["deploy"]["steps"] if s.get("name") == "Check the deploy identity")
    assert step["if"].startswith("inputs.environment != ''")
    env = {**os.environ, "ENVIRONMENT": "my-proj", "WIF_PROVIDER": "", "DEPLOY_SA": "sa"}
    res = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)
    assert res.returncode == 1
    assert "no GAPP_WIF_PROVIDER variable" in res.stdout and "of the fleet repo" in res.stdout
    assert "gapp ci project setup my-proj" in res.stdout
    env.update(WIF_PROVIDER="projects/1/x", DEPLOY_SA="sa")
    assert subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True).returncode == 0


# -- the deploy workflow's lookup step, run for real --


def _go_yq():
    """The Go yq (v4) prints "version v4.x"; other tools named yq print something else."""
    path = shutil.which("yq")
    if not path:
        return False
    out = subprocess.run([path, "--version"], capture_output=True, text=True).stdout
    return "version v4" in out


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


class _Runner:
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
        self.output.touch()
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state_path),
                    "GITHUB_REPOSITORY": FLEET, "GITHUB_OUTPUT": str(self.output)}

    def run_resolve(self, env):
        doc = yaml.safe_load(_rendered(ci_mod.DEPLOY_WORKFLOW))
        step = doc["jobs"]["resolve"]["steps"][1]
        full_env = {**self.env, **{k: ("" if v is None else str(v)) for k, v in (step.get("env") or {}).items()
                                   if not str(v).startswith("${{")}, **env}
        res = subprocess.run(["bash", "-c", step["run"]], cwd=self.dir, env=full_env, capture_output=True, text=True)
        return res.returncode == 0, res.stdout + res.stderr

    def outputs(self):
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)


def _with_fleet_file(tmp_path, doc):
    (tmp_path / "gapp").mkdir(exist_ok=True)
    (tmp_path / "gapp" / "deployments.yaml").write_text(fleet.dump(doc))


DEPLOY_ENV = {"DEPLOYMENT": "my-app", "APP_ID": "", "ALLOWED_SOURCES": "me", "GH_TOKEN": "x", "DATA_FILE": "gapp/deployments.yaml"}


@needs_yq
def test_deploy_resolves_a_deployment_into_repo_project_id_and_overlay(tmp_path):
    _with_fleet_file(tmp_path, {"my-app": {"id": "my-app-2", "repo": "me/my-app", "project": "my-proj",
                                           "domain": "mcp.example.com", "env": [{"name": "A", "value": "1"}]}})
    runner = _Runner(tmp_path, {"environments": ["my-proj"], "repos": {"me/my-app": False}})
    ok, log = runner.run_resolve(DEPLOY_ENV)
    assert ok, log
    out = runner.outputs()
    assert out["repo"] == "me/my-app" and out["project"] == "my-proj" and out["id"] == "my-app-2" and out["app_id"] == ""
    assert json.loads(out["overlay"]) == {"domain": "mcp.example.com", "env": [{"name": "A", "value": "1"}], "name": "my-app-2"}


@needs_yq
def test_deploy_passes_the_hosting_app_for_a_repo_the_token_cannot_read(tmp_path):
    _with_fleet_file(tmp_path, {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}})
    runner = _Runner(tmp_path, {"environments": ["my-proj"], "repos": {}})
    ok, log = runner.run_resolve({**DEPLOY_ENV, "APP_ID": "4242"})
    assert ok, log
    out = runner.outputs()
    assert out["app_id"] == "4242" and json.loads(out["overlay"]) == {"name": "my-app"}


@needs_yq
def test_deploy_refuses_an_unknown_deployment_a_private_repo_without_app_and_an_untrusted_project(tmp_path):
    _with_fleet_file(tmp_path, {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}})
    runner = _Runner(tmp_path, {"environments": ["my-proj"], "repos": {}})
    ok, log = runner.run_resolve({**DEPLOY_ENV, "DEPLOYMENT": "nope"})
    assert not ok and "'nope' is not a deployment" in log and "gapp ci setup nope --repo <owner>/<repo>" in log
    ok, log = runner.run_resolve(DEPLOY_ENV)
    assert not ok and "HOSTING_GITHUB_APP_ID variable is not set in this fleet repo" in log
    runner = _Runner(tmp_path / "two", {"environments": [], "repos": {"me/my-app": False}}) if (tmp_path / "two").mkdir() is None else None
    _with_fleet_file(tmp_path / "two", {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj"}})
    ok, log = runner.run_resolve(DEPLOY_ENV)
    assert not ok and "no Environment named 'my-proj'" in log and "gapp ci project setup my-proj" in log


def test_deploy_reads_the_allowed_sources_from_the_vars_context_in_the_existing_step():
    """The owner re-check costs nothing: the repository variable, read in the step that already reads the entry."""
    doc = yaml.safe_load(_rendered(ci_mod.DEPLOY_WORKFLOW))
    assert list(doc["jobs"]) == ["resolve", "deploy"]
    step = doc["jobs"]["resolve"]["steps"][1]
    assert step["name"] == "Read the entry"
    assert step["env"]["ALLOWED_SOURCES"] == "${{ vars.GAPP_ALLOWED_SOURCES }}"
    assert doc["jobs"]["resolve"]["permissions"] == {"contents": "read", "actions": "read"}


@needs_yq
def test_deploy_refuses_an_entry_whose_owner_is_not_allowed_even_if_added_by_hand(tmp_path):
    _with_fleet_file(tmp_path, {"my-app": {"id": "my-app", "repo": "Other/my-app", "project": "my-proj"}})
    runner = _Runner(tmp_path, {"environments": ["my-proj"], "repos": {"Other/my-app": False}})
    for allowed in ("me", "", "me,other-org"):
        ok, log = runner.run_resolve({**DEPLOY_ENV, "ALLOWED_SOURCES": allowed})
        assert not ok, allowed
        assert ("gapp-deploy: Apps owned by Other aren't allowed in this fleet, so 'my-app' (Other/my-app) was not "
                "deployed. Allow the owner with: gapp ci sources add Other") in log
        assert runner.outputs() == {}


@needs_yq
def test_deploy_matches_the_allowed_owner_case_insensitively_and_ignores_spaces(tmp_path):
    _with_fleet_file(tmp_path, {"my-app": {"id": "my-app", "repo": "Me/my-app", "project": "my-proj"}})
    runner = _Runner(tmp_path, {"environments": ["my-proj"], "repos": {"Me/my-app": False}})
    ok, log = runner.run_resolve({**DEPLOY_ENV, "ALLOWED_SOURCES": "someone, ME "})
    assert ok, log
    assert runner.outputs()["repo"] == "Me/my-app"


@pytest.mark.parametrize("path", [ci_mod.DEPLOY_WORKFLOW, ci_mod.REGISTER_WORKFLOW, ci_mod.STATUS_WORKFLOW])
def test_every_dispatched_workflow_ends_its_run_name_with_the_optional_request_id(path):
    """gapp finds the run its dispatch created by the id at the end of the run name."""
    doc = yaml.safe_load(_rendered(path))
    assert doc["run-name"].endswith(" ${{ inputs.request_id }}")
    request_id = doc[True]["workflow_dispatch"]["inputs"]["request_id"]
    assert request_id["required"] is False and request_id["default"] == "" and request_id["type"] == "string"


def _gcloud_setup_step(doc_steps):
    return next(s for s in doc_steps if "/setup-gcloud@" in str(s.get("uses", "")))


def test_status_workflow_installs_gclouds_beta_component_for_domain_mappings():
    """`gcloud beta run domain-mappings` is the only command for a managed Cloud Run mapping."""
    doc = yaml.safe_load(_rendered(ci_mod.STATUS_WORKFLOW))
    steps = next(j for j in doc["jobs"].values() if any("setup-gcloud" in str(s.get("uses", "")) for s in j.get("steps", [])))["steps"]
    assert _gcloud_setup_step(steps)["with"]["install_components"] == "beta"


def test_reusable_deploy_workflow_installs_gclouds_beta_component():
    doc = yaml.safe_load((pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/deploy.yml").read_text())
    steps = [s for j in doc["jobs"].values() for s in j.get("steps", [])]
    assert _gcloud_setup_step(steps)["with"]["install_components"] == "beta"
