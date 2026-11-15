from .conftest import gapp


def test_remove_gives_no_commands_that_take_cloud_resources_down(fresh_machine):
    """Commitment: output never reads as a complete recipe when it is not
    one. `gapp remove` changes the fleet only, so it names what is left
    in Google Cloud and prints no teardown commands.

    Why: a list that misses one resource reads as complete, and the
    operator believes the deployment is gone (CONTRIBUTING.md, "Removing a
    deployment").
    """
    gapp("add", "my-app", "--project", "my-project", "--repo", "acme/my-app")
    gapp("env", "set", "API_KEY", "--secret", "--deployment", "my-app")
    out = gapp("remove", "my-app").output
    assert "Nothing in Google Cloud was touched." in out
    assert "gapp has no command to take them down yet." in out
    for word in ("gcloud", "terraform", "gsutil", "destroy", "delete"):
        assert word not in out.lower(), f"gapp remove printed '{word}'"


def test_remove_says_adding_it_again_does_not_bring_its_settings_back(fresh_machine):
    """Commitment: where gapp cannot do the whole job, it says so. Adding
    a removed deployment again does not restore its settings, and the
    output says that next to the command.

    Why: same as above (CONTRIBUTING.md, "Design goals: A command never
    claims more than it does").
    """
    gapp("add", "my-app", "--project", "my-project", "--repo", "acme/my-app")
    out = gapp("remove", "my-app").output
    assert "gapp add my-app --project my-project --repo acme/my-app" in out
    assert "does not bring back its settings" in out
