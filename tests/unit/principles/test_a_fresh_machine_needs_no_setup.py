from .conftest import fleet_file, gapp


def test_the_first_command_on_a_fresh_machine_defines_a_deployment(fresh_machine):
    """Commitment: on a machine where nothing has been set up, `gapp add`
    works. No config file, profile step or path comes first.

    Why: the first command must not be configuration (CONTRIBUTING.md,
    "Design goals: Minimal cognitive load"; "Properties kept on purpose").
    """
    assert not any(fresh_machine.iterdir())
    result = gapp("add", "my-app", "--project", "my-project", "--repo", "acme/my-app")
    assert result.exit_code == 0, result.output
    assert gapp("show", "--deployment", "my-app").exit_code == 0
    assert fleet_file(fresh_machine).is_file()


def test_words_for_optional_capabilities_stay_out_of_a_first_users_output(fresh_machine):
    """Commitment: someone with one fleet and one login never meets the
    words for what they have not turned on: profiles, a fleet repo, CI.

    Why: vocabulary appears with need, never before (CONTRIBUTING.md,
    "Design goals: Each added capability is opt-in").
    """
    said = "".join(gapp(*command).output for command in (
        ("add", "my-app", "--project", "my-project", "--repo", "acme/my-app"),
        ("set", "public", "true", "--deployment", "my-app"),
        ("env", "set", "LOG_LEVEL", "info", "--deployment", "my-app"),
        ("show", "--deployment", "my-app"),
        ("list",),
        ("rename", "my-app", "prod"),
        ("remove", "prod"),
    )).lower()
    for word in ("profile", "fleet repo", "gapp ci", "github", "runner", "workflow"):
        assert word not in said, f"a first user's output says '{word}'"
