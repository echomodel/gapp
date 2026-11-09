"""`gapp ci setup` adds or updates a deployment and `gapp ci rename` renames
one, both through the fleet repo's register run, never gcloud, never the
working directory. The run executes the same fleet code the command ran
before dispatching, so a refusal is the same on both sides. The two
one-time steps come back as the verbatim messages the operator reads.
"""

import pytest

from gapp.admin.sdk import ci as ci_mod
from gapp.admin.sdk import fleet

FLEET = "acme/fleet"
WIF = "projects/123456789012/locations/global/workloadIdentityPools/github/providers/github"
SA = "@".join(["gapp-deploy", "my-proj.iam.gserviceaccount.com"])

PROJECT_NOT_TRUSTED = (
    "Fleet repo acme/fleet is not yet trusted to deploy to GCP project my-proj. One-time setup:\n"
    "\n"
    "1. Run `gapp ci project setup my-proj`\n"
    "   - Requires gcloud signed in as an Owner of my-proj\n"
    "   - Requires `gh` signed in as an admin of acme/fleet\n"
    "2. Run this command again: gapp ci setup my-app --repo me/my-app --project my-proj"
)
SOURCE_NOT_ALLOWED = (
    "Apps owned by me aren't allowed in your fleet yet (fleet repo acme/fleet). One-time setup:\n"
    "\n"
    "1. Run `gapp ci sources add me`\n"
    "   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo acme/fleet --body <current-list>,me\n"
    "   - Requires `gh` signed in with write access to acme/fleet\n"
    "2. Run this command again: gapp ci setup my-app --repo me/my-app --project my-proj"
)


def _no_gcloud(*args, **kwargs):
    raise AssertionError("gcloud must never be called by ci setup")


@pytest.fixture
def wired(monkeypatch, fleet_repo, fake_gh):
    """The fleet repo set and installed, the project trusted, the owner allowed, the repo public;
    every path to gcloud explodes."""
    for step in ("_ensure_wif_pool", "_ensure_wif_provider", "_ensure_deploy_sa",
                 "_ensure_wif_binding", "_get_project_number", "run_gcloud", "require_gcloud_account"):
        monkeypatch.setattr(ci_mod, step, _no_gcloud)
    monkeypatch.setenv("PATH", "/nonexistent")
    fake_gh.private["me/my-app"] = False
    fake_gh.variables[(FLEET, "GAPP_ALLOWED_SOURCES")] = "me"
    fake_gh.environments[(FLEET, "my-proj")] = {"GAPP_WIF_PROVIDER": WIF, "GAPP_DEPLOY_SERVICE_ACCOUNT": SA}
    return fake_gh


def _setup(**kw):
    kw.setdefault("repo", "me/my-app")
    kw.setdefault("project", "my-proj")
    return ci_mod.setup_deployment(kw.pop("name", None), **kw)


# -- add and update --


def test_setup_adds_a_deployment_through_the_run_without_gcloud_or_api_writes(wired):
    """The cloud-session case: no gcloud, API writes refused, Actions API unreadable."""
    wired.api_writes = False
    wired.actions_api = False
    res = _setup(domain="mcp.example.com")
    assert res["ok"] is True and res["action"] == "added"
    assert res["name"] == "my-app" and res["id"] == "my-app" and res["project"] == "my-proj"
    assert res["dispatched"] is True and res["conclusion"] == "success" and res["result_code"] == "added"
    assert wired.dispatched == [(FLEET, "gapp-register.yml", {
        "operation": "set", "name": "my-app", "repo": "me/my-app", "project": "my-proj",
        "changes": '{"domain": "mcp.example.com"}', "source": "",
    })]
    assert res["run_url"].endswith("/actions/runs/100") and res["next_step"] is None
    assert wired.deployments(FLEET) == {"my-app": {"id": "my-app", "repo": "me/my-app", "project": "my-proj",
                                                   "domain": "mcp.example.com"}}


