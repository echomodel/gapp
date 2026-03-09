# gapp — GCP App Deployer

Deploy Python MCP servers to Google Cloud Run with Terraform.

## Quick Start

```bash
pip install -e .
```

From inside a git repo with a Python package:

```bash
gapp init                          # scaffold gapp.yaml, register locally
gapp setup <gcp-project-id>       # enable APIs, create state bucket, label project
gapp secret set <secret-name>     # populate secrets in Secret Manager
gapp deploy                       # build container + terraform apply
```

Each command is idempotent and tells you what to do next.

## How It Works

1. **`gapp init`** — creates `gapp.yaml` in your repo root and adds a `gapp-solution` GitHub topic. No cloud interaction.

2. **`gapp setup <gcp-project-id>`** — provisions GCP foundation: enables APIs (Cloud Run, Secret Manager, Cloud Build, Artifact Registry), creates a per-solution GCS bucket for Terraform state, and labels the project. The project ID is remembered for future commands.

3. **`gapp secret set <name>`** — stores secret values in GCP Secret Manager, guided by metadata in `gapp.yaml`.

4. **`gapp deploy`** — builds a container image via Cloud Build and deploys to Cloud Run via Terraform. Requires a clean git tree (no uncommitted changes). Skips the build if the image for the current commit already exists.

## The `gapp.yaml` File

Add this to your repo root:

```yaml
service:
  entrypoint: mypackage.mcp.server:mcp_app   # REQUIRED: uvicorn module:app

prerequisites:
  secrets:
    api-token:
      description: "API authentication token"
```

Optional overrides:

```yaml
service:
  entrypoint: mypackage.mcp.server:mcp_app
  memory: "512Mi"       # default
  cpu: "1"              # default
  max_instances: 1      # default
  public: false         # default
  env:                  # default: {}
    LOG_LEVEL: "INFO"
```

## Additional Commands

```
gapp status [name]                 Show solution health across all phases
gapp plan                          Terraform plan (preview changes)
gapp solutions list [--available]  List local (and optionally GitHub) solutions
gapp solutions restore <name>     Clone from GitHub + find GCP project
gapp secret list                   Show prerequisite secrets and status
```

## Key Concepts

- **Solution** — a repo with `gapp.yaml`. One repo = one Cloud Run service.
- **Per-solution bucket** — `gapp-{name}-{project-id}` stores Terraform state. Created by `gapp setup`.
- **GCP project labels** — `gapp-{name}=default` enables auto-discovery on new workstations.
- **GitHub topic** — `gapp-solution` enables discovery via `gapp solutions list --available`.
- **Image tagging** — images are tagged with the HEAD commit SHA. Builds are skipped if the image already exists.
- **Source integrity** — `git archive HEAD` is used as the build source. Uncommitted changes and gitignored files are never included.

## Prerequisites

- Python 3.10+
- `gcloud` CLI (authenticated)
- `terraform` CLI
- `gh` CLI (for GitHub topic management)

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/unit/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture and design principles.
