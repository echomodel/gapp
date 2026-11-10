"""The `gapp ci` CLI: output leads with the deployment and what happened,
then "Next: <exact command>"; the fleet repo, files, variables and id only
with --verbose or when they are the problem; every outcome is its plain
sentence and exit 1. Each reference output from the design has a test.
"""

from click.testing import CliRunner

from gapp.admin.cli.main import main as cli_main
from gapp.admin.sdk import ci as ci_mod

FLEET = "acme/fleet"


def _run(args):
    return CliRunner().invoke(cli_main, args)


def _init_result(**over):
    base = {"ok": True, "fleet_repo": FLEET, "set_by": "gapp ci init acme/fleet", "found": [], "created": False,
            "files": {".github/workflows/gapp-deploy.yml": "created", ".github/workflows/gapp-register.yml": "created",
                      "gapp/deployments.yaml": "created"},
            "legacy_solutions": None, "variables": {"GAPP_ALLOWED_SOURCES": False, "HOSTING_GITHUB_APP_ID": False,
                                                    "HOSTING_GITHUB_APP_PRIVATE_KEY": False},
            "variables_note": None, "topic": "set",
            "next_step": {"action": "ci sources add", "hint": "gapp ci sources add <owner>"}}
    base.update(over)
    if "lines" not in over and base.get("files"):
        base["lines"] = ci_mod._init_lines(base)
    return base


def test_init_leads_with_what_gapp_can_do_then_next_then_the_fleet_in_brackets(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result())
    result = _run(["ci", "init", FLEET])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "gapp can now deploy your apps through GitHub Actions.",
        f"Installed gapp's workflows and gapp/deployments.yaml in {FLEET}.",
        f"(Your fleet is kept in {FLEET}.)",
        "Next: gapp ci sources add <owner>",
    ]
    assert "✓" not in result.output and "created" not in result.output.replace("Installed", "")


def test_init_rerun_on_an_existing_fleet_with_nothing_to_do_says_so(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result(
        files={k: "unchanged" for k in _init_result()["files"]},
        variables={"GAPP_ALLOWED_SOURCES": True, "HOSTING_GITHUB_APP_ID": False, "HOSTING_GITHUB_APP_PRIVATE_KEY": False},
        next_step={"action": "ci list", "hint": "gapp ci list"}))
    assert _run(["ci", "init", FLEET]).output.splitlines() == [
        "Nothing changed.",
        f"(Your fleet is kept in {FLEET}.)",
        "Next: gapp ci list",
    ]


def test_init_upgrade_prints_what_changed_and_ci_list_next(monkeypatch):
    files = {k: "updated" for k in _init_result()["files"]}
    files["gapp/deployments.yaml"] = "unchanged"
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result(
        files=files, next_step={"action": "ci list", "hint": "gapp ci list"}))
    assert _run(["ci", "init", FLEET]).output.splitlines() == [
        f"Updated gapp's workflows in {FLEET} to this release.",
        f"(Your fleet is kept in {FLEET}.)",
        "Next: gapp ci list",
    ]


def test_init_without_args_shows_the_saved_repo_and_how_it_was_set(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result(
        files=None, variables=None, topic=None, next_step=None,
        lines=[f"Your fleet is kept in {FLEET} (set by: gapp ci init acme/fleet)."]))
    out = _run(["ci", "init"]).output
    assert out.strip() == f"Your fleet is kept in {FLEET} (set by: gapp ci init acme/fleet)."


def test_init_create_and_the_two_verbatim_refusals(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result(created=True))
    out = _run(["ci", "init", FLEET, "--create"]).output.splitlines()
    assert out[0] == f"Created your private fleet repo {FLEET} with gapp's workflows and an empty gapp/deployments.yaml."
    for message in (ci_mod.REPO_MISSING_MESSAGE.format(repo=FLEET), ci_mod.REPO_EXISTS_MESSAGE.format(repo=FLEET)):
        monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False, m=message: {
            "ok": False, "fleet_repo": None, "next_step": {"action": "x", "hint": m}})
        result = _run(["ci", "init", FLEET])
        assert result.exit_code == 1 and result.output.strip() == message