def test_setup_defaults_the_name_to_the_repo_name_derived_only_from_the_repo(wired):
    res = _setup(repo="other-org/their-service")
    assert res["name"] == "their-service"
    assert res["ok"] is False and res["next_step"]["action"] == "ci sources add"   # other-org is not allowed yet


def test_setup_with_a_name_uses_it(wired):
    res = _setup(name="prod-api")
    assert res["ok"] is True and res["name"] == "prod-api" and res["id"] == "prod-api"
    assert "prod-api" in wired.deployments(FLEET)


def test_setup_again_with_the_same_values_changes_nothing_and_dispatches_nothing(wired):
    _setup()
    dispatched = len(wired.dispatched)
    res = _setup()
    assert res["ok"] is True and res["action"] == "unchanged" and res["dispatched"] is False
    assert len(wired.dispatched) == dispatched


def test_setup_again_updates_keys_keeps_the_domain_and_an_empty_domain_removes_it(wired):
    _setup(domain="mcp.example.com")
    res = _setup(config={"env": [{"name": "LOG_LEVEL", "value": "DEBUG"}]})
    assert res["action"] == "updated" and res["domain"] == "mcp.example.com"
    entry = wired.deployments(FLEET)["my-app"]
    assert entry["domain"] == "mcp.example.com" and entry["env"] == [{"name": "LOG_LEVEL", "value": "DEBUG"}]
    res = _setup(domain="")
    assert res["action"] == "updated" and res["domain"] is None
    assert "domain" not in wired.deployments(FLEET)["my-app"]


def test_setup_refuses_a_repo_already_deployed_under_another_name_before_dispatching(wired):
    _setup()
    res = _setup(name="my-app-staging", project="my-proj")
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "update or copy"
    assert res["next_step"]["hint"].startswith("me/my-app can't be set up as my-app-staging: me/my-app is already deployed as my-app.")
    assert "Update it: gapp ci setup my-app --repo me/my-app --project my-proj" in res["next_step"]["hint"]
    assert "Add another: gapp ci setup my-app-staging --copy-of my-app" in res["next_step"]["hint"]


def test_setup_refuses_a_name_taken_by_another_repo(wired):
    _setup()
    wired.private["me/other"] = False
    res = _setup(name="my-app", repo="me/other")
    assert res["ok"] is False and res["next_step"]["action"] == "pick another name"
    assert "'my-app' already deploys me/my-app, not me/other" in res["next_step"]["hint"]


def test_setup_refuses_a_name_whose_id_would_exceed_27_characters(wired):
    res = _setup(name="a" * 28)
    assert res["ok"] is False and res["next_step"]["action"] == "pick a shorter name"
    assert "27 characters" in res["next_step"]["hint"] and wired.dispatched == []


# -- the one-time steps, verbatim --


def test_setup_on_an_untrusted_project_prints_the_message_verbatim_without_dispatching(wired):
    del wired.environments[(FLEET, "my-proj")]
    res = _setup()
    assert res["ok"] is False and res["dispatched"] is False
    assert res["next_step"]["action"] == "ci project setup"
    assert res["next_step"]["hint"] == PROJECT_NOT_TRUSTED


def test_setup_when_the_owner_is_not_allowed_prints_the_message_verbatim(wired):
    del wired.variables[(FLEET, "GAPP_ALLOWED_SOURCES")]
    res = _setup()
    assert res["ok"] is False and res["result_code"] == "source-owner-not-allowed"
    assert res["next_step"]["action"] == "ci sources add"
    assert res["next_step"]["hint"] == SOURCE_NOT_ALLOWED
    assert "<current-list>" in res["next_step"]["hint"]              # rendered literally, by design
    assert wired.deployments(FLEET) == {}


def test_setup_makes_no_variables_read_before_dispatching(wired):
    """The allowlist is the register run's to check; this machine makes no extra read."""
    _setup()
    machine_reads = [c for c, _ in wired.calls if c[:2] == ["gh", "api"] and "/actions/variables" in c[2]]
    assert machine_reads == []


