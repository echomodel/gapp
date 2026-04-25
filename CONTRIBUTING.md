# Contributing to gapp

This document covers the design, architecture, and principles behind gapp. Read this before contributing code.

---

## What gapp Does

gapp is a CLI tool that deploys Python MCP servers to Google Cloud Run with Terraform. A developer adds a `gapp.yaml` to their repo and runs four commands:

```
gapp init                    # scaffold gapp.yaml + Dockerfile, register locally
gapp setup <project-id>      # enable GCP APIs, create state bucket, label project
gapp secret set <name>       # populate secrets in Secret Manager
gapp deploy                  # build container, terraform apply
```

## Design Goals

**Remote MCP Access.** Every MCP server that accesses non-local resources (APIs, cloud data, third-party services) should be easily deployable to Cloud Run.

**Minimal Cognitive Load.** Adding a new MCP server to GCP should require near-zero configuration. The CLI always tells the user what step comes next.

**Resilient and Recoverable.** It should always be possible to tear down and rebuild. The system makes external dependencies, secret sources, and recovery paths clear.

**Solutions Are Independent.** Solutions in the same GCP project co-exist without knowing about each other. Each has its own TF state, secrets, and Cloud Run services.

**State Is Cloud-Native.** Terraform state lives in GCS (`terraform/state/` prefix in the per-solution bucket), not on any one workstation. Any authorized machine — or CI — can run `gapp deploy` and pick up the same state. There is no local `terraform.tfstate` to synchronize. At runtime, the Cloud Run container FUSE-mounts the same bucket with `only-dir=data`, so the running app can only see and write the `data/` subtree. Terraform state is structurally unreachable from inside the container.

## Architecture

### Where Things Live

```
GAPP REPO (this repo — defines HOW to deploy)
  terraform/main.tf                     ← static HCL, references shared module
  terraform/variables.tf                ← variable declarations
  modules/cloud-run-service/            ← reusable TF module
  gapp/sdk/deploy.py                    ← orchestrates build + TF apply

SOLUTION REPO (what gets deployed)
  gapp.yaml                             ← solution metadata, prerequisites
  Dockerfile                            ← optional; if absent, gapp generates one at build time

GCP (runtime state)
  Project labels: gapp-<owner>-{name}=default   ← links project to solution
  Secret Manager: labeled gapp-solution=<name>  ← every gapp-managed secret
  Cloud Run: running services           ← runtime
  GCS: gapp-{name}-{project-id}/       ← per-solution bucket
    terraform/state/                    ← TF state (not visible to container)
    data/                               ← app data (FUSE-mounted at /mnt/data via only-dir=data)
  Cloud Build: container image builds   ← no local Docker needed
  Artifact Registry: gapp/ repo         ← container images

LOCAL (~/.config/gapp/, working cache, fully reconstructable)
  config.yaml                           ← owner + solution registry (replaces solutions.yaml)
  ~/.cache/gapp/{solution}/terraform/   ← staged TF files + generated tfvars.json
```

### Convention Over Configuration

Most values are derived, not configured:

| Field | Convention | Override needed? |
|-------|-----------|-----------------|
| Solution name | Git repo directory name | Rarely (configurable in gapp.yaml) |
| Solution bucket | `gapp-{name}-{project-id}` (holds TF state and app data under distinct prefixes) | Never |
| TF state path | `terraform/state/` in solution bucket (not visible to container — FUSE mount is scoped to `data/`) | Never |
| App data path | `data/` in solution bucket, FUSE-mounted into the container at `/mnt/data` | Never |
| TF location | Static HCL in gapp repo, staged to `~/.cache/gapp/` | Never |
| Repo identity | Current working directory (git root) | Always use cwd |
| Image tag | HEAD commit SHA (12 chars) | Never |

### The 1:1:1 Default

One repo = one solution = one Cloud Run service. This is the default. Multi-service repos are supported via the workspace pattern (see README).