def test_verbose_adds_the_fleet_detail_and_default_output_hides_it(monkeypatch):
    monkeypatch.setattr(ci_mod, "init_fleet", lambda repo=None, owner=None, create=False: _init_result())
    plain = _run(["ci", "init", FLEET]).output
    verbose = _run(["ci", "init", FLEET, "--verbose"]).output
    assert "Files:" not in plain and "Variables:" not in plain and "Topic" not in plain
    assert f"Fleet repo: {FLEET}" in verbose
    assert "Files: gapp-deploy.yml created, gapp-register.yml created, deployments.yaml created" in verbose
    assert "Variables: GAPP_ALLOWED_SOURCES missing, HOSTING_GITHUB_APP_ID missing, HOSTING_GITHUB_APP_PRIVATE_KEY missing" in verbose
    assert "Topic gapp-ci: set" in verbose


def _setup_result(**over):
    base = {"ok": True, "name": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": None, "id": "my-app",
            "fleet_repo": FLEET, "copy_of": None, "action": "added", "changes": {},
            "project_from_source": False, "workflow": "gapp-register.yml", "dispatched": True, "run_id": "42",
            "run_url": f"https://github.com/{FLEET}/actions/runs/42", "conclusion": "success", "result_code": "added",
            "messages": [], "next_step": None}
    base.update(over)
    return base


def test_setup_output_leads_with_the_repo_project_and_name_then_the_deploy_command(monkeypatch):
    calls = {}

    def fake(name=None, **kw):
        calls["name"] = name
        calls.update(kw)
        return _setup_result()

    monkeypatch.setattr(ci_mod, "_setup_outcome", fake)
    monkeypatch.setattr("sys.argv", ["gapp", "ci", "setup", "--repo", "me/my-app", "--project", "my-proj"])
    result = _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ["me/my-app is set up to deploy to my-proj as my-app.", "Deploy it: gapp ci deploy my-app"]
    assert calls["name"] is None and calls["repo"] == "me/my-app" and calls["project"] == "my-proj"
    assert calls["command"] == "gapp ci setup --repo me/my-app --project my-proj" and calls["watch"] is True


def test_setup_id_shows_only_with_verbose(monkeypatch):
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(id="my-app-2"))
    assert "my-app-2" not in _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"]).output
    assert "Id: my-app-2" in _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj", "--verbose"]).output


def test_setup_blocked_by_the_owner_leads_with_the_repo_then_the_verbatim_message(monkeypatch):
    message = ci_mod.SOURCE_NOT_ALLOWED_MESSAGE.format(owner="me", fleet_repo=FLEET, command="gapp ci setup --repo me/my-app --project my-proj")
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(
        dispatched=True, conclusion="failure", result_code="source-owner-not-allowed", action=None,
    ) | ci_mod._outcome("ci sources add", message))
    result = _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"])
    assert result.exit_code == 1
    assert result.output.startswith("me/my-app can't be set up yet: apps owned by me aren't allowed in your fleet.\n" + message)
    assert "<current-list>" in result.output


def test_setup_blocked_by_trust_leads_with_the_repo_then_the_verbatim_message(monkeypatch):
    message = ci_mod.PROJECT_NOT_TRUSTED_MESSAGE.format(fleet_repo=FLEET, project="my-proj", command="gapp ci setup --repo me/my-app --project my-proj")
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(
        dispatched=False, run_id=None, run_url=None, conclusion=None, result_code=None, action=None,
    ) | ci_mod._outcome("ci project setup", message))
    result = _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"])
    assert result.exit_code == 1
    assert result.output.startswith("me/my-app can't be set up yet: your fleet isn't trusted to deploy to my-proj.\n" + message)


def test_setup_refusals_print_their_sentence(monkeypatch):
    hint = ("me/my-app can't be set up as my-app-staging: me/my-app is already deployed as my-app. "
            "Update it: gapp ci setup my-app --repo me/my-app --project my-proj ... Add another: gapp ci setup my-app-staging --copy-of my-app ...")
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(dispatched=False) | ci_mod._outcome("update or copy", hint))
    result = _run(["ci", "setup", "my-app-staging", "--repo", "me/my-app", "--project", "my-proj"])
    assert result.exit_code == 1 and result.output.strip() == hint


def test_setup_copy_of_reports_no_env_or_secret_lists(monkeypatch):
    """Lists built from the entry alone would miss the app's own gapp.yaml, so a copy reports none."""
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(
        name="my-app-staging", project="staging-proj", copy_of="my-app"))
    out = _run(["ci", "setup", "my-app-staging", "--copy-of", "my-app", "--project", "staging-proj"]).output.splitlines()
    assert out[0] == "my-app-staging is set up as a copy of my-app: me/my-app deploys to staging-proj."
    assert not [l for l in out if l.startswith(("Env copied", "Secrets "))]
    assert ("Set the secrets the app's gapp.yaml declares on staging-proj before the first deploy: "
            "gapp secrets list shows them from the app's folder.") in out
    assert out[-1] == "Deploy it: gapp ci deploy my-app-staging"


