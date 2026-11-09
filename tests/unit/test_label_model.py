"""The label model's fixed points, checked without a cloud or terraform.

- Terraform passes the deployment labels to every Cloud Run service, and
  the root module forwards them.
- Clean break: no gapp code reads or writes the old per-solution project
  labels (gapp__<name>, gapp_<owner>_<name>) or the v-N contract values.
- The fleet's deploy workflow passes the project from the fleet entry, so
  the runner never searches for it.
"""

import pathlib
import re

import yaml

from gapp.admin.sdk import ci as ci_mod

ROOT = pathlib.Path(__file__).resolve().parents[2]
GAPP = ROOT / "gapp"


def test_the_service_module_labels_the_cloud_run_service():
    module = (GAPP / "modules/cloud-run-service/main.tf").read_text()
    service = module[module.index('resource "google_cloud_run_v2_service"'):]
    assert re.search(r"\n\s+labels\s+=\s+var\.labels\n", service[:service.index("template {")])
    assert 'variable "labels"' in (GAPP / "modules/cloud-run-service/variables.tf").read_text()


def test_the_root_module_forwards_the_labels_with_no_default():
    root_vars = (GAPP / "terraform/variables.tf").read_text()
    block = root_vars[root_vars.index('variable "labels"'):]
    block = block[:block.index("}") + 1]
    assert "map(string)" in block and "default" not in block
    assert re.search(r"\n\s+labels\s+=\s+var\.labels\n", (GAPP / "terraform/main.tf").read_text())


def test_no_code_reads_or_writes_old_per_solution_project_labels():
    """The source guard for the clean break (3.x project labels are a manual migration)."""
    old = [
        re.compile(r"gapp__"),                       # global form
        re.compile(r"""f["']gapp_\{"""),             # owned form, built
        re.compile(r"""["']labels:gapp"""),          # project-label searches by prefix
        re.compile(r"""startswith\(["']v-["']\)"""),  # parsing v-N contract values
        re.compile(r"MIN_SUPPORTED_MAJOR"),
    ]
    offenders = []
    for path in GAPP.rglob("*.py"):
        text = path.read_text()
        for pattern in old:
            for m in pattern.finditer(text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{path.relative_to(ROOT)}:{line}: {pattern.pattern}")
    assert offenders == []


def test_the_fleet_deploy_workflow_passes_the_project_from_the_entry():
    rendered = ci_mod._render_template(ci_mod.DEPLOY_WORKFLOW, "example-org/gapp", "v4.0.0")
    deploy = yaml.safe_load(rendered)["jobs"]["deploy"]
    assert deploy["with"]["project-id"] == "${{ needs.resolve.outputs.project }}"
    assert deploy["with"]["environment"] == "${{ needs.resolve.outputs.project }}"


def test_no_code_calls_gcloud_projects_update_with_label_flags():
    """GA `gcloud projects update` takes only --name; project labels go through Resource Manager."""
    offenders = [str(p.relative_to(ROOT)) for p in GAPP.rglob("*.py")
                 if re.search(r'''["']projects["'],\s*["']update["']''', p.read_text())]
    assert offenders == []