def test_setup_repeats_the_users_exact_command_line(wired):
    del wired.environments[(FLEET, "my-proj")]
    res = _setup(command="gapp ci setup --repo me/my-app --project my-proj")
    assert res["next_step"]["hint"].endswith("2. Run this command again: gapp ci setup --repo me/my-app --project my-proj")


# -- the run's other refusals --


def test_setup_prints_other_register_refusals_verbatim_with_the_run_link(wired):
    wired.private.pop("me/my-app")                                   # not readable by the run's token, no hosting app
    res = _setup()
    assert res["ok"] is False and res["result_code"] == "repo-unreachable"
    assert res["next_step"]["action"] == "fix and run again"
    assert "me/my-app is not readable with this run's token" in res["next_step"]["hint"]
    assert res["run_url"] in res["next_step"]["hint"]


def test_setup_accepts_a_private_repo_when_the_hosting_app_is_present(wired):
    wired.private["me/my-app"] = True
    wired.variables[(FLEET, "HOSTING_GITHUB_APP_ID")] = "1"
    wired.secrets.add((FLEET, "HOSTING_GITHUB_APP_PRIVATE_KEY"))
    assert _setup()["ok"] is True


def test_setup_reports_a_refused_dispatch_in_githubs_words_without_a_wrong_login_hint(wired):
    wired.dispatch_refusal = "gh: HTTP 403: Resource not accessible by integration (https://api.github.com/x)"
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "dispatch the register workflow"
    assert "GitHub returned 403: Resource not accessible by integration" in res["next_step"]["hint"]
    assert "gh auth login" not in res["next_step"]["hint"]                 # it reached GitHub; signing in won't help
    assert "GitHub refused the request itself" in res["next_step"]["hint"]


def test_setup_suggests_signing_in_only_when_the_login_is_the_problem(wired):
    wired.dispatch_refusal = "gh: HTTP 401: Bad credentials (https://api.github.com/x)"
    res = _setup()
    assert "Sign in with `gh auth login`" in res["next_step"]["hint"]


def test_setup_dispatches_through_rest_on_the_default_branch(wired):
    """The cloud-session failure: `gh workflow run` resolves the default branch through GraphQL."""
    wired.default_branch = "trunk"
    res = _setup()
    assert res["ok"] is True
    dispatch = next(k for c, k in wired.calls if "-X" in c and c[c.index("-X") + 2].endswith("/dispatches"))
    import json as _json
    assert _json.loads(dispatch["input"])["ref"] == "trunk"
    assert all(c[:2] == ["gh", "api"] and "graphql" not in c for c, _ in wired.calls)


def test_setup_reports_an_unreadable_default_branch(wired):
    wired.fail_with["repos/acme/fleet"] = "gh: HTTP 403: blocked"
    res = _setup()
    assert res["ok"] is False


def test_setup_reports_a_run_that_did_not_finish_with_its_link(wired, monkeypatch):
    monkeypatch.setattr(ci_mod, "_wait_for_run", lambda fleet_repo, run_id, timeout=900: ("in_progress", None))
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "check the run"
    assert res["run_url"] in res["next_step"]["hint"]


def test_setup_without_watch_returns_after_dispatch(wired):
    res = _setup(watch=False)
    assert res["ok"] is True and res["dispatched"] is True and res["action"] is None and res["run_id"] == "100"