def test_setup_copy_to_the_same_project_states_it(monkeypatch):
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(
        name="my-app-2", copy_of="my-app", project_from_source=True))
    assert "deploys to my-proj (the same project as the source)." in _run(["ci", "setup", "my-app-2", "--copy-of", "my-app"]).output


def test_setup_unchanged_and_no_watch(monkeypatch):
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(action="unchanged", dispatched=False))
    out = _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj"]).output.splitlines()
    assert out == ["my-app is already set up to deploy me/my-app to my-proj. Nothing changed.", "Deploy it: gapp ci deploy my-app"]
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(action=None, conclusion=None, result_code=None))
    out = _run(["ci", "setup", "--repo", "me/my-app", "--project", "my-proj", "--no-watch"]).output.splitlines()
    assert out == [f"Setting up my-app (me/my-app to my-proj): https://github.com/{FLEET}/actions/runs/42", "Next: gapp ci status my-app"]


def test_setup_without_repo_prints_the_outcome(monkeypatch):
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(dispatched=False) | ci_mod._outcome(
        "pass --repo", "Name the repo to deploy: `gapp ci setup [<name>] --repo <owner>/<repo> --project <project>`."))
    result = _run(["ci", "setup", "my-app"])
    assert result.exit_code == 1 and result.output.startswith("Name the repo to deploy")
    assert "--solution" not in _run(["ci", "setup", "--help"]).output


def test_rename_output(monkeypatch):
    monkeypatch.setattr(ci_mod, "rename_deployment", lambda old, new, watch=True: {
        "ok": True, "name": new, "old_name": old, "id": "my-app", "fleet_repo": FLEET, "action": "renamed",
        "dispatched": True, "run_url": "u", "next_step": None})
    out = _run(["ci", "rename", "my-app", "renamed"]).output.splitlines()
    assert out == ["my-app is now renamed. Its id and GCP resources are unchanged.", "Next: gapp ci deploy renamed"]


def test_project_setup_output(monkeypatch):
    base = {"ok": True, "project_id": "my-proj", "fleet_repo": FLEET, "wif_pool": "created", "wif_provider": "created",
            "service_account": "exists", "binding": "set", "environment": "my-proj", "environment_record": "created",
            "changed": True, "next_step": None}
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: base)
    out = _run(["ci", "project", "setup", "my-proj"]).output.splitlines()
    assert out == ["my-proj now trusts your fleet to deploy to it.", "Next: gapp ci setup <name> --repo <owner>/<repo> --project my-proj"]
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: {**base, "changed": False, "environment_record": "unchanged"})
    assert _run(["ci", "project", "setup", "my-proj"]).output.splitlines()[0] == "my-proj already trusts your fleet to deploy to it. Nothing changed."
    assert "WIF pool: created" in _run(["ci", "project", "setup", "my-proj", "--verbose"]).output
    lag = ("It can take a few minutes before gapp sees the new mark when searching for deployments "
           "(`gapp list`, and commands run without `--project`).")
    trusted = {**base, "wif_pool": "exists", "wif_provider": "exists", "environment_record": "unchanged"}
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: {**trusted, "hosted": "set", "changed": True})
    out = _run(["ci", "project", "setup", "my-proj"]).output.splitlines()
    assert out[:2] == ["my-proj already trusts your fleet to deploy to it; marked it gapp=hosted, so `gapp list` "
                       "and `gapp status <name>` can find its deployments.", lag]
    assert "Hosted mark (gapp=hosted): set" in _run(["ci", "project", "setup", "my-proj", "--verbose"]).output
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: {**trusted, "hosted": "exists", "changed": False})
    out = _run(["ci", "project", "setup", "my-proj"]).output.splitlines()
    assert out[0] == "my-proj already trusts your fleet to deploy to it. Nothing changed." and lag not in out
    monkeypatch.setattr(ci_mod, "setup_ci_project", lambda project: {**base, "hosted": "set"})
    out = _run(["ci", "project", "setup", "my-proj"]).output.splitlines()
    assert out[:2] == ["my-proj now trusts your fleet to deploy to it.", lag]
    help_text = _run(["ci", "project", "setup", "--help"]).output
    assert "Owner" in help_text and "admin of the fleet repo" in help_text


