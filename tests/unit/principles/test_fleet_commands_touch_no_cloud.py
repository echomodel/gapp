from .conftest import gapp


def test_defining_changing_and_removing_a_deployment_runs_no_cloud_tool(fresh_machine, no_cloud, tmp_path):
    """Commitment: a command that changes or reads the fleet runs no
    gcloud, Terraform or GitHub CLI.

    Why: defining what should run and deploying it need different access,
    so each must work where the other's is missing (CONTRIBUTING.md,
    "Properties kept on purpose").
    """
    settings = tmp_path / "settings.yaml"
    settings.write_text("public: true\nmemory: 1Gi\n")
    for command in (
        ("add", "my-app", "--project", "my-project", "--repo", "acme/my-app"),
        ("add", "my-app-test", "--copy-of", "my-app", "--project", "my-test-project"),
        ("set", "ref", "main", "--deployment", "my-app"),
        ("set", "domain", "app.example.com", "--deployment", "my-app"),
        ("unset", "domain", "--deployment", "my-app"),
        ("env", "set", "LOG_LEVEL", "info", "--deployment", "my-app"),
        ("env", "set", "APP_KEY", "--secret", "--generate", "--deployment", "my-app"),
        ("env", "unset", "LOG_LEVEL", "--deployment", "my-app"),
        ("apis", "add", "drive.googleapis.com", "--deployment", "my-app"),
        ("apply", str(settings), "--deployment", "my-app"),
        ("show", "--deployment", "my-app"),
        ("list",),
        ("fleet",),
        ("fleet", "export"),
        ("rename", "my-app", "prod"),
        ("remove", "prod"),
    ):
        result = gapp(*command)
        assert result.exit_code == 0, f"gapp {' '.join(command)}: {result.output}{result.exception!r}"