#### Workspace pattern internals

When `paths:` is present in gapp.yaml, gapp iterates each path, loads that path's gapp.yaml, and deploys as an independent service. Each service gets its own Cloud Run service, Terraform state, service account, and secrets — same isolation as separate repos.

Service name derivation: `{repo-name}-{path-segments-joined-with-hyphens}`. Override with `name:` in any gapp.yaml. The schema is recursive — any gapp.yaml can have both `paths:` and service config, and `paths:` targets can themselves have `paths:`.

Container builds for multi-package repos: when no `pyproject.toml` exists at repo root, the Dockerfile finds all `pyproject.toml` files up to 2 levels deep and installs each. The entire repo is copied into the container so cross-directory dependencies resolve.

Modeled on: npm workspaces (`package.json`), Cargo workspaces (`Cargo.toml`), Maven multi-module (`pom.xml`). Same filename at every level, same schema, different keys populated.

### GitHub-Centric Discovery

Solutions are discovered via GitHub repos and topics, not GCP project configurations. GitHub is more durable and discoverable than GCP for this purpose — repos have READMEs, topics, and are browsable. GCP labels (`gapp-<owner>-{name}=default`) are the secondary source, used to map a solution to its GCP project. Local config (`~/.config/gapp/config.yaml`) is a working registry reconstructable from GitHub + GCP.

gapp is GitHub-flavored but not GitHub-locked. The core lifecycle — `gapp init`, `gapp setup`, `gapp secret set`, `gapp deploy` — works with any local git repo and requires no GitHub account, no GitHub API, and no GitHub Actions. GitHub is required only for optional features: remote discovery (`gapp list --available`), CI/CD automation (`gapp ci`), and installing the runtime wrapper during container build. The CI layer calls `gapp deploy` — not the other way around.

## Solution Lifecycle Phases

| Phase | What | Command |
|-------|------|---------|
| **Foundation** | GCP project exists, APIs enabled, bucket exists, project labeled | `gapp setup <project-id>` |
| **Prerequisites** | Secrets populated in Secret Manager | `gapp secret set <name>` |
| **Application** | Cloud Run service deployed via Terraform | `gapp deploy` |

Each phase completes cleanly and tells the user what comes next. No phase does double duty.

## The `gapp.yaml` File

Each solution repo has a manifest at the root:

```yaml
service:
  entrypoint: myapp.mcp.server:mcp_app   # REQUIRED: uvicorn module:app

prerequisites:
  secrets:
    api-token:
      description: "API authentication token"
```

Key decisions:
- **`service.entrypoint` is required** — passed as `--build-arg` to the static Dockerfile.
- **Port 8080 is hardcoded** — not configurable. All Cloud Run services use 8080.
- **No `prerequisites.apis`** — foundation APIs are hardcoded in `gapp setup`.
- **Secrets require an explicit `name`** — the `name` field under `secret:` is the short name in Secret Manager. gapp prefixes it with the solution name: `name: signing-key` on solution `my-app` → `my-app-signing-key` in Secret Manager. No auto-derivation from the env var name.
- **Every gapp-managed secret is stamped with `gapp-solution=<name>`** — the label is the machine-readable ownership signal. `gapp secrets list`, the pre-deploy validator, and any future tooling query Secret Manager by `labels.gapp-solution=<solution>` (one call) and diff against gapp.yaml declarations.
- **gapp never implicitly takes over pre-existing secrets** — if `gapp secrets set` or a deploy-time generate path tries to create `<solution>-<short-name>` and a secret at that ID already exists without a matching `gapp-solution=<solution>` label, the operation fails with an actionable error. Every secret gapp manages is labeled; the absence of a label (or a different owner label) means something outside gapp's lifecycle put it there, and silently adopting it would be a security-sensitive side-effect. The user must investigate manually with `gcloud secrets describe` and either delete the existing secret (so gapp can reclaim the name) or resolve the ownership conflict another way.

  ```bash
  # 1. Copy the value from the legacy secret to the solution-scoped name,
  #    stamping the label in one shot.
  gcloud secrets versions access latest \
      --secret=<legacy-name> --project=$PROJECT | \
    gcloud secrets create <solution>-<short-name> --project=$PROJECT \
      --data-file=- --labels=gapp-solution=<solution>

  # 2. Redeploy — terraform now mounts the new name.
  gapp deploy

  # 3. After verification, delete the legacy secret
  #    (only once no solution still mounts it).
  gcloud secrets delete <legacy-name> --project=$PROJECT
  ```