def test_sources_output(monkeypatch):
    monkeypatch.setattr(ci_mod, "manage_sources", lambda action, owner=None: {
        "ok": True, "action": action, "fleet_repo": FLEET, "owner": owner, "owners": ["me", "other-org"],
        "changed": action != "list", "next_step": None})
    assert _run(["ci", "sources", "add", "me"]).output.splitlines() == [
        "Apps owned by me are now allowed in your fleet.", "Next: gapp ci project setup <project>"]
    assert _run(["ci", "sources", "list"]).output.strip() == "Allowed owners: me, other-org"
    assert _run(["ci", "sources", "remove", "other-org"]).output.strip() == "Apps owned by other-org are no longer allowed in your fleet."
    monkeypatch.setattr(ci_mod, "manage_sources", lambda action, owner=None: {
        "ok": False, "action": action, "fleet_repo": FLEET, "owner": owner, "owners": None, "changed": False,
        "next_step": {"action": "sign in to gh with write access", "hint": "GAPP_ALLOWED_SOURCES in acme/fleet could not be written: GitHub returned 403: blocked."}})
    result = _run(["ci", "sources", "add", "me"])
    assert result.exit_code == 1 and result.output.strip().startswith("GAPP_ALLOWED_SOURCES in acme/fleet could not be written")


def test_deploy_output(monkeypatch):
    def fake(name, ref="main", watch=True):
        return {"ok": True, "name": name, "repo": "me/my-app", "project": "my-proj", "fleet_repo": FLEET,
                "workflow": "gapp-deploy.yml", "ref": ref, "run_id": "42", "run_url": f"https://github.com/{FLEET}/actions/runs/42",
                "watched": watch, "conclusion": "success" if watch else None, "next_step": None}
    monkeypatch.setattr(ci_mod, "deploy_deployment", fake)
    out = _run(["ci", "deploy", "my-app", "--ref", "v1.2.3", "--no-watch"]).output.splitlines()
    assert out == ["Deploying my-app (ref v1.2.3).", f"my-app deploy started (ref v1.2.3): https://github.com/{FLEET}/actions/runs/42",
                   "Next: gapp ci status my-app"]
    out = _run(["ci", "deploy", "my-app"]).output.splitlines()
    assert out[1] == f"my-app deployed (success): https://github.com/{FLEET}/actions/runs/42"
    monkeypatch.setattr(ci_mod, "deploy_deployment", lambda name, ref="main", watch=True: {
        "ok": False, "name": name, "fleet_repo": FLEET, "next_step": {"action": "ci setup", "hint": "'x' is not a deployment in acme/fleet, so there is nothing to deploy. Set it up: gapp ci setup x --repo <owner>/<repo> --project <project>. See the deployments: gapp ci list"}})
    result = _run(["ci", "deploy", "x"])
    assert result.exit_code == 1 and "'x' is not a deployment in acme/fleet" in result.output


def test_status_output(monkeypatch):
    base = {"ok": True, "name": "my-app", "fleet_repo": FLEET, "registered": True, "id": "my-app-2", "repo": "me/my-app",
            "project": "my-proj", "domain": "mcp.example.com", "overlay": {"domain": "mcp.example.com"},
            "last_deploy": {"checked": True, "note": None, "run": {"id": "42", "status": "completed", "conclusion": "success",
                            "created_at": "2026-01-01T00:00:00Z", "ref": "main", "url": f"https://github.com/{FLEET}/actions/runs/42"}},
            "problem": None, "live_hint": "For the live service state: gapp ci status my-app --live", "next_step": None}
    monkeypatch.setattr(ci_mod, "status_deployment", lambda name: base)
    out = _run(["ci", "status", "my-app"]).output.splitlines()
    assert out == ["my-app: me/my-app deploys to my-proj, domain mcp.example.com.",
                   f"Last deploy: success, 2026-01-01T00:00:00Z (ref main): https://github.com/{FLEET}/actions/runs/42",
                   "Deploy it: gapp ci deploy my-app",
                   "For the live service state: gapp ci status my-app --live"]
    assert "my-app-2" not in "\n".join(out)
    verbose = _run(["ci", "status", "my-app", "--verbose"]).output
    assert "Id: my-app-2" in verbose and "gapp.yaml keys set by the fleet: domain" in verbose
    monkeypatch.setattr(ci_mod, "status_deployment", lambda name: {**base, "last_deploy": {"checked": True, "run": None, "note": None},
                                                                   "problem": "acme/fleet has no Environment named 'my-proj', so a deploy cannot authenticate to that project.",
                                                                   "next_step": {"action": "ci project setup", "hint": "gapp ci project setup my-proj"}})
    out = _run(["ci", "status", "my-app"]).output.splitlines()
    assert out[1] == "No deploys yet." and out[-3].startswith("acme/fleet has no Environment") and out[-2] == "Next: gapp ci project setup my-proj"
    assert out[-1] == "For the live service state: gapp ci status my-app --live"
    assert "Deploy it" not in "\n".join(out)


