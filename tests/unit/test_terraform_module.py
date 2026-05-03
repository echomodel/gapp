"""Conformance tests for the cloud-run-service terraform module.

These tests run `terraform init -backend=false` and `terraform validate`
against `gapp/modules/cloud-run-service/` from a throwaway wrapper module.
They verify the module's input contract — specifically that `data_bucket`
is a required variable (no default, no empty-string opt-out) so a missing
key in `_build_tfvars` fails terraform plan loudly instead of silently
stripping the GCS-FUSE volume from the deployed Cloud Run service.

See issue #35 for the regression these guards prevent.

If `terraform` isn't on PATH, these tests skip — they are conformance
tests for the deploy contract, not for terraform itself.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "gapp" / "modules" / "cloud-run-service"


def _have_terraform() -> bool:
    return shutil.which("terraform") is not None


pytestmark = pytest.mark.skipif(
    not _have_terraform(),
    reason="terraform CLI not installed; module conformance tests skipped",
)


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _wrapper(tmp_path: Path, body: str) -> Path:
    (tmp_path / "main.tf").write_text(body)
    init = _run(["terraform", "init", "-backend=false", "-input=false"], tmp_path)
    assert init.returncode == 0, f"terraform init failed:\n{init.stderr}"
    return tmp_path


def test_module_validates_with_required_inputs(tmp_path):
    """All required tfvars present → terraform validate passes."""
    _wrapper(tmp_path, f'''
module "svc" {{
  source       = "{MODULE_PATH}"
  project_id   = "p"
  service_name = "s"
  image        = "us-central1-docker.pkg.dev/p/r/i:t"
  data_bucket  = "gapp-s-p"
}}
''')
    res = _run(["terraform", "validate"], tmp_path)
    assert res.returncode == 0, f"validate failed:\n{res.stdout}\n{res.stderr}"


def test_module_rejects_missing_data_bucket(tmp_path):
    """data_bucket omitted → terraform validate fails with required-argument error.

    This is the load-bearing assertion for issue #35: if `_build_tfvars` ever
    drops `data_bucket` again, a deploy will fail at terraform plan instead
    of silently shipping a Cloud Run service with no persistent volume.
    """
    _wrapper(tmp_path, f'''
module "svc" {{
  source       = "{MODULE_PATH}"
  project_id   = "p"
  service_name = "s"
  image        = "us-central1-docker.pkg.dev/p/r/i:t"
}}
''')
    res = _run(["terraform", "validate"], tmp_path)
    assert res.returncode != 0
    combined = res.stdout + res.stderr
    assert "data_bucket" in combined
    assert "required" in combined.lower()
