# gapp — GCP App Deployer

Deploy Python MCP servers to Google Cloud Run with Terraform.

Solution repos remain cloud-agnostic — no GCP imports, no framework dependencies, no auth-aware code. A solution deployed via gapp to Cloud Run works identically when run locally, deployed manually to another cloud, or served without gapp at all. The `gapp.yaml` file is the only touchpoint, and even it is optional metadata — not a code dependency.

gapp handles the full lifecycle: infrastructure, secrets, container builds, multi-user auth, and credential management. Solutions scale to thousands of users without additional engineering, and remain fully isolated from each other even when sharing a GCP project.

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

# If auth enabled in gapp.yaml:
gapp users register user@example.com <credential>   # register a user
gapp tokens create user@example.com                  # create a personal access token (PAT)
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
  runtime: <gapp-git-ref>  # required when auth enabled; gapp_run version
  mcp_path: /mcp          # MCP endpoint path (enables gapp mcp commands)
  auth:
    enabled: true
    strategy: bearer      # default; or google_oauth2
```

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
- **Per-solution bucket** — `gapp-{name}-{project-id}` stores Terraform state. Created by `gapp setup`.
- **GCP project labels** — `gapp-{name}=default` enables auto-discovery on new workstations.
- **GitHub topic** — `gapp-solution` enables discovery via `gapp solutions list --available`.
- **Image tagging** — images are tagged with the HEAD commit SHA. Builds are skipped if the image already exists.
- **Source integrity** — `git archive HEAD` is used as the build source. Uncommitted changes and gitignored files are never included.
- **Credential mediation** — when `auth.enabled`, gapp injects an ASGI wrapper (`gapp-run`) at deploy time that handles JWT-based client auth and upstream credential lookup via GCS FUSE. Solutions remain unaware of the auth layer.

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

---

## Why gapp

### Cloud-Agnostic Solutions

Solutions never import gapp, never reference GCP, and never contain auth logic. A solution is a standard Python ASGI app that reads an `Authorization: Bearer <token>` header — the same interface whether the token comes from a local test client, a direct HTTP caller, or gapp's credential mediation wrapper. This means:

- **Run locally** with `uvicorn myapp:app` and a token in the header
- **Deploy to Cloud Run via gapp** with multi-user auth, credential rotation, and infrastructure managed for you
- **Deploy to any cloud manually** — the app has no GCP coupling to remove
- **Use stdio transport** for local MCP clients with no HTTP at all

gapp is an overlay, not a lock-in.

### Infrastructure You Don't Have to Think About

gapp manages Terraform, IAM, API enablement, service accounts, secret references, and container builds behind four commands. You never write HCL, never enable a GCP API by hand, never create a service account or grant it roles. `gapp setup` handles the foundation, `gapp deploy` handles the rest. If the underlying Terraform modules evolve (new security controls, new resource types), all solutions benefit automatically on their next deploy.

### Agent Wiring Made Easy

Once deployed, `gapp mcp connect` generates ready-to-use connection commands for Claude Code, Gemini CLI, and Claude.ai — with the real service URL, MCP path, and credentials already filled in. No hunting for hostnames, no copy-pasting tokens into config files, no guessing the right CLI flags. It checks whether each client already has the service registered and shows the exact command to add it. With `--user`, it mints a real PAT inline so the output is immediately usable. For automation, `--json` returns a structured result that scripts or MCP tools can consume directly.

### Multi-User from Day One

When auth is enabled, gapp injects a credential mediation wrapper at deploy time. Each user gets a long-lived personal access token (PAT) and their upstream API credential is stored server-side. The solution never sees PATs or credential files — it receives a standard bearer token on every request.

PATs make deployed services portable across clients. Tools like Claude Code, Gemini CLI, and IDE extensions (Antigravity, etc.) authenticate with static headers or URL parameters loaded at startup — they have no way to manage token refresh or run an OAuth2 flow. Claude.ai supports OAuth2 but requires you to implement your own authorization server with a web-based consent flow, and you'd still need to mediate the upstream service's credentials behind it. With PATs, any client that can set an HTTP header or append a query parameter can authenticate — no OAuth2 infrastructure required. Meanwhile, the real upstream credentials — which often expire, rotate, or require refresh — are managed in one place on the server. When a backend token changes, you update it once with `gapp users update` and every device and agent keeps working.

This is also more secure. Raw credentials — like Google OAuth refresh tokens or API keys for financial services — never leave the server. They aren't scattered across workstations, dotfiles, or handed directly to third-party agents (local or cloud-hosted). A PAT, if exposed, only grants access through the MCP tools you've deployed — not direct access to the underlying service. An attacker with a leaked PAT can call your MCP tools but cannot, for example, access your Google account directly or call arbitrary API endpoints. And PATs can be revoked instantly without touching the upstream credential.

- **Register users** with `gapp users register` — one credential file per user in GCS
- **Issue PATs** with `gapp tokens create` — signed JWTs, default 10-year duration
- **Rotate credentials centrally** with `gapp users update` — all clients keep working, no PAT reissue needed
- **Revoke access** by deleting the credential file or invalidating all tokens with a timestamp

This scales to tens of thousands of users. Each user is a single small file in GCS (~100 bytes). Lookups are O(1) by email hash. No databases, no user tables, no connection pools.

### Security Isolation

Solutions sharing a GCP project are fully isolated:

- **Per-solution Terraform state** — each solution's infrastructure is independently managed
- **Per-solution service account** — no shared identity
- **Per-secret IAM** — each service account can only access its own declared secrets, not project-wide
- **Per-solution GCS bucket** — credential files and state are in separate buckets
- **Per-solution signing key** — JWT signing keys are auto-generated by Terraform and scoped to the solution

Solutions can share a project (for billing convenience and API enablement) or use separate projects (for stricter blast radius). The framework works identically either way.

### Scalability Without Complexity

The design avoids patterns that require re-engineering at scale:

| Concern | Approach | Why it scales |
|---------|----------|--------------|
| User credentials | One GCS file per user | GCS handles millions of objects; no single-file bottleneck |
| Credential lookup | SHA-256 email hash → file path | O(1), no index, no scan |
| Token caching | In-memory (5-min TTL) + GCS FUSE | 99% of requests hit memory; FUSE handles cross-instance sharing |
| Secret management | GCP Secret Manager per-secret | No central vault; IAM scoped per service account |
| Infrastructure | Terraform with generated tfvars | Declarative, idempotent, no drift between solutions |
| Container builds | Cloud Build + git archive | No local Docker; image tagged by commit SHA |

### Known Limitations

These are conscious tradeoffs in favor of simplicity:

- **User credentials in GCS, not Secret Manager** — GCS lacks audit logging and versioning. Acceptable because per-user credentials are high-cardinality, low-value-per-unit. Deployment secrets (signing keys, API keys) remain in Secret Manager.
- **No self-registration** — an admin must register users via CLI. The future OAuth2 authorization server phase adds self-registration when needed.
- **5-minute revocation window** — in-memory cache TTL means revoked users may retain access for up to 5 minutes. Acceptable for the threat model.
- **GCP-only deployment** — gapp deploys to Cloud Run. Solutions themselves are cloud-agnostic, but the framework's infrastructure automation targets GCP.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed architecture, code structure, and design principles.
