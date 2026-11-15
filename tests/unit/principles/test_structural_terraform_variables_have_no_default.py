import re
from pathlib import Path

MODULE = Path(__file__).resolve().parents[3] / "gapp" / "modules" / "cloud-run-service" / "variables.tf"
STRUCTURAL = ("data_bucket", "labels")


def block(name):
    found = re.search(rf'variable "{name}" \{{(.*?)\n\}}', MODULE.read_text(), re.S)
    assert found, f"the module no longer declares {name}"
    return found.group(1)


def test_what_every_deployment_must_have_cannot_be_left_out_silently():
    """Commitment: a variable the Terraform module needs for every
    deployment (its data bucket, its labels) has no default.

    Why: with a default, a deploy that stops sending it still applies,
    and ships a service without, say, its data volume. With none, it
    fails at plan (CONTRIBUTING.md, "Static Terraform and generated
    tfvars").
    """
    for name in STRUCTURAL:
        assert "default" not in block(name), f"{name} has a default"
