"""`gapp ci manifest <name> --merged [--ref REF]`: what a CI deploy would use,
worked out from GitHub reads with the caller's own login. Nothing is saved,
no runner, no GCP."""

import pytest
import yaml
from click.testing import CliRunner

from gapp.admin.cli.main import main
from gapp.admin.sdk import ci as ci_mod

from tests.unit.test_ci_setup import FLEET, wired  # noqa: F401  (fixture)

APP = "me/my-app"


@pytest.fixture
def deployed(wired):
    wired.deploy(FLEET, "my-app", APP, "my-proj", public=True, service={"memory": "1Gi"},
                 env=[{"name": "API_KEY", "secret": {"name": "api-key"}}])
    wired.files[(APP, "gapp.yaml")] = ("name: my-app\npublic: false\nservice:\n  memory: 512Mi\n  cpu: '2'\n"
                                       "env:\n  - name: LOG_LEVEL\n    value: info\n")
    wired.files[(APP, "Procfile")] = "web: my-app serve\n"
    return wired


def _pin(fake, ref):
    path = (FLEET, ci_mod.DEPLOY_WORKFLOW_PATH)
    fake.files[path] = f"jobs:\n  deploy:\n    uses: echomodel/gapp/.github/workflows/deploy.yml@{ref}\n"


def test_merged_is_the_fleet_manifest_over_the_repos_gapp_yaml_at_the_default_branch(deployed):
    writes = len(deployed.puts)
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is True, res
    assert res["ref"] == "main" and res["repo_has_gapp_yaml"] is True
    assert res["merged"] == {
        "name": "my-app", "public": True, "service": {"memory": "1Gi", "cpu": "2"},
        "env": [{"name": "LOG_LEVEL", "value": "info"}, {"name": "API_KEY", "secret": {"name": "api-key"}}],
    }
    assert res["start"]["method"] == "procfile" and res["problems"] == []
    assert len(deployed.puts) == writes and deployed.dispatched == []


def test_merged_at_a_ref_reads_that_version_of_the_app_repo(deployed):
    deployed.ref_files[(APP, "v1.4.0")] = {"gapp.yaml": "public: false\nservice:\n  cpu: '4'\n",
                                           "Dockerfile": "FROM scratch\n"}
    res = ci_mod.merged_manifest("my-app", ref="v1.4.0")
    assert res["ok"] is True, res
    assert res["ref"] == "v1.4.0" and res["commit"] == "sha-v1.4.0"
    assert res["merged"]["service"] == {"memory": "1Gi", "cpu": "4"}
    assert res["start"]["method"] == "dockerfile"


def test_with_no_gapp_yaml_in_the_repo_the_manifest_is_the_whole_file(deployed):
    del deployed.files[(APP, "gapp.yaml")]
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is True and res["repo_has_gapp_yaml"] is False
    assert res["merged"] == {"name": "my-app", "public": True, "service": {"memory": "1Gi"},
                             "env": [{"name": "API_KEY", "secret": {"name": "api-key"}}]}
    assert list(res["merged"])[0] == "name"


def test_an_app_repo_this_login_cannot_read_fails_only_this_and_says_the_deploy_may_still_work(deployed):
    deployed.unreadable.add(APP)
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is False and res["merged"] is None
    hint = res["next_step"]["hint"]
    assert hint.startswith(f"Can't read {APP} from here")
    assert "does not mean the deploy fails" in hint and "gapp ci manifest my-app" in hint
    assert ci_mod.deployment_manifest("my-app")["ok"] is True


def test_a_ref_the_repo_does_not_have_is_named(deployed):
    res = ci_mod.merged_manifest("my-app", ref="nope")
    assert res["ok"] is False and "has no branch, tag or commit 'nope'" in res["next_step"]["hint"]


def test_merged_settings_gapp_does_not_accept_are_a_problem_and_still_shown(deployed):
    deployed.files[(APP, "gapp.yaml")] = "publik: true\n"
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is False and res["merged"]["publik"] is True
    assert "publik" in res["problems"][0] and "the deploy would refuse" in res["problems"][0]


def test_an_unparseable_repo_gapp_yaml_is_a_problem(deployed):
    deployed.files[(APP, "gapp.yaml")] = "public: [true\n"
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is False and "can't be read as YAML" in res["problems"][0]


def test_start_in_the_manifest_is_reported_ignored_when_the_repo_has_a_procfile(deployed):
    deployed.deploy(FLEET, "my-app", APP, "my-proj", start="other serve")
    res = ci_mod.merged_manifest("my-app")
    assert res["start"] == {"method": "procfile", "web": "my-app serve", "ignored": "gapp.yaml's start",
                            "reason": None}


def test_no_start_method_is_a_problem_and_start_in_the_manifest_fixes_it(deployed):
    del deployed.files[(APP, "Procfile")]
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is False and "would refuse it" in res["problems"][0]
    deployed.deploy(FLEET, "my-app", APP, "my-proj", start="my-app serve")
    res = ci_mod.merged_manifest("my-app")
    assert res["ok"] is True and res["start"]["method"] == "start"


def test_a_fleet_on_another_gapp_version_gets_a_note(deployed):
    import gapp
    installed = f"v{gapp.__version__}"
    _pin(deployed, installed)
    assert ci_mod.merged_manifest("my-app")["notes"] == []
    _pin(deployed, "v0.0.1")
    notes = ci_mod.merged_manifest("my-app")["notes"]
    assert len(notes) == 1 and "gapp v0.0.1" in notes[0] and f"gapp ci init {FLEET}" in notes[0]


def test_cli_merged_prints_only_yaml_after_its_comment_lines(deployed):
    res = CliRunner().invoke(main, ["ci", "manifest", "my-app", "--merged"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith("# What a deploy of my-app at main uses:")
    assert yaml.safe_load(res.output)["service"] == {"memory": "1Gi", "cpu": "2"}


def test_cli_ref_implies_merged(deployed):
    deployed.ref_files[(APP, "v1.4.0")] = {"Procfile": "web: x\n"}
    res = CliRunner().invoke(main, ["ci", "manifest", "my-app", "--ref", "v1.4.0"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith("# What a deploy of my-app at v1.4.0 (sha-v1.4.0) uses: the fleet-side manifest "
                                 "alone (me/my-app has no gapp.yaml).")


def test_cli_problems_go_to_stderr_with_exit_1_and_the_yaml_still_prints(deployed):
    del deployed.files[(APP, "Procfile")]
    res = CliRunner().invoke(main, ["ci", "manifest", "my-app", "--merged"])
    assert res.exit_code == 1
    assert "would refuse it" in res.stderr and "would refuse it" not in res.stdout
    assert yaml.safe_load(res.stdout)["name"] == "my-app"


def test_cli_without_merged_is_unchanged_and_never_reads_the_app_repo(deployed):
    deployed.unreadable.add(APP)
    res = CliRunner().invoke(main, ["ci", "manifest", "my-app"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith("# Fleet-side manifest for my-app")
