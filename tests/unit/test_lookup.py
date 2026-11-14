"""Which deployment a command means: named outright, or the deployment of
the clone's repo. The remote finds candidates and the fleet decides; a
folder's name only ever proposes a name for a new deployment."""

import subprocess

import pytest

from gapp.admin.sdk import lookup
from gapp.admin.sdk.lookup import LookupRefusal, Remote, find, parse_remote, pick_remote

APP = Remote("github.com", "me/my-app")
FLEET = {
    "my-app": {"id": "my-app", "repo": "me/my-app", "project": "prod-proj"},
    "notes": {"id": "notes", "repo": "me/notes", "project": "prod-proj"},
}


@pytest.mark.parametrize("url", [
    "https://github.com/me/my-app.git", "https://github.com/me/my-app", "https://github.com/me/my-app/",
    "git@github.com:me/my-app.git", "ssh://git@github.com/me/my-app.git", "ssh://git@github.com:22/me/my-app",
    "https://" + "@".join(["token", "github.com"]) + "/me/my-app.git", "git://GitHub.com/me/my-app.git",
])
def test_every_spelling_of_a_github_remote_is_the_same_repo(url):
    assert parse_remote(url) == APP and parse_remote(url).repo == "me/my-app"


def test_another_host_keeps_its_host_and_its_whole_path():
    remote = parse_remote("@".join(["git", "gitlab.com"]) + ":group/sub/tool.git")
    assert remote == Remote("gitlab.com", "group/sub/tool")
    assert remote.repo == "gitlab.com/group/sub/tool" and remote.name == "tool"


@pytest.mark.parametrize("url", ["", "not a url", "/local/path/repo.git", "https://github.com/onlyone"])
def test_what_is_not_a_hosted_repo_is_no_remote(url):
    assert parse_remote(url) is None


def test_origin_identifies_the_clone_whatever_else_is_there():
    assert pick_remote({"upstream": Remote("github.com", "them/my-app"), "origin": APP}) == APP


def test_the_only_remote_identifies_the_clone_when_it_is_not_called_origin():
    assert pick_remote({"github": APP}) == APP


def test_several_remotes_and_no_origin_is_refused_with_them_listed():
    with pytest.raises(LookupRefusal) as exc:
        pick_remote({"a": APP, "b": Remote("github.com", "them/my-app")})
    assert "a (me/my-app), b (them/my-app)" in str(exc.value) and "does not choose" in str(exc.value)


def test_a_name_given_outright_is_used_from_anywhere():
    found = find(FLEET, name="notes", remotes={"origin": APP})
    assert found.name == "notes" and found.how == "named" and found.entry["repo"] == "me/notes"


def test_a_name_that_is_not_a_deployment_lists_the_fleet():
    with pytest.raises(LookupRefusal) as exc:
        find(FLEET, name="nope")
    assert str(exc.value) == "'nope' is not a deployment in your fleet. It holds: my-app, notes."


def test_in_a_clone_the_one_deployment_of_its_repo_is_used():
    found = find(FLEET, remotes={"origin": APP})
    assert found.name == "my-app" and found.how == "remote" and found.repo == "me/my-app"


def test_the_match_is_on_the_repo_not_on_its_name():
    """A fork has another owner: it is not the deployment, and the name it would take is refused."""
    with pytest.raises(LookupRefusal) as exc:
        find(FLEET, remotes={"origin": Remote("github.com", "them/my-app")})
    assert str(exc.value) == ("them/my-app has no deployment in your fleet, and the name 'my-app' belongs to "
                              "me/my-app (on prod-proj). Give this repo's deployment another name.")


def test_the_same_repo_on_another_host_is_another_repo():
    with pytest.raises(LookupRefusal, match="gitlab.com/me/my-app has no deployment"):
        find(FLEET, remotes={"origin": Remote("gitlab.com", "me/my-app")})


def test_several_deployments_of_one_repo_are_refused_with_the_command_for_each():
    doc = {**FLEET, "my-app-test": {"id": "my-app-test", "repo": "ME/My-App", "project": "test-proj"}}
    with pytest.raises(LookupRefusal) as exc:
        find(doc, remotes={"origin": APP}, command="gapp secrets list --deployment")
    assert str(exc.value) == (
        "me/my-app has 2 deployments. Say which:\n"
        "  gapp secrets list --deployment my-app    (me/my-app on prod-proj)\n"
        "  gapp secrets list --deployment my-app-test    (ME/My-App on test-proj)")


def test_a_project_narrows_several_to_the_one_on_it():
    doc = {**FLEET, "my-app-test": {"id": "my-app", "repo": "me/my-app", "project": "test-proj"}}
    assert find(doc, remotes={"origin": APP}, project="test-proj").name == "my-app-test"


def test_a_project_the_repo_is_not_on_is_refused_and_never_treated_as_new():
    with pytest.raises(LookupRefusal) as exc:
        find(FLEET, remotes={"origin": APP}, project="elsewhere")
    assert str(exc.value).startswith("me/my-app has no deployment on elsewhere. Its deployments:\n  gapp deploy my-app")


def test_a_repo_with_no_deployment_is_new_and_takes_its_repo_name():
    found = find(FLEET, remotes={"origin": Remote("github.com", "me/fresh")})
    assert found.is_new and found.proposed == "fresh" and found.repo == "me/fresh"


def test_a_clone_with_no_remote_is_matched_by_gapp_yaml_name_only_to_a_definition_with_no_repo():
    doc = {**FLEET, "scratch": {"id": "scratch", "project": "prod-proj"}}
    found = find(doc, manifest_name="scratch", folder_name="whatever")
    assert found.name == "scratch" and found.how == "gapp.yaml"


def test_a_name_alone_never_reaches_a_deployment_tied_to_a_repo():
    with pytest.raises(LookupRefusal) as exc:
        find(FLEET, manifest_name="my-app", folder_name="my-app")
    assert "which is me/my-app's deployment" in str(exc.value)


def test_a_folders_name_never_selects_an_existing_deployment():
    doc = {**FLEET, "scratch": {"id": "scratch", "project": "prod-proj"}}
    with pytest.raises(LookupRefusal) as exc:
        find(doc, folder_name="scratch")
    assert "cannot tell which deployment it is" in str(exc.value) and "Deployments with no repo: scratch." in str(exc.value)
    with pytest.raises(LookupRefusal):
        find(doc, folder_name="my-app")


def test_with_no_remote_and_nothing_to_confuse_it_with_the_folder_only_proposes_a_name():
    found = find(FLEET, folder_name="experiment")
    assert found.is_new and found.proposed == "experiment" and found.entry is None
    assert find(FLEET, manifest_name="brand-new", folder_name="experiment").proposed == "brand-new"


def test_clone_remotes_reads_the_fetch_urls_of_a_real_clone(tmp_path):
    for args in (["init", "-q"], ["remote", "add", "origin", "git@github.com:me/my-app.git"],
                 ["remote", "add", "upstream", "https://gitlab.com/group/sub/tool.git"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    assert lookup.clone_remotes(tmp_path) == {"origin": APP, "upstream": Remote("gitlab.com", "group/sub/tool")}


def test_a_folder_that_is_not_a_clone_has_no_remotes(tmp_path):
    assert lookup.clone_remotes(tmp_path) == {}