- **Custom domains are subdomains only** — `domain` in gapp.yaml creates a Cloud Run domain mapping with a CNAME record. Apex/bare domains (`example.com`) are not supported because they require A records instead of CNAME, adding complexity for a scenario that's unlikely — MCP servers and web API services are virtually always hosted on subdomains (`mcp.example.com`, `api.example.com`).
- **gapp.yaml has exactly ONE source of truth: `gapp/admin/sdk/schema.py`.** The Pydantic `Manifest` model (and its submodels: `ServiceSpec`, `EnvEntry`, `SecretSpec`, `Prerequisites`, etc.) is the sole authority for every field, type, required flag, and enum value. Everything else derives from it at call time:

  | Consumer                              | How it derives from the model                                                         |
  |---------------------------------------|---------------------------------------------------------------------------------------|
  | Runtime validation (every load, deploy, etc.) | `load_manifest` → `validate_manifest` → `Manifest.model_validate()`                |
  | Error responses (CLI + MCP + SDK)     | `ManifestValidationError.to_dict()` embeds `Manifest.model_json_schema()` live       |
  | CLI schema dump                       | `gapp manifest schema` → `get_schema()` → `Manifest.model_json_schema()`                       |
  | MCP schema tool                       | `gapp_schema` → `get_schema()` → `Manifest.model_json_schema()`                       |
  | Editor / JSON-Schema tooling          | Run `gapp manifest schema` on demand. **No JSON file is committed.**                           |
  | README / CONTRIBUTING / SKILL docs    | Reference `gapp manifest schema` (CLI example) rather than re-listing fields.                  |
  | Unit tests                            | Import models (`Manifest`, `EnvEntry`, etc.) from `schema.py`; never re-declare fields. |

  **Rule:** no other file in this repo — code, markdown, tests, generated artifact — may independently enumerate gapp.yaml fields. If documentation needs to show the schema, point at `gapp manifest schema`. If tests need a field list, import it from `schema.py`. If error payloads need field info, embed `Manifest.model_json_schema()`. Changing a field means editing exactly one Python file.

  Unknown fields are rejected (`extra="forbid"`) so typos surface as validation errors with the offending yaml path.

Optional overrides with defaults:

```yaml
service:
  entrypoint: app.mcp.server:mcp_app   # REQUIRED
  memory: "512Mi"                       # default
  cpu: "1"                              # default
  max_instances: 1                      # default
  public: false                         # default
  env:                                  # default: {}
    DB_HOST: "localhost"
```

### gapp.yaml Design Decisions

**Auth is not gapp's concern.** gapp is purely a deployment tool: containers, secrets, data volumes, IAM. How a solution authenticates clients or mediates upstream credentials is entirely the solution's business. gapp does not ship auth middleware, does not manage users, does not mint tokens.

**gapp's boundary is "service is up."** `gapp_status` checks `/health` as a liveness convenience — it confirms the container started and accepts HTTP. Everything beyond that (auth verification, tool enumeration, user management, MCP client registration, MCP endpoint paths) is the solution's concern, handled by the solution framework's own admin CLI and skills. gapp does not probe app-specific endpoints or know anything about MCP.

