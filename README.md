# gapp — GCP App Deployer

Deploy Python MCP servers to Google Cloud Run with Terraform.

Solution repos remain cloud-agnostic — no GCP imports, no framework dependencies, no auth-aware code. A solution deployed via gapp to Cloud Run works identically when run locally, deployed manually to another cloud, or served without gapp at all. The `gapp.yaml` file is the only touchpoint, and even it is optional metadata — not a code dependency.

gapp handles the full lifecycle: infrastructure, secrets, container builds, multi-user auth, and credential management. Solutions scale to thousands of users without additional engineering, and remain fully isolated from each other even when sharing a GCP project.

## Quick Start

### Option 1: Claude Code Plugin (recommended)

Install the gapp plugin for guided deployment via Claude Code:

```bash
claude plugin marketplace add https://github.com/krisrowe/claude-plugins.git
claude plugin marketplace update claude-plugins
claude plugin install gapp@claude-plugins --scope user
```

Restart Claude Code, then ask: **"help me deploy this app"** or **"deploy this to Cloud Run"**. The plugin's deploy skill walks you through the entire lifecycle.

### Option 2: CLI

Install gapp as a standalone CLI:

```bash
pipx install git+https://github.com/krisrowe/gapp.git
```

There are two paths to deploying a solution. Choose the one that fits your workflow.

### Path A: Local Deploy

Deploy directly from your workstation. Requires `gcloud` and `terraform` installed locally.

```bash
gapp init                          # scaffold gapp.yaml, register locally
gapp setup <gcp-project-id>       # enable APIs, create state bucket, label project
gapp secret set <secret-name>     # populate secrets in Secret Manager
gapp deploy                       # build container + terraform apply
```

### Path B: CI/CD (No Local Terraform)

Set up once from your workstation, then deploy from anywhere — GitHub UI, Claude.ai, Claude Code on the web, your phone. No terraform or docker needed locally. After one-time setup, code changes and deployments are fully decoupled from your machine.

```bash
# One-time setup (requires gcloud + gh CLI):
gapp init                          # scaffold gapp.yaml
gapp setup <gcp-project-id>       # GCP foundation
gapp secret set <secret-name>     # populate secrets
gapp ci init <your-ci-repo>       # designate your private CI repo
gapp ci setup <solution-name>     # create WIF, SA, push workflow

# From now on, deploy from anywhere:
gapp ci trigger <solution-name>   # trigger GitHub Actions deploy
```

After CI setup, any tool with GitHub access can deploy — push a commit, trigger the workflow from GitHub's web UI, or use `gh workflow run` from any device. Cloud-based agents like Claude.ai and Claude Code on the web can make code changes and trigger deployments without access to GCP credentials or a local development environment.

### After Deploying (Both Paths)

```bash
# If auth enabled in gapp.yaml:
gapp users register user@example.com <credential>   # register a user
gapp tokens create user@example.com                  # create a PAT
gapp mcp connect                                     # show client connection info
```

Each command is idempotent and tells you what to do next.

### Solution Lifecycle

`gapp status` tells you where a solution is in its lifecycle:

| State | `initialized` | `project.id` | `pending` | `next_step.action` | CLI | MCP tool | How you get here |
|-------|--------------|-------------|-----------|-------------------|-----|----------|-----------------|
| Not initialized | `false` | — | — | `init` | `gapp init` | `gapp_init` | Haven't run `gapp init` yet |
| Initialized, no project | `true` | `null` | `true` | `setup` | `gapp setup <project-id>` | `gapp_setup` | Ran `gapp init` but not `gapp setup` |
| Has project, not deployed | `true` | set | `true` | `deploy` | `gapp deploy` | `gapp_deploy` | Ran `gapp setup` but not `gapp deploy`, or infrastructure was destroyed |
| Deployed | `true` | set | `false` | — | — | — | Service URL available |

## How It Works

1. **`gapp init`** — creates `gapp.yaml` in your repo root and adds a `gapp-solution` GitHub topic. No cloud interaction.

2. **`gapp setup <gcp-project-id>`** — provisions GCP foundation: enables APIs (Cloud Run, Secret Manager, Cloud Build, Artifact Registry), creates a per-solution GCS bucket (`gapp-{name}-{project-id}`) that holds Terraform state, app data, and — when auth is enabled — per-user credential files, each isolated from the others by prefix, and labels the project. The project ID is remembered for future commands.

3. **`gapp secret set <name>`** — stores secret values in GCP Secret Manager, guided by metadata in `gapp.yaml`.

4. **`gapp deploy`** (Path A) — builds a container image via Cloud Build and deploys to Cloud Run via Terraform. Requires a clean git tree (no uncommitted changes). Skips the build if the image for the current commit already exists.

5. **`gapp ci trigger`** (Path B) — dispatches the solution's GitHub Actions workflow, which runs `gapp deploy` on a runner with WIF-authenticated GCP access. No local terraform or docker.

## What Goes in Your Repo

gapp needs to know how to build and run your service. You have three options, from least to most configuration:

