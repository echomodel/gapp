"""Tests for gapp.sdk.deploy — Dockerfile and Terraform generation."""

from gapp.sdk.deploy import _generate_dockerfile, _generate_terraform


def test_generate_dockerfile():
    config = {
        "entrypoint": "monarch.mcp.server:mcp_app",
        "port": 8080,
    }
    dockerfile = _generate_dockerfile(config)
    assert "FROM python:3.11-slim-bookworm" in dockerfile
    assert "EXPOSE 8080" in dockerfile
    assert '"monarch.mcp.server:mcp_app"' in dockerfile
    assert '"8080"' in dockerfile


def test_generate_terraform():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "public": False,
        "env": {},
    }
    tf = _generate_terraform("my-app", "my-project", "us-docker.pkg.dev/my-project/gapp/my-app:latest", config)
    assert 'backend "gcs" {}' in tf
    assert 'service_name = "my-app"' in tf
    assert 'project_id   = "my-project"' in tf
    assert "public       = false" in tf
    assert 'output "service_url"' in tf


def test_generate_terraform_with_env():
    config = {
        "entrypoint": "app:main",
        "port": 8080,
        "memory": "512Mi",
        "cpu": "1",
        "max_instances": 1,
        "public": True,
        "env": {"DB_HOST": "localhost", "DEBUG": "true"},
    }
    tf = _generate_terraform("my-app", "proj", "img:latest", config)
    assert "public       = true" in tf
    assert 'DB_HOST = "localhost"' in tf
    assert 'DEBUG = "true"' in tf