**`public` is an independent flag.** Public access (Cloud Run `allUsers` IAM) is set by the `public` field in gapp.yaml or the CLI/MCP arg, independent of anything else. Default is non-public (safe). Priority on each deploy: CLI/MCP arg → gapp.yaml `public:` → default false.

**`service.entrypoint` and `service.cmd` are mutually exclusive.** `entrypoint` is an ASGI module:app path — gapp wraps it with uvicorn. `cmd` is a raw command — gapp passes it through as the Dockerfile CMD. Having both is ambiguous, so gapp rejects it.

**How gapp determines what to run.** At deploy time, gapp resolves the container entrypoint in this order:

1. `service.entrypoint` or `service.cmd` in gapp.yaml — explicit config, always takes priority. Use `entrypoint` for ASGI module:app paths (gapp wraps with uvicorn). Use `cmd` for raw commands (e.g., `mcp-app serve`). These are mutually exclusive.
2. `Dockerfile` in the repo — solution controls its own build entirely. gapp builds it as-is, no generated CMD.
3. `mcp-app.yaml` in the repo — gapp detects this file and generates `CMD ["mcp-app", "serve"]`. This is a minimal coupling: gapp knows the filename and the command string. If `mcp-app` renames its config file or changes its serve command, this detection breaks. The coupling is accepted because it eliminates an otherwise-mandatory `service.cmd` line from every mcp-app solution's gapp.yaml, and because mcp-app is a first-party framework in this ecosystem. Solutions that prefer no coupling can use `service.cmd: mcp-app serve` explicitly and skip detection.
4. None of the above — error with guidance listing all options.

**Dockerfile tradeoffs.** The design preference is for solutions to NOT maintain a Dockerfile — gapp generates one, meaning less maintenance and consistent builds across solutions. But maintaining a Dockerfile gives full control over the build (custom system deps, non-Python components, multi-stage builds). Both are valid. gapp uses a solution's Dockerfile without question when present.

**`env` section replaces `prerequisites.secrets`.** The old `prerequisites.secrets` section is deprecated. The `env` section supports plain values, secret-backed values, and auto-generation (`generate: true`). Each secret entry requires a `name` field — the short name used in Secret Manager (auto-prefixed with the solution name for isolation). `{{VARIABLE}}` substitution resolves gapp-provided values (`SOLUTION_DATA_PATH`, `SOLUTION_NAME`) at deploy time. Secrets with `generate: true` are created automatically during deploy. Secrets without `generate` must be populated in advance with `gapp secrets set`.

## Static Terraform + Generated tfvars.json

Solutions never own Terraform files. gapp manages TF:

- **Static HCL** lives in this repo at `terraform/main.tf` and `terraform/variables.tf`.
- **Reusable TF module** at `modules/cloud-run-service/` handles Cloud Run v2 service, service account, IAM, env vars, and secret references.
- **At deploy time**, gapp stages TF files to `~/.cache/gapp/{solution}/terraform/`, generates only `terraform.tfvars.json`, and runs `terraform init + apply`.

This pattern follows a proven static TF + staging + tfvars.json approach.

### Why Solutions Don't Own TF

TF files are nearly identical boilerplate across solutions. Centralizing TF in gapp means:
- No drift between solution repos
- Framework upgrades benefit all solutions automatically
- Less cognitive load for simple MCP servers

## Container Build Pipeline

### Static Dockerfile with Build Args

gapp generates a Dockerfile at build time from its template (`gapp/templates/Dockerfile`). The Dockerfile uses `ARG ENTRYPOINT` to parameterize the run command. Solutions can provide their own Dockerfile to take full control of the build.

### Source Integrity via `git archive`

Build source is piped from `git archive HEAD` directly into Cloud Build. This ensures the built image contains exactly the committed contents of HEAD — no uncommitted changes, no gitignored files, no working directory artifacts. The HEAD SHA is used as the image tag, making the tag truthful by construction.

### Dirty Tree Guard