### Option 1: Use the mcp-app framework (zero entrypoint config)

If your repo has an `mcp-app.yaml`, gapp detects it and knows to run `mcp-app serve`. Your `gapp.yaml` only needs env vars and public access — no entrypoint configuration:

```yaml
public: true
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"
```

### Option 2: Specify a command or entrypoint

Tell gapp what to run. Use `service.entrypoint` for an ASGI module:app path (gapp wraps it with uvicorn), or `service.cmd` for any command:

```yaml
service:
  entrypoint: mypackage.server:app    # gapp adds uvicorn + host + port

# OR

service:
  cmd: mcp-app serve                  # runs exactly as written
```

Use one or the other, not both.

### Option 3: Bring your own Dockerfile

If your repo has a `Dockerfile`, gapp builds it as-is. You control the entire build — system dependencies, multi-stage builds, custom runtimes. Less to configure in gapp.yaml, but you maintain the Dockerfile yourself.

### Priority

If multiple options are present, gapp uses the first match:
1. `service.entrypoint` or `service.cmd` in gapp.yaml
2. `Dockerfile` in your repo
3. `mcp-app.yaml` in your repo

### Full gapp.yaml schema

The snippets in this README are illustrative — not exhaustive. For the
authoritative list of every valid field, its type, whether it's
required, and a one-line description, run:

```bash
gapp manifest schema
```

This emits the live JSON Schema generated from gapp's Pydantic model.
It is the same schema used to validate `gapp.yaml` at load time and
embedded in every `manifest_invalid` error response from both the CLI
and the MCP tools — there is no second source.

### Additional gapp.yaml settings

```yaml
public: false         # default — allow unauthenticated HTTP access?

domain: mcp.example.com  # optional — custom domain (subdomain only)

env:                  # environment variables
  - name: LOG_LEVEL
    value: INFO
  - name: SIGNING_KEY
    secret:             # backed by Secret Manager
      generate: true    # auto-create if missing

  # {{SOLUTION_DATA_PATH}} resolves to the GCS FUSE mount path
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"

# Legacy — prerequisite secrets (still supported):
prerequisites:
  secrets:
    api-token:
      description: "API authentication token"
```

### Custom Domain

Set `domain` in `gapp.yaml` to map a custom subdomain to your Cloud Run service:

```yaml
domain: mcp.example.com
```

On the next `gapp deploy`, gapp creates a Cloud Run domain mapping. Add a CNAME record at your domain registrar:

```
CNAME  mcp.example.com  →  ghs.googlehosted.com
```

`gapp status` reports the domain state: `pending_dns`, `pending_cert`, or `active`. The default `.run.app` URL always works — the custom domain is an additional endpoint, not a replacement. Only subdomains are supported (not bare/apex domains).

### Multi-Service Repos

A repo can contain multiple deployable services. Add `paths:` to your root `gapp.yaml`:

```yaml
paths:
  - mcp/diet
  - mcp/workout
```

Each path has its own `gapp.yaml` with service-specific config:

```yaml
# mcp/diet/gapp.yaml
public: true
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"
```

Service names auto-derive from `{repo}-{path}` (e.g., `echofit-mcp-diet`). Override with `name:`:

```yaml
name: echofit
public: true
```

`gapp.yaml` uses one schema everywhere. Any file can combine `paths:` (point to more services) with service config (`public:`, `env:`, etc.). No `paths:` key → single-service mode, same as before. Fully backwards compatible.

**Name changes and Terraform:** If the service name changes (e.g., from `echofit` to `echofit-mcp`), Terraform will plan a destroy + create. You'll see this in the plan before anything happens. Use `name:` to preserve the existing service name when migrating, or accept the rename.

#### Does gapp.yaml couple my app to gapp?

No. It's a deployment descriptor — like `Dockerfile`, `fly.toml`, or `docker-compose.yml`. Doesn't modify code, add dependencies, or require imports. Remove it and the app works everywhere else. Repos routinely carry configs for multiple deployment tools.

## Additional Commands

```
gapp status [name] [--json]          Infrastructure health check with guided next steps
gapp list [--available]              List registered solutions (--available for GitHub)
gapp restore <name>                  Clone from GitHub + find GCP project
gapp plan                            Terraform plan (preview changes)

gapp mcp status [name] [--json]      MCP health + tool enumeration
gapp mcp list [--json]               List solutions with MCP endpoints
gapp mcp connect [name] [--json]     Client connection info (Claude Code, Gemini CLI, Claude.ai)
  --user <email>                     Mint a real PAT for the connection commands
  --claude <scope>                   Filter to Claude Code config (user/project)
  --gemini <scope>                   Filter to Gemini CLI config (user/project)

gapp secret list                     Show prerequisite secrets and status
gapp users register <email> <cred>   Register a user with upstream credential
gapp users list                      List registered users
gapp users update <email> [options]  Update credential or set revoke_before
gapp users revoke <email>            Delete user's credential file
gapp tokens create <email>           Create a PAT (JWT) for a user
gapp tokens revoke <email>           Invalidate all PATs for a user
```