def test_status_live_prints_the_root_status_lines_from_the_run(monkeypatch):
    live = {"initialized": True, "name": "my-app", "repo_path": None,
            "deployment": {"project": "my-proj", "pending": False,
                           "services": [{"name": "my-app", "url": "https://my-app-abc-uc.a.run.app", "healthy": True}]},
            "domain": {"name": "mcp.example.com", "status": "active", "cname_target": "ghs.googlehosted.com", "detail": None},
            "next_step": None}
    monkeypatch.setattr(ci_mod, "status_live", lambda name: {
        "ok": True, "name": name, "fleet_repo": FLEET, "id": "my-app", "repo": "me/my-app", "project": "my-proj",
        "workflow": "gapp-status.yml", "dispatched": True, "run_id": "7", "run_url": f"https://github.com/{FLEET}/actions/runs/7",
        "conclusion": "success", "live": live, "messages": [], "next_step": None})
    out = _run(["ci", "status", "my-app", "--live"]).output.splitlines()
    assert out[0] == "Asking the project for the live state of my-app; this takes about as long as a runner start."
    assert out[1] == f"my-app: live state from my-proj (https://github.com/{FLEET}/actions/runs/7)"
    assert "Status:   READY" in out and "  URL:     https://my-app-abc-uc.a.run.app" in out and "Domain:   mcp.example.com (active)" in out
    monkeypatch.setattr(ci_mod, "status_live", lambda name: {"ok": False, "name": name, "fleet_repo": FLEET, "live": None,
        "next_step": {"action": "read the run log", "hint": "The status run for 'my-app' did not report the live state: x (url)."}})
    result = _run(["ci", "status", "my-app", "--live"])
    assert result.exit_code == 1 and result.output.splitlines()[-1].startswith("The status run for 'my-app'")


def test_json_prints_the_sdk_result_exactly_for_every_ci_command(monkeypatch):
    import json
    cases = {
        "init_fleet": (["ci", "init", FLEET, "--json"], {"ok": True, "fleet_repo": FLEET, "files": {"a": "created"}, "next_step": None}),
        "setup_ci_project": (["ci", "project", "setup", "my-proj", "--json"], {"ok": True, "project_id": "my-proj", "changed": True, "next_step": None}),
        "manage_sources": (["ci", "sources", "list", "--json"], {"ok": True, "owners": ["me"], "next_step": None}),
        "setup_deployment": (["ci", "setup", "--repo", "me/my-app", "--project", "my-proj", "--json"], {"ok": True, "name": "my-app", "action": "added", "next_step": None}),
        "rename_deployment": (["ci", "rename", "a", "b", "--json"], {"ok": True, "name": "b", "action": "renamed", "next_step": None}),
        "deploy_deployment": (["ci", "deploy", "my-app", "--json"], {"ok": True, "name": "my-app", "run_url": "u", "watched": True, "conclusion": "success", "next_step": None}),
        "status_deployment": (["ci", "status", "my-app", "--json"], {"ok": True, "name": "my-app", "live_hint": "x", "next_step": None}),
        "status_live": (["ci", "status", "my-app", "--live", "--json"], {"ok": True, "name": "my-app", "live": {"initialized": True}, "next_step": None}),
        "list_deployments": (["ci", "list", "--json"], {"ok": True, "deployments": [], "next_step": {"action": "ci setup", "hint": "gapp ci setup ..."}}),
    }
    for fn, (argv, payload) in cases.items():
        monkeypatch.setattr(ci_mod, fn, lambda *a, _p=payload, **kw: dict(_p))
        result = _run(argv)
        assert result.exit_code == 0, (fn, result.output)
        assert json.loads(result.output) == payload, fn
    monkeypatch.setattr(ci_mod, "list_deployments", lambda *a, **kw: {"ok": False, "next_step": {"action": "ci init", "hint": "No fleet repo"}})
    result = _run(["ci", "list", "--json"])
    assert result.exit_code == 1 and json.loads(result.output) == {"ok": False, "next_step": {"action": "ci init", "hint": "No fleet repo"}}