`gapp deploy` blocks if the working tree has uncommitted changes. The user must commit or stash before deploying.

### Redundant Build Skipping

Before building, gapp checks if `{image}:{sha}` already exists in Artifact Registry. If it does, the build is skipped entirely. This makes re-deploys fast and free.

### Identity Consistency

A single identity is used across the entire deploy flow. gapp passes a gcloud access token to Terraform via the `GOOGLE_OAUTH_ACCESS_TOKEN` env var, ensuring gcloud CLI and Terraform use the same identity.

## Secrets Security Model

Secret values live in GCP Secret Manager within the project where they're consumed. Key properties:
- **Blast radius isolation** — per-project Secret Manager with its own IAM
- **No secret values in repos** — only secret names (references)
- **Secrets are solution-scoped** — Secret Manager IDs are `{solution}-{name}`, preventing collisions when multiple solutions share a GCP project
- **Per-secret IAM** — each solution's service account gets `secretAccessor` only on its declared secrets, not project-wide. Solutions sharing a GCP project cannot read each other's secrets.

## Code Architecture

### SDK Layer First

All business logic lives in `gapp/admin/sdk/`. CLI and MCP layers are thin wrappers that call SDK functions and format output.

```
gapp/
├── admin/
│   ├── sdk/              # business logic, testable, reusable
│   │   ├── config.py     # XDG-compliant config management
│   │   ├── context.py    # solution detection and resolution
│   │   ├── deploy.py     # build + terraform orchestration
│   │   ├── init.py       # solution initialization
│   │   ├── manifest.py   # gapp.yaml parsing
│   │   ├── mcp_status.py # MCP tool enumeration, connect info, client config
│   │   ├── models.py     # pydantic response models with next_step guidance
│   │   ├── secrets.py    # Secret Manager operations
│   │   ├── setup.py      # GCP foundation provisioning
│   │   ├── solutions.py  # solution listing and discovery
│   │   ├── status.py     # infrastructure health check
│   │   ├── tokens.py     # JWT creation and revocation
│   │   └── users.py      # user registration and credential management
│   ├── cli/              # thin Click wrapper
│   │   └── main.py
│   └── mcp/              # stdio MCP server (gapp_ prefixed tools)
│       └── server.py
├── templates/            # Dockerfile, cloudbuild.yaml
```

If you're writing logic in a CLI command or MCP tool handler, stop and move it to SDK.

SDK operations return pydantic models (for status and MCP operations) or dicts (legacy operations — migration in progress). CLI formats text by default; `--json` dumps `model.model_dump()` directly. MCP tools return `model.model_dump()` for the same structured output.

### Testing Standards

**Sociable unit tests.** No mocks unless testing network I/O. Isolate via temp dirs and env vars.

- **Unit tests (`tests/unit/`):** Fast, local, no network, no credentials. Subprocess only to ubiquitous tools (e.g., `git init`).
- **Integration tests (`tests/integration/`):** Only when explicitly requested. Excluded from default `pytest` run.

Test names describe scenario + outcome, not implementation:
- Good: `test_init_creates_manifest_and_dockerfile`
- Bad: `test_returns_false_when_file_missing`

Run tests: `python -m pytest tests/unit/ -v`

### Per-Secret IAM

Each solution's service account gets `roles/secretmanager.secretAccessor` only on its own declared secrets — not project-wide. This prevents solution A from reading solution B's secrets when sharing a GCP project.

## External Framework Awareness

gapp is a deployment tool. It deploys containers, manages secrets,
and mounts data volumes. It does not know or care what framework
the solution uses — mcp-app, FastMCP, FastAPI, or anything else.

**Code:** gapp must never import, depend on, or bundle any
external app framework. No references in `pyproject.toml`,
`requirements*.txt`, or Python code.