## Key Concepts

- **Solution** — a repo with `gapp.yaml`. One repo = one Cloud Run service.
- **Per-solution bucket** — `gapp-{name}-{project-id}` holds Terraform state (`terraform/state/`), app data (`data/`, FUSE-mounted into the container), and per-user credential files (`data/auth/`, when auth is enabled). Created by `gapp setup`. Contents are isolated by prefix — see [Security Isolation](#security-isolation).
- **GCP project labels** — `gapp-{name}=default` enables auto-discovery on new workstations.
- **GitHub topic** — `gapp-solution` enables discovery via `gapp solutions list --available`.
- **Image tagging** — images are tagged with the HEAD commit SHA. Builds are skipped if the image already exists.
- **Source integrity** — `git archive HEAD` is used as the build source. Uncommitted changes and gitignored files are never included.
- **Credential mediation** — when `auth.enabled`, gapp injects an ASGI wrapper (`gapp-run`) at deploy time that handles JWT-based client auth and upstream credential lookup via GCS FUSE. Solutions remain unaware of the auth layer.

## Prerequisites

**Both paths:**
- Python 3.10+
- `gcloud` CLI (authenticated)
- `gh` CLI (for GitHub integration)

**Path A (local deploy) also requires:**
- `terraform` CLI

**Path B (CI/CD) does not require terraform or docker locally.** After one-time setup, all deployments run on GitHub Actions runners.

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/unit/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture and design principles.

See [docs/CI.md](docs/CI.md) for deploying without a local machine — via GitHub Actions, Workload Identity Federation, and the operator repo pattern.

---

## Why gapp

### Cloud-Agnostic Solutions

Solutions never import gapp, never reference GCP, and never contain gapp-specific auth code. A solution is a standard Python ASGI app. This means:

- **Run locally** with `uvicorn myapp:app`
- **Deploy to Cloud Run via gapp** with infrastructure managed for you
- **Deploy to any cloud manually** — the app has no GCP coupling to remove
- **Use stdio transport** for local MCP clients with no HTTP at all

gapp is an overlay, not a lock-in.

### Infrastructure You Don't Have to Think About

gapp manages Terraform, IAM, API enablement, service accounts, secret references, and container builds behind four commands. You never write HCL, never enable a GCP API by hand, never create a service account or grant it roles. `gapp setup` handles the foundation, `gapp deploy` handles the rest. If the underlying Terraform modules evolve (new security controls, new resource types), all solutions benefit automatically on their next deploy.

### Agent Wiring Made Easy

Once deployed, `gapp mcp connect` generates ready-to-use connection commands for Claude Code, Gemini CLI, and Claude.ai — with the real service URL and MCP path already filled in. No hunting for hostnames, no guessing the right CLI flags. It checks whether each client already has the service registered and shows the exact command to add it. The auth token placeholder is `<YOUR_PAT>`; obtaining the actual token is the solution framework's responsibility, not gapp's. For automation, `--json` returns a structured result that scripts or MCP tools can consume directly.

This scales to tens of thousands of users. Each user is a single small file in GCS (~100 bytes). Lookups are O(1) by email hash. No databases, no user tables, no connection pools.

### Security Isolation

Solutions sharing a GCP project are fully isolated:

- **Per-solution Terraform state** — each solution's infrastructure is independently managed
- **Per-solution service account** — no shared identity
- **Per-secret IAM** — each service account can only access its own declared secrets, not project-wide
- **Per-solution GCS bucket** — one bucket per solution (`gapp-{name}-{project-id}`) holds Terraform state and app data, separated by prefix
- **State isolated from the container at runtime** — the Cloud Run container FUSE-mounts the bucket with `only-dir=data`, so the running app can only see and write the `data/` subtree. Terraform state under `terraform/state/` is unreachable from inside the container even though it lives in the same bucket. A compromised or misbehaving app cannot read or corrupt its own infrastructure state.
- **Cloud-backed Terraform state enables multi-workstation IaC** — state lives in GCS, not on a single developer's machine. `gapp deploy` can be run from any authorized workstation (or from CI) and pick up exactly where another left off. No state file to hand around, no risk of divergent local state.

Solutions can share a project (for billing convenience and API enablement) or use separate projects (for stricter blast radius). The framework works identically either way.

### Scalability Without Complexity

The design avoids patterns that require re-engineering at scale:

| Concern | Approach | Why it scales |
|---------|----------|--------------|
| Secret management | GCP Secret Manager per-secret, labeled by solution | No central vault; IAM scoped per service account |
| Infrastructure | Terraform with generated tfvars | Declarative, idempotent, no drift between solutions |
| Container builds | Cloud Build + git archive | No local Docker; image tagged by commit SHA |

### Known Limitations

These are conscious tradeoffs in favor of simplicity:

- **GCP-only deployment** — gapp deploys to Cloud Run. Solutions themselves are cloud-agnostic, but the framework's infrastructure automation targets GCP.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed architecture, code structure, and design principles.