def test_list_output(monkeypatch):
    monkeypatch.setattr(ci_mod, "list_deployments", lambda repo=None, verbose=False: {
        "ok": True, "fleet_repo": FLEET, "repo": repo, "deployments": [
            {"name": "my-app", "id": "my-app", "repo": "me/my-app", "project": "my-proj", "domain": "mcp.example.com", "keys": ["domain"]},
            {"name": "my-app-staging", "id": "my-app-staging", "repo": "me/my-app", "project": "staging-proj", "domain": None, "keys": []}],
        "files": None, "variables": None, "variables_note": None, "legacy_workflows": ["old-app"] if verbose else [],
        "problem": None, "next_step": None})
    out = _run(["ci", "list"]).output.splitlines()
    assert out[0] == f"2 deployments in {FLEET}:"
    assert out[1] == "  my-app          me/my-app -> my-proj, domain mcp.example.com"
    assert out[2] == "  my-app-staging  me/my-app -> staging-proj"
    assert "old-app" not in "\n".join(out)
    assert "Per-solution workflows from gapp 3.x" in _run(["ci", "list", "--verbose"]).output
    monkeypatch.setattr(ci_mod, "list_deployments", lambda repo=None, verbose=False: {
        "ok": True, "fleet_repo": FLEET, "repo": repo, "deployments": [], "files": None, "variables": None,
        "variables_note": None, "legacy_workflows": [], "problem": None,
        "next_step": {"action": "ci setup", "hint": "gapp ci setup <name> --repo <owner>/<repo> --project <project>"}})
    assert _run(["ci", "list"]).output.splitlines() == [f"No deployments in {FLEET} yet.",
                                                        "Next: gapp ci setup <name> --repo <owner>/<repo> --project <project>"]


def test_ci_commands_take_names_not_working_directory_defaults():
    assert "Defaults to current directory" not in _run(["ci", "--help"]).output
    for cmd in ("setup", "deploy", "status", "list", "rename"):
        help_text = _run(["ci", cmd, "--help"]).output
        assert "--solution" not in help_text and "current directory" not in help_text, cmd
    assert _run(["ci", "deploy"]).exit_code != 0 and _run(["ci", "status"]).exit_code != 0


def test_setup_update_flag_and_its_output(monkeypatch):
    calls = {}

    def fake(name=None, **kw):
        calls.update(kw)
        return _setup_result(action="updated", result_code="updated", domain="new.example.com",
                             changes_shown=["domain: old.example.com → new.example.com", "dropped: colour"])
    monkeypatch.setattr(ci_mod, "_setup_outcome", fake)
    out = _run(["ci", "setup", "my-app", "--repo", "me/my-app", "--project", "my-proj",
                "--domain", "new.example.com", "--update"]).output.splitlines()
    assert calls["update"] is True
    assert out[:3] == ["my-app updated (me/my-app to my-proj):",
                       "  domain: old.example.com → new.example.com",
                       "  dropped: colour"]
    assert "Deploy it: gapp ci deploy my-app" in out
    help_text = " ".join(_run(["ci", "setup", "--help"]).output.split())
    assert ("--update Apply a change to an existing deployment's entry. Without it, setup shows the change "
            "and makes none.") in help_text


def test_setup_without_update_prints_the_change_and_exits_1(monkeypatch):
    hint = ("my-app already deploys me/my-app to my-proj. This would change its entry:\n"
            "  domain: old.example.com → new.example.com\n"
            "Run it again with --update to apply: gapp ci setup my-app --repo me/my-app --project my-proj "
            "--domain new.example.com --update")
    monkeypatch.setattr(ci_mod, "_setup_outcome", lambda name=None, **kw: _setup_result(dispatched=False) | ci_mod._outcome("pass --update", hint))
    result = _run(["ci", "setup", "my-app", "--repo", "me/my-app", "--project", "my-proj", "--domain", "new.example.com"])
    assert result.exit_code == 1 and result.output.strip() == hint