**Skills and documentation:** the deploy skill and README may
mention an external framework parenthetically as an example
(e.g., "solutions handling their own auth, such as mcp-app")
but must never contain framework-specific configuration,
commands, or workflows. The skill describes gapp's capabilities
generically — how to map env vars, secrets, persistent storage,
and service config. It relies on the agent to carry the app's
runtime requirements from the app's own skill or documentation
and map them to gapp's primitives. Neither skill needs to be
intimately aware of the other's details.

Universal tools like Docker are the exception — Docker examples
serve both practical and illustrative purposes and don't create
coupling to a specific app framework.

## Design Principles

### 1. Separate the Tool from the Deployment

**This repo is the tool** — application code, Dockerfile, and `gapp.yaml` live in solution repos. **GCP is the deployment** — project labels, Secret Manager, Cloud Run. **Local machine is ephemeral** — `~/.config/gapp/` is a reconstructable cache.

These tiers must not bleed: no project IDs in public repos, no credential values in any repo, no state that can't be reconstructed from GitHub + GCP.

### 2. Terraform Earns Its Keep

Even simple deployments involve 5-6 interdependent resources (service + SA + IAM + secret refs). Terraform manages the resource graph declaratively. The reusable patterns belong in shared modules; the app-specific configuration belongs in `gapp.yaml`.

### 3. Open/Closed Principle

Adding a solution = adding `gapp.yaml` + Dockerfile to a repo. No editing of existing gapp code or config files.

### 4. Prove Patterns Before Abstracting

Don't build abstractions until you've done the thing manually 2-3 times and felt the actual pain of repetition. Identify what's genuinely common vs. accidentally similar. Three similar lines of code is better than a premature abstraction.

### 5. Config Is a List, Not a Graph

Solutions have no inter-service deployment dependencies. Each is independently deployable.

### 6. Visibility Is a Feature

If you build useful things but nobody can find them, they might as well not exist. GitHub topics (`gapp-solution`) enable discovery. `gapp list` is the live inventory. Every system has one obvious entry point that answers the key question.

### 7. Minimize Places to Look

Every system should have one canonical place for the key question. Before gapp: check TF state per repo, check gcloud per project, check Console. After gapp: `gapp list`, `gapp status`. One command per question.

### 8. Derive, Don't Configure

If a value can be derived from convention, don't require configuration. The only truly unique input is the GCP project ID.

### 9. The CLI Always Knows What's Next

Every status display, error message, and blocking condition ends with the specific next action to take. SDK operations return a `next_step` field so CLI and MCP interfaces can provide the same guidance.

### 10. Solutions Are Independent

Solutions in the same project co-exist without knowing about each other — at deploy time and at runtime. Each has its own TF state, secrets, and services. If two solutions need to communicate at runtime, that's a signal they should be one solution.

### 11. Public Repos Must Not Depend on Private Repos

The test: "Can someone deploy this app to their own GCP project using only public repos?" The answer must be yes — via gapp's CLI and modules and the app repo's `gapp.yaml`, not by reverse-engineering a private repo. Reusable logic belongs in public repos. Private repos should contain only personal data, config, and documentation.

### 12. Credential Security

Credentials are isolated, protected, and never in repos:

- **Blast radius isolation** — per-solution credentials, per-solution service accounts, per-secret IAM
- **Protected by identity provider** — Google account + MFA for admin access
- **Encrypted at rest and in transit** — GCS and Secret Manager handle encryption
- **No credential values in repos** — only references (secret names in `gapp.yaml`)
- **Centrally rotatable** — update upstream credential once via `gapp users update`, all clients keep working without PAT reissue

### 13. Secrets Stay Decentralized

Secret values live in GCP Secret Manager per-project. No central vault, no secret values in repos. Recovery procedures are documented per-secret in `gapp.yaml`.

### 14. Don't Hide Reusable Logic in Private Repos

If code is generic and useful, it belongs in a public repo. Private repos should contain only personal data and configuration. The gapp CLI, Terraform modules, and runtime wrapper are all public. Personal infrastructure decisions live in private repos.