def test_setup_requires_repo_and_project(wired):
    res = ci_mod.setup_deployment("my-app", project="my-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "pass --repo"
    res = ci_mod.setup_deployment("my-app", repo="me/my-app")
    assert res["ok"] is False and res["next_step"]["action"] == "pass --project"
    assert "never looks a project up in GCP" in res["next_step"]["hint"]


def test_setup_without_the_register_workflow_or_the_deployments_file_points_at_ci_init(wired):
    del wired.files[(FLEET, ci_mod.REGISTER_WORKFLOW_PATH)]
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "ci init" and f"gapp ci init {FLEET}" in res["next_step"]["hint"]
    del wired.files[(FLEET, fleet.DEPLOYMENTS_FILE)]
    res = _setup()
    assert res["ok"] is False and res["next_step"]["action"] == "ci init"
    assert f"{FLEET} has no {fleet.DEPLOYMENTS_FILE}" in res["next_step"]["hint"]


# -- copy-of --


def test_copy_of_makes_an_independent_deployment_and_echoes_env_and_secrets(wired):
    _setup(domain="mcp.example.com", config={"env": [
        {"name": "LOG_LEVEL", "value": "INFO"}, {"name": "API_KEY", "secret": {"name": "api-key"}}]})
    wired.environments[(FLEET, "staging-proj")] = {}
    res = ci_mod.setup_deployment("my-app-staging", copy_of="my-app", project="staging-proj")
    assert res["ok"] is True and res["action"] == "added"
    assert res["repo"] == "me/my-app" and res["project"] == "staging-proj" and res["id"] == "my-app-staging"
    assert res["domain"] is None and res["project_from_source"] is False
    assert res["copied_env"] == ["LOG_LEVEL", "API_KEY"] and res["secrets"] == ["api-key"]
    assert wired.dispatched[-1][2]["operation"] == "copy" and wired.dispatched[-1][2]["source"] == "my-app"
    docs = wired.deployments(FLEET)
    assert docs["my-app-staging"]["env"] == docs["my-app"]["env"] and "domain" not in docs["my-app-staging"]
    assert docs["my-app"]["domain"] == "mcp.example.com"


def test_copy_of_defaults_to_the_source_project_and_gets_its_own_id(wired):
    _setup()
    res = ci_mod.setup_deployment("my-app-2", copy_of="my-app")
    assert res["ok"] is True and res["project"] == "my-proj" and res["project_from_source"] is True
    assert res["id"] == "my-app-2"
    docs = wired.deployments(FLEET)
    assert docs["my-app"]["id"] == "my-app" and docs["my-app-2"]["id"] == "my-app-2"


def test_copy_of_needs_a_name_and_an_existing_source(wired):
    res = ci_mod.setup_deployment(None, copy_of="my-app")
    assert res["ok"] is False and res["next_step"]["action"] == "pass the new name"
    res = ci_mod.setup_deployment("new", copy_of="nope")
    assert res["ok"] is False and res["next_step"]["action"] == "name an existing deployment"


def test_copy_of_to_an_untrusted_project_prints_the_one_time_message(wired):
    _setup()
    res = ci_mod.setup_deployment("my-app-staging", copy_of="my-app", project="staging-proj")
    assert res["ok"] is False and res["next_step"]["action"] == "ci project setup"
    assert "not yet trusted to deploy to GCP project staging-proj" in res["next_step"]["hint"]
    assert res["next_step"]["hint"].endswith("gapp ci setup my-app-staging --copy-of my-app --project staging-proj")


# -- rename --


def test_rename_changes_the_name_only_and_keeps_the_id(wired):
    _setup(domain="mcp.example.com")
    res = ci_mod.rename_deployment("my-app", "renamed")
    assert res["ok"] is True and res["action"] == "renamed" and res["id"] == "my-app"
    docs = wired.deployments(FLEET)
    assert list(docs) == ["renamed"] and docs["renamed"]["id"] == "my-app" and docs["renamed"]["domain"] == "mcp.example.com"


def test_rename_refuses_a_missing_deployment_or_a_taken_name(wired):
    _setup()
    res = ci_mod.rename_deployment("gone", "x")
    assert res["ok"] is False and res["dispatched"] is False and "gapp ci list" in res["next_step"]["hint"]
    _setup(name="other", repo="me/other") if wired.private.setdefault("me/other", False) is False else None
    res = ci_mod.rename_deployment("my-app", "other")
    assert res["ok"] is False and res["next_step"]["action"] == "pick another name"


def test_setup_has_no_working_directory_parameters():
    import inspect
    params = inspect.signature(ci_mod.setup_deployment).parameters
    assert set(params) == {"name", "repo", "project", "domain", "config", "copy_of", "watch", "command"}
    assert "solution" not in params and "solution_repo" not in params