## MCP Admin Server

The `gapp-mcp` entry point runs a stdio MCP server that exposes admin operations as tools. All tools are prefixed with `gapp_` to avoid name collisions.

Available tools:
- `gapp_user` — view or set the global gcloud account and app owner
- `gapp_init` — bootstrap a solution (yaml + GitHub topic + registry)
- `gapp_setup` — GCP foundation (APIs, bucket, project label)
- `gapp_build` — submit an async Cloud Build
- `gapp_deploy` — run terraform apply
- `gapp_status` — infrastructure health check
- `gapp_list` — list registered solutions
- `gapp_secret_set` / `gapp_secret_get` / `gapp_secret_list` — manage gapp-owned secrets
- `gapp_schema` — live gapp.yaml JSON schema
- `gapp_ci_*` — GitHub Actions CI/CD wiring
- `gapp_deployments_list` — projects with deployed solutions

Each tool calls the same SDK function the CLI uses and returns the same structured result. Register with Claude Code:

```bash
claude mcp add --scope user gapp-admin gapp-mcp
```

## Version Management

**Single source of truth:** `gapp/__init__.py` contains `__version__` and `MIN_SUPPORTED_MAJOR`. Git tags match the version with a `v` prefix (e.g. `v3.0.0` for `3.0.0`).

### Label contract versioning

The gapp **major version IS the contract version**. Bumping major == breaking the contract. The label sentinel `v-N` written into project labels at deploy time is derived directly from `__version__` major:

```python
from gapp import __version__
label_value = f"v-{int(__version__.split('.')[0])}"   # 3.0.4 → "v-3"
```

A 3.x build stamps `v-3`. A future 4.x build stamps `v-4`. There is no separate `CONTRACT_VERSION` constant — multiple version numbers create complexity and drift. One number, one source.

`MIN_SUPPORTED_MAJOR` is the read-floor — the oldest contract this build can manage. Setup/deploy gating policy:

| Project's contract | Action |
|---|---|
| `n > __version__` major | Refuse writes — "deployed by newer gapp; upgrade." Read ops still work. |
| `n < MIN_SUPPORTED_MAJOR` | Refuse writes — "deployed by unsupported gapp; migrate manually or use older build." Read ops still work. |
| `MIN_SUPPORTED_MAJOR ≤ n ≤ current major` | Allow. On write, restamp to current `v-N`. |

Read operations (`gapp list`, `gapp status`) never gate — they show all `gapp_*` labeled projects regardless of contract version, with the parsed contract major reported as a structured field.

**Default policy is `MIN_SUPPORTED_MAJOR == __version__` major** — a hard cutover at every major bump. Carrying older contracts forward (e.g., `MIN_SUPPORTED_MAJOR = N-1`) is opt-in and requires that the SDK actually still supports the older shape. Don't lower the floor unless backward compatibility is intentionally implemented and tested.

### What counts as a major bump

Anything that changes the *contract* between deployed projects and the gapp build that manages them:

- Solution label key format
- Solution label value format (the `v-N[_…]` shape)
- Bucket naming convention
- Secret naming convention
- Terraform state path layout
- Role label format

If a project deployed by an older gapp would become unmanageable by the newer gapp without manual intervention, that's a major.

### Release workflow

```bash
# 1. Update __version__ in gapp/__init__.py
# 2. Update version in pyproject.toml to match
# 3. Commit
git add gapp/__init__.py pyproject.toml
git commit -m "chore: bump version to X.Y.Z"

# 4. Tag
git tag vX.Y.Z

# 5. Push (with tags)
git push && git push --tags
```

### Why version bumps matter

- `pip install --upgrade` only installs if the version number is higher. Same version = pip thinks nothing changed.
- The label sentinel `v-N` derives from `__version__` major. Forgetting to bump major on a contract-breaking change means the new code stamps the same `v-N` as the old code — silently incompatible deployments. Always bump major when the contract changes.
- Editable installs (`pip install -e .`) always use live code regardless of version, so day-to-day development isn't gated by version bumps.

For development, use editable install to avoid version concerns:
```bash
pipx install -e .   # or: pip install -e .
```

## Label Model: Two Labels, Two Concerns

gapp uses two distinct GCP project label families. They serve different purposes and must not be conflated.

### Solution label (deploy record)

One label per actual deployment. Records that a specific project hosts a specific solution for a specific owner in a specific env.

| Field | Format | Notes |
|---|---|---|
| **Key** (owned) | `gapp_<owner>_<solution>[_<env>]` | env appended only when non-default |
| **Key** (global / no owner) | `gapp__<solution>[_<env>]` | double underscore is the no-owner sentinel |
| **Value** | `v-<contract-major>` | e.g., `v-3` — purely the contract version |

A project can carry many solution labels — one per (solution × env) combination it hosts, across multiple owners. Solution labels are the source of truth for what's actually deployed where.

The env lives in the KEY (where it makes the label uniquely identifying), not the VALUE (which carries only the contract version).

### Role label (default-target hint)

Per-owner routing preference for new deployments. This is convenience metadata, not a constraint.

| Field | Format | Notes |
|---|---|---|
| **Key** (owned) | `gapp-env_<owner>` | one per owner per project |
| **Key** (global / no owner) | `gapp-env` | bare key, no owner segment |
| **Value** | `<env>` | e.g., `prod`, `default`, `dev` |

Says: *"For owner Y, when `gapp setup --env Z` is run without `--project`, default to this project."* It does **not** bind the project to env Z. A project tagged `gapp-env_alice=prod` can still host alice's `dev` solutions if explicitly targeted, and can simultaneously host bob's `staging` solutions for owner bob.

### Why two labels?

Deploy-records and routing-defaults are independent concerns. Stripping env from solution labels and trying to derive it from role labels would force one project per owner per env — eliminating the ability to mix deployments and breaking the entire mental model. The two-label design is load-bearing.

## Label Keyspace

Deliberate prefix-query partitioning enables O(1) `gcloud projects list --filter=labels:<prefix>*` lookups with zero post-filter parsing.

| Filter prefix | Matches |
|---|---|
| `labels:gapp_*` | All solution labels (any owner, including global) |
| `labels:gapp__*` | Global-namespace solutions ONLY (no owner) |
| `labels:gapp_<owner>_*` | One specific owner's solutions ONLY |
| `labels:gapp-env*` | Role labels (project-role-per-owner) |

Two design choices make this work:

**Prefix-sentinel separation.** The `gapp-env` hyphen-prefix and `gapp_` underscore-prefix never collide. Solution labels use `_` everywhere; role labels use `-` after `gapp`. A single prefix query can target one keyspace without sweeping in the other — no reserved owner names required.

**Double-underscore for empty owner.** `gapp__<solution>` (two underscores between `gapp` and the solution name) is the no-owner sentinel. It preserves positional regularity for prefix matching: `gapp_<X>_<Y>` always parses as 3 segments, where `<X>` empty means global. This is intentional — it lets `labels:gapp__*` match global-namespace solutions exclusively without parsing every label value, and it avoids reserving "global" or any other word as a forbidden owner name.

A single API call returns full label dicts for matching projects. Multiple solutions per project (owner-namespaced or global) all arrive in one response — `gapp list` is O(1) network calls regardless of how many deployments per project.

## CI/CD and Remote Deployment

gapp is designed to work without a local machine. The three-layer model — tool (gapp), application (solution repo), and operator config (private repo) — enables deployment from GitHub Actions, Codespaces, or any stateless environment using Workload Identity Federation for keyless GCP authentication.

See [docs/CI.md](docs/CI.md) for the full design: authentication architecture, the operator repo pattern, CLI design decisions, and what changes are needed in gapp.
