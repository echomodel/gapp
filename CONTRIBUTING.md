# Contributing to gapp

This document covers the design, architecture, and principles behind gapp. Read this before contributing code.

---

## What gapp Does

gapp is a CLI tool that deploys Python MCP servers to Google Cloud Run with Terraform. A developer adds a `gapp.yaml` to their repo and runs four commands:

```
gapp init                            # scaffold gapp.yaml + Dockerfile, register locally
gapp setup --project <project-id>    # enable GCP APIs, create state bucket, label project
gapp secret set <name>               # populate secrets in Secret Manager
gapp deploy                          # build container, terraform apply
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
  gapp/admin/sdk/core.py                ← orchestrates build + TF apply

SOLUTION REPO (what gets deployed)
  gapp.yaml                             ← solution metadata, prerequisites
  Dockerfile                            ← optional; if absent, gapp generates one at build time

GCP (runtime state)
  Project labels (written only by an Owner):
    gapp=hosted                         ← the project hosts gapp deployments
  Deployment labels (on the bucket and every Cloud Run service):
    gapp-deployment=<id>  gapp-version=<major>
  Secret Manager: labeled gapp-solution=<name>  ← every gapp-managed secret
  Cloud Run: running services           ← runtime
  GCS: gapp-{name}-{project-id}/       ← per-solution bucket
    terraform/state/                    ← TF state (not visible to container)
    data/                               ← app data (FUSE-mounted at /mnt/data via only-dir=data)
  Cloud Build: container image builds   ← no local Docker needed
  Artifact Registry: gapp/ repo         ← container images

LOCAL (~/.config/gapp/, working cache, fully reconstructable)
  config.yaml                           ← profiles: account, discovery, fleet repo (an `owner:` key is an error)
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

Solutions are discovered via GitHub repos and topics, not GCP project configurations. GitHub is more durable and discoverable than GCP for this purpose — repos have READMEs, topics, and are browsable. GCP labels (`gapp=hosted` on the project, `gapp-deployment=<id>` on the deployment's bucket and services) are the secondary source, used to map a solution to its GCP project. Local config (`~/.config/gapp/config.yaml`) is a working registry reconstructable from GitHub + GCP.

gapp is GitHub-flavored but not GitHub-locked. The core lifecycle — `gapp init`, `gapp setup`, `gapp secret set`, `gapp deploy` — works with any local git repo and requires no GitHub account, no GitHub API, and no GitHub Actions. GitHub is required only for optional features: remote discovery (`gapp list --available`), CI/CD automation (`gapp ci`), and installing the runtime wrapper during container build. The CI layer calls `gapp deploy` — not the other way around.

## Solution Lifecycle Phases

| Phase | What | Command |
|-------|------|---------|
| **Foundation** | Enable APIs, create and label the bucket, mark the project `gapp=hosted` | `gapp setup --project <project-id>` |
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
- **Secrets require an explicit `name`** — the `name` field under `secret:` is the short name in Secret Manager. gapp prefixes it with the solution name: `name: app-key` on solution `my-app` → `my-app-app-key` in Secret Manager. No auto-derivation from the env var name.
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
- **A domain change is destructive, so deploy guards it** — Terraform replaces a changed `google_cloud_run_domain_mapping` by destroying the old mapping first. `gapp deploy` therefore reads the live mapping from Terraform state before building and refuses to replace or remove it unless `--replace-domain` is passed, and refuses to create a mapping for a domain the deploying identity has not verified (Cloud Run would reject it after the old mapping is gone). A redeploy that keeps the same domain is unaffected. The fleet entry's keys (`gapp ci setup --domain`, `--config`) override gapp.yaml at deploy time, which is how a public repo keeps its real domain out of the repo.
- **The domain guard fails closed** — when the Terraform state cannot be read, `gapp deploy` refuses before building, even with `--replace-domain`. An unreadable state is never taken for "no mapping", and a failed domain-mapping describe is never taken for a missing mapping; only GitHub's or gcloud's own not-found answer means absent.
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
│   │   ├── core.py       # GappSDK class — all setup/deploy/list/resolve
│   │   ├── config.py     # XDG-compliant profile config management
│   │   ├── init.py       # solution initialization
│   │   ├── manifest.py   # gapp.yaml parsing
│   │   ├── models.py     # pydantic response models with next_step guidance
│   │   ├── schema.py     # gapp.yaml Pydantic schema (single source of truth)
│   │   ├── secrets.py    # Secret Manager operations
│   │   ├── ci.py         # the CI fleet system (gapp ci), GitHub only
│   │   ├── fleet.py      # the fleet file's pure operations (entries, ids, the overlay merge)
│   │   ├── util.py       # subprocess + git helpers
│   │   └── cloud/        # provider abstraction (gcp.py, dummy.py)
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

**Skills and documentation:** the gapp skill and README may
mention an external framework parenthetically as an example
(e.g., "solutions handling their own auth, such as mcp-app")
but must never contain framework-specific configuration,
commands, or workflows. The skill describes gapp's capabilities
generically — how to map env vars, secrets, persistent storage,
and service config. It relies on the agent to carry the app's
runtime requirements from the app's own skill or documentation
and map them to gapp's primitives. Neither skill needs to be
intimately aware of the other's details.

The skill lives in `skills/gapp/` with its references one level
down. Its eval prompts and assertions are in `skills/gapp/evals/evals.json`,
with fixture repos under `evals/files/`. After changing the skill, rerun
them with the skill-creator loop (each prompt against the new and the
old skill, graded against the assertions) before releasing.

Universal tools like Docker are the exception — Docker examples
serve both practical and illustrative purposes and don't create
coupling to a specific app framework.

## Avoiding Product Coupling

Code, comments, documentation, skills, unit tests, and test fixtures
in this repo must not use illustrative examples drawn from co-owned
frameworks (e.g., `mcp-app`) or co-owned apps that gapp happens to
deploy (e.g., a specific MCP server). Examples for env var names,
secret short names, and similar configuration must be generic
enough that a reader unfamiliar with our other products would not
infer any of them as a load-bearing concept of gapp itself.

The canonical forbidden example — cited here as the reference for the rule:

| Forbidden example | Reason | Use instead |
|---|---|---|
| env var `SIGNING_KEY` / secret short name `signing-key` | Specific to a co-owned framework's session-signing scheme (`mcp-app`). Not a gapp concept. | Generic placeholders: `APP_KEY` / `app-key`, `API_TOKEN` / `api-token`, `DB_PASSWORD` / `db-password`, etc. |

Solution names of specific deployed instances are also out of bounds
as illustrative examples. Use placeholder names like `my-app`,
`my-svc`, `parent-app` instead.

The forbidden names above must not appear anywhere in the repo
*except* this single section, where they are cited as the reference
for the rule. That includes Python source, code comments, markdown
documentation, skill files, unit tests, manifest fixtures, and
CLI/help strings.

The reasoning is the same as the `External Framework Awareness`
section above, applied one level lower: leaking another product's
naming into gapp's examples implies gapp knows something about that
product. It does not. Every example used in this codebase should
read as if gapp had been written before any of our other products
existed.

When adding a new example, ask: would this name still make sense
in a hypothetical fork of gapp deployed inside an unrelated
organization that has never heard of our other tools? If yes, use
it. If no, pick something more generic.

## Design Principles

### 1. Separate the Tool from the Deployment

**This repo is the tool** — application code, Dockerfile, and `gapp.yaml` live in solution repos. **GCP is the deployment** — labels, Secret Manager, Cloud Run. **Local machine is ephemeral** — `~/.config/gapp/` is a reconstructable cache.

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

### 15. Bounded Queries Over Project-Wide Scans

gapp never enumerates "all secrets in a project" or "all resources of
type X in a project." Every read is either:

- **Label-filtered**, e.g. `gcloud secrets list --filter labels.gapp-solution=<sol>` — server-side filter, scales with matches not project total, and is gapp's only enumeration shape.
- **Addressed by ID**, e.g. `gcloud secrets describe <secret_id>` — O(1) per call, no scan.

This is intentional and must be preserved. Reasons:

1. **Cost discipline** — projects can host hundreds or thousands of secrets that have nothing to do with any single solution. Scanning them all on every `gapp secrets list` would be wasteful and slow at the wrong moments (pre-deploy validation, status checks).
2. **No lateral exposure** — a label-filtered or ID-addressed read returns only what gapp owns or what gapp explicitly asked for. An unfiltered list would surface neighboring solutions' secret IDs in the API response, even if gapp ignored them. Bounded queries minimize that exposure.
3. **Predictable latency** — call cost scales with the solution's own declared surface, not the project's history.
4. **Graceful degradation under scale** — if a project ever does grow to a five-figure secret count, gapp's behavior is identical to a fresh project. No latency cliff, no pagination loop, no rewrite needed.

When adding new operations, never reach for an unfiltered list of project-wide resources. Use the existing label or ID-addressed paths, or extend them with a new label. If a future operation genuinely needs a project-wide scan, that needs explicit design discussion — it is not the default shape.

### 16. gapp Is Not a Git Client for the Operator's Repos

gapp never clones, commits to, or pushes an operator's repository on their
behalf. Generated files gapp owns (the three workflows and the deployments
file of the fleet repo) are written through the GitHub API as config-store
updates, by `ci init` from the operator's machine and by the register run's
own token from the runner; anything a human authors goes through the
operator's own commit process and hooks. A tool that commits in the
operator's name with hooks disabled is defeating controls the operator
chose. See docs/CI.md, "Why gapp's files are written through the API, not
git."

## MCP Admin Server

The `gapp-mcp` entry point runs a stdio MCP server that exposes admin operations as tools. All tools are prefixed with `gapp_` to avoid name collisions.

Available tools:
- `gapp_user` — view or set the global gcloud account
- `gapp_init` — bootstrap a solution (yaml + GitHub topic + registry)
- `gapp_setup` — GCP foundation (APIs, labeled bucket, `gapp=hosted` on the project).
- `gapp_deploy` — build + terraform apply
- `gapp_status` — infrastructure health check
- `gapp_list` — list the deployments on projects marked `gapp=hosted`
- `gapp_secret_set` / `gapp_secret_get` / `gapp_secret_list` — manage gapp-owned secrets
- `gapp_schema` — live gapp.yaml JSON schema
- `gapp_ci_init`, `gapp_ci_sources`, `gapp_ci_project_setup`, `gapp_ci_setup`, `gapp_ci_rename`, `gapp_ci_deploy`, `gapp_ci_status`, `gapp_ci_list` — the CI fleet system (GitHub only)

Each tool calls the same SDK function the CLI uses and returns the same structured result. Register with Claude Code:

```bash
claude mcp add --scope user gapp-admin gapp-mcp     # Claude Code
agy mcp add gapp-admin -- gapp-mcp                   # Antigravity CLI
```

## Version Management

**Single source of truth:** `gapp/__init__.py` contains `__version__`. Git tags match the version with a `v` prefix (e.g. `v3.0.0` for `3.0.0`).

### Label contract versioning

The gapp **major version IS the contract version**. Bumping major == breaking the contract. Every setup and deploy writes `gapp-version=<major>` on the deployment's bucket and Cloud Run services, derived directly from `__version__` major (`CURRENT_MAJOR` in `core.py`). There is no separate `CONTRACT_VERSION` constant — multiple version numbers create complexity and drift. One number, one source.

Setup/deploy gating reads `gapp-version` from the deployment's own services (both modes), never from a project label:

| Service's `gapp-version` | Action |
|---|---|
| `n > __version__` major | Refuse — "Deployment '<id>' on project '<p>' was deployed by gapp n.x ...; upgrade gapp to deploy it." Read ops still work. |
| `n ≤ __version__` major, absent, or no service yet | Allow. The deploy writes the current major. |

There is no lower floor: 4.x is the first major whose labels live on the deployment's resources, so a missing `gapp-version` means "new, or not yet redeployed by 4.x". Read operations (`gapp list`, `gapp status`) never gate; `gapp list` reports each deployment's major as a structured field.

**4.x is a clean break from the 3.x project labels** (`gapp__<name>=v-N`, `gapp_<owner>_<name>=v-N`): no 4.x code reads or writes them (`tests/unit/test_label_model.py` guards that).

### What counts as a major bump

Anything that changes the *contract* between deployed projects and the gapp build that manages them:

- Deployment label keys and values (`gapp-deployment`, `gapp-version`) and the project mark (`gapp=hosted`)
- Bucket naming convention
- Secret naming convention
- Terraform state path layout
- Role label format

If a project deployed by an older gapp would become unmanageable by the newer gapp without manual intervention, that's a major.

### Where the version actually lives

`gapp/__init__.py` is the single source of truth for the version
string. Two derived locations track it:

| File | Mechanism |
|---|---|
| `gapp/__init__.py` | **Source.** `__version__ = "X.Y.Z"`. |
| `pyproject.toml` | **Auto-derived.** Declares `dynamic = ["version"]` and reads `gapp.__version__` via `[tool.setuptools.dynamic]`. Never edit the version directly here — there isn't one to edit. |

### Release workflow

A release is one local prep command plus two publish steps. The local
prep is automated via `scripts/release.py` so the version files can
never drift and the test suite is never skipped.

#### 0. Changelog

Move the entries under `## Unreleased` in `CHANGELOG.md` to a `## X.Y.Z`
heading and commit that with the last change going into the release (or
on its own), so the version bump below lands on a changelog that already
names the version.

#### 1. Local prep (one command)

```bash
scripts/release.py X.Y.Z          # a release
scripts/release.py X.Y.ZrcN       # a release candidate (PEP 440; tags as vX.Y.ZrcN)
```

A release candidate is spelled the PEP 440 way, `X.Y.ZrcN`, because the
tag is `v` plus the version string and `gapp ci init` pins the fleet
repo's workflows to exactly that tag; a tag spelled differently from
`__version__` would leave the pin dangling. The changelog section for an
rc is headed `## X.Y.ZrcN (pre-release of X.Y.Z)`.

This script:
- validates that the new version is strictly greater than the current
  `__version__` (an rc orders before its final release),
- requires a clean working tree,
- updates `gapp/__init__.py` (`pyproject.toml` auto-tracks via dynamic
  version),
- runs `python3 -m pytest tests/unit/` and aborts on red,
- commits that edit with `chore: bump version to X.Y.Z`
  (the precommit privacy hook still runs),
- tags `vX.Y.Z` at the new commit,
- prints the two remaining publish steps below.

If anything fails, the script aborts before tagging. Re-run after
fixing.

#### 2. Push the commit and the tag

```bash
git push origin main && git push origin vX.Y.Z
```

Every released version gets a tag; untagged versions break pinned
installs.

#### 3. Reinstall the CLI at the tag

On each machine that uses gapp:

```bash
cd ~ && pipx install --force "gapp @ git+https://github.com/echomodel/gapp.git@vX.Y.Z"
```

Run from `$HOME`, not from inside the gapp repo. From the repo
directory, pipx parses a bare `gapp` argument as the `./gapp/`
directory and errors out. After this, `gapp --version` reports the
new version, and `gapp-mcp` on that machine serves the new code.

The gapp skill under `skills/` is not part of the pip package. When it
changes, re-save it wherever it is distributed. There is no plugin
packaging: the CLI and the skill carry every operation, and anyone who
wants the typed MCP tools on a workstation registers `gapp-mcp` with
their client as shown under "MCP Admin Server".

### Why version bumps matter

- `pip install --upgrade` only installs if the version number is
  higher. Same version = pip thinks nothing changed and silently
  keeps the old code.
- The `gapp-version` label derives from `__version__` major.
  Forgetting to bump major on a contract-breaking change means the
  new code writes the same `gapp-version` as the old code — silently
  incompatible deployments. Always bump major when the contract
  changes.
- Editable installs (`pip install -e .`) always use live code
  regardless of version, so day-to-day development isn't gated by
  version bumps.

For development, use editable install to avoid version concerns:

```bash
pipx install -e .   # or: pip install -e .
```

## No owner namespaces

gapp 4.0 removed owner namespaces (`gapp config owner`, `gapp list --all`, setup's cross-owner check and its `--force`). A deployment's id is unique within its project, and its bucket and service names derive from it. Teams that must not share deployments separate by project and IAM, or by fleet. gapp does not accept configuration it does not understand: an unknown key in the user config (top level or in a profile; `owner:` has its own message) is an error naming the file and the key, never ignored. A deployment entry's gapp.yaml keys go through the same gapp.yaml validator (`validate_manifest`, `extra="forbid"`) when `gapp ci setup` writes them and when `gapp ci status` reads them.

## No environments

gapp 4.0 removed environments (the `gapp-env` project label, `gapp projects set-env/clear-env/list`, and `--env` on setup, deploy and status). A deployment is the unit: its own name, project and config. The same app on a test project and a production project is two deployments, addressed by `--project` (or, in CI, two fleet entries made with `gapp ci setup <new> --copy-of <existing>`). No gapp code reads or writes `gapp-env`; a test guards it.

## Deployment labels

Every setup and deploy, in both modes, writes the same labels on the deployment's own resources: its bucket (by gapp, since the bucket holds Terraform's own state) and every Cloud Run service (by Terraform, through the module's required `labels` variable):

| Label | Value | Notes |
|---|---|---|
| `gapp-deployment` | `<id>` | the deployment's id; a workspace's services all carry the parent id |
| `gapp-version` | `<major>` | e.g. `4`, from `__version__`; the version guard reads it from the services |

The project carries one gapp label: `gapp=hosted` (the project hosts gapp deployments). Only an Owner writes project labels: `gapp setup` marks the project once from a workstation, and `gapp ci project setup` marks it for a CI fleet. A CI run (`GITHUB_ACTIONS=true`) never writes a project label, so the deploy identity needs no project-update permission.

A project can host multiple deployments; each id is unique within it.

## Finding a deployment

- **`--project` given** (always, in CI: the project comes from the fleet entry): no search and no project-label read. "Is it set up" is the deployment's bucket existing (deploy refuses with "Foundation missing" otherwise).
- **No `--project`**: list the projects marked `gapp=hosted` (one Resource Manager call), then ask each for its buckets labeled `gapp-deployment=<id>`, in parallel. Buckets rather than services, because a deployment has its bucket from setup on, before its first service exists. Every command stops searching at the second match: one match is the deployment, and a second means the name is ambiguous, refused with "'<name>' exists on more than one project. Pass --project to choose one; `gapp list` shows where each is." Matches are never counted or listed.
- **`gapp list`**: the same hosted-project listing, then every labeled deployment bucket per project, in parallel; major from `gapp-version`. The same name on two projects is two rows.

A very large organization could replace the per-project calls with one Cloud Asset Inventory search; it needs an organization scope, the Cloud Asset API and its index lags by minutes, so it is not used.

## Resolution rules

Every solution-keyed command of the direct system (`setup`, `deploy`, `status`, `secret get/set/list`) routes through `GappSDK.resolve_project_for_solution(solution, project=None)`. The CI system (`gapp ci`) never calls it: its project comes from `--project` and its record is the fleet repo (see "Two fleet systems"). Inputs: solution name (from gapp.yaml or arg), optional `--project`.

| Deployments found on hosted projects | `--project` | Outcome |
|---|---|---|
| — | given (P) | Proceed with P, no search and no project-label read. Deploy then needs the bucket ("Foundation missing" otherwise). |
| 1 | omitted | Proceed. |
| 0 | omitted | Error: "No deployment '<id>' found in any project marked gapp=hosted. Pass --project, or run `gapp setup --project <project-id>` first." |
| 2+ (search stops at the second) | omitted | Refuse: "'<id>' exists on more than one project. Pass --project to choose one; `gapp list` shows where each is." |

## What writes what

| Command | Writes deployment labels | Writes `gapp=hosted` | Writes tfstate identity (planned) |
|---|---|---|---|
| `gapp setup` | bucket (idempotent) | once per project, from a workstation; never from a CI run | initial value on first apply (Layer 2) |
| `gapp deploy` | bucket and services (current major) | **never** | rewrites if `--takeover`, else verifies (Layer 2) |
| `gapp ci project setup` | never | yes (as an Owner) | never |

This table is load-bearing: prior versions of gapp had setup/deploy quietly stamping role labels (`gapp-env_<owner>`), and conflating "deploy record" with "default-target hint" drove much of the redesign that landed in v-3. Each command now has exactly one write responsibility. Don't add hidden side-effects.

## CI/CD and Remote Deployment

gapp is designed to work without a local machine. The three-layer model — tool (gapp), application (deployed repo), and operator config (the fleet repo) — enables deployment from GitHub Actions, Codespaces, or any stateless environment using Workload Identity Federation for keyless GCP authentication.

See [docs/CI.md](docs/CI.md) for the full design: authentication architecture, the fleet repo, the deployments file, CLI design decisions and the security model.

## Two fleet systems

gapp has **two fleet systems that do not talk to each other**:

- **Root commands** (`gapp setup`, `deploy`, `status`, `list`, `secrets`)
  deploy the local checkout to GCP with gcloud. Their record of where a
  solution lives is its labels in GCP (`gapp=hosted` on the project,
  `gapp-deployment` on its bucket and services). They never read GitHub.
- **`gapp ci` commands** manage the fleet through GitHub only. They never
  use gcloud (except `ci project setup`, once per project) and never read
  the working directory. Their record is `gapp/deployments.yaml` in the
  fleet repo and the project's GitHub Environment.

**No command consults the other system's state**, there is no shared
local store, and no `--force` reconciliation. Every command's requirements
are fixed by its name; nothing is conditional on hidden state. Labels
remain the direct system's running-state marker; the fleet repo remains
the CI system's intent log; neither is promoted to authority over the
other.

**Parity rule.** The `ci` group mirrors the root group's verbs and
meanings wherever both have the operation:

| Root | `ci` | Same meaning | Where they differ, and why |
|---|---|---|---|
| `gapp setup --project <p>` | `gapp ci setup [<name>] --repo <o>/<r> --project <p>` | make one app deployable to one project | `ci` names the repo, because it never reads the working directory; root setup runs in the app's checkout |
| `gapp deploy` | `gapp ci deploy <name>` | deploy it | `ci` takes a name for the same reason |
| `gapp status [<name>]` | `gapp ci status <name>` | one deployment's state and next step | the root name defaults to the checkout's; `ci` requires it |
| `gapp list` | `gapp ci list [--repo <o>/<r>]` | the fleet | `ci` lists the fleet file; root lists the deployments on hosted projects |

`test_ci_rules.py::test_the_ci_group_mirrors_the_root_verbs` asserts the
names and the marker sentence in each `ci` command's help.

### The fleet

The **fleet repo** (the term replaces "CI repo" everywhere) holds gapp's
three workflows (deploy, register, status) and `gapp/deployments.yaml`: one entry per **deployment**,
keyed by an operator-chosen name, flat shape, no nested block. `id`,
`repo` and `project` are the entry's own fields; **every other key is a
gapp.yaml key**, deep-merged over the repo's gapp.yaml by the deploy run's
overlay step, or written as the whole file when the repo has none
(`fleet.merge_manifest`). gapp core is unchanged. `id` keys every GCP
resource and is passed to the runner as the solution name; it defaults to
the deployment name, takes the next free variant (`<name>-2`, ...) when
another entry on the same project uses it, is at most 27 characters
(service account `<id>-sa` ≤ 30), is never copied, and shows only with
`--verbose`. Environments and instances are more entries for the same
repo (`ci setup <new> --copy-of <existing>`); there is no first-class
environment concept. `fleet.py` holds these as pure functions; the
operator's machine runs them before dispatching and the register run runs
them again against the live file (`ci.runner_register`), so both refuse
the same things.

### Rules of the CI system

Each rule is deliberate, recorded here so it is not regressed by a later
change that looks like a convenience, and guarded by the named test.

1. **Only `ci init` sets the fleet repo**, stored in gapp's local config
   (the active profile's `fleet_repo`). No other command looks it up,
   caches it or writes it; without one, every `ci` command stops with
   "No fleet repo is set on this machine. Start here: gapp ci init (It
   finds your existing fleet repo, or tells you how to create one.)".
   A command that silently found the repo by topic would make "which
   fleet am I in?" depend on GitHub's answer of the moment.
   Guard: `test_ci_rules.py::test_every_ci_command_without_a_fleet_repo_stops_with_the_one_message`.
2. **`ci` never reads the working directory.** The local checkout is the
   root system's concern; the `ci` system is remote only. Reading the cwd
   to guess a name or repo implies local state matters when it does not,
   and in a cloud session the checkout may not be the app. `ci setup`
   takes `--repo`, `ci deploy`/`status` take the name.
   Guard: `test_ci_rules.py::test_every_ci_command_gives_the_same_result_from_any_directory_and_only_invokes_gh`
   (an unrelated directory, another app's checkout, and a non-git
   directory give identical results and only `gh` is invoked) and
   `test_the_ci_module_has_no_working_directory_or_git_dependency`.
3. **Output leads with the deployment and what happened**, then
   `Next: <exact command>`. The fleet repo, files, topic, variables and
   `id` appear only with `--verbose` or when they are the cause of a
   problem. No checkmark lists of internal bookkeeping in default output.
   Applies to every `ci` command; recorded as the rule for the root
   commands too, to be applied there in a later change.
   Guard: `test_ci_cli.py` (one test per reference output).
4. **Every failure is a plain sentence with the exact fix**, including the
   two verbatim one-time messages (project not trusted; owner not
   allowed, with the `gh variable set ... --body <current-list>,<owner>`
   alternative and `<current-list>` literal) and the three fleet-repo
   messages. The story from nothing to first deploy is in `gapp ci
   --help` and the README, each step's output naming the next.
   Guard: `test_ci_setup.py`, `test_ci_init.py`, `test_ci_rules.py::test_ci_help_tells_the_from_nothing_to_first_deploy_story`.
5. **"Missing" only when GitHub says so.** Something is reported missing
   only when GitHub's answer confirms it does not exist. Otherwise:
   "Couldn't check <thing>: GitHub returned <status>: <GitHub's own
   message>", trimmed, plus which request failed; no mapping table, no
   assumed cause. Because GitHub returns 404 both for missing and for
   not visible, a 404 on a repo is one clause: "it doesn't exist, or your
   GitHub login (<user>) can't see it"; a 404 on a file or Environment of
   a repo that itself answers is a confirmed absence.
   Guard: `test_ci_rules.py` ("could not check" tests) and
   `test_ci_deploy_status_list.py::test_status_reports_unchecked_variables_and_runs_instead_of_missing`.
6. **Names are unique across the fleet file** (they are its keys), so
   `ci deploy <name>` always means one deployment.
   Guard: `test_fleet.py`.
7. **`ci status` is fast by default and asks the project only on
   request.** `gapp ci status <name>` is GitHub only (the entry, the last
   deploy run with its link, any problem) and ends with "For the live
   service state: gapp ci status <name> --live". `--live` dispatches the
   fleet repo's read-only status workflow, which authenticates exactly as
   a deploy does (the project's Environment, WIF), runs `gapp status` for
   the deployment's id and reports the result, which the command prints
   like the root `gapp status`. **No recorded status file**: live state is
   asked of the project, never stored in the fleet repo, so
   `deployments.yaml` stays intent only.
   Guard: `test_ci_deploy_status_list.py` (the hint; the live round trip
   with nothing written) and `test_ci_workflows.py::test_status_workflow_is_read_only_and_asks_the_project_as_the_deploy_identity`.
8. **REST only.** Every GitHub call `gapp ci` makes is a REST call through
   `gh api`. Higher-level `gh` subcommands (`workflow run`, `run view`,
   `run watch`, `repo create`, `repo edit`, `search`) reach for GraphQL,
   which some sessions (a Claude Code cloud session among them) refuse;
   `gh workflow run` failed there resolving the default branch. Run logs
   are read per job and only the lines a step printed count, never the
   script GitHub echoes into the log.
   Guard: `test_ci_rules.py::test_the_ci_module_calls_github_only_through_gh_api`,
   the fake GitHub in `tests/unit/conftest.py` (it fails any non-`gh api`
   call), and `test_ci_deploy_status_list.py::test_emitted_messages_skip_the_echoed_script_and_keep_what_the_step_printed`.
9. **`--json` on every `ci` command** prints the command's result dict,
   the same one the MCP tool returns, and nothing else; the exit code is
   unchanged. Recorded as the rule for the root commands later.
   Guard: `test_ci_cli.py::test_json_prints_the_sdk_result_exactly_for_every_ci_command`.

Other guards from the design: the deployment's id governs over the repo's
gapp.yaml name (`test_ci_rules.py::test_runner_overlay_merges_over_the_repo_file_or_writes_it_whole`);
the overlay merge supplies, overrides and leaves alone, and a repo with no
gapp.yaml deploys from the entry (`test_fleet.py`); two deployments of one
repo on two projects and on one project are independent; id default,
next-free variant and 27-character limit; the name default from `--repo`;
the repo-reuse refusal with both ways on; `--copy-of` contents and echo;
rename keeps the id (`test_fleet.py`, `test_ci_setup.py`).

### Why the allowed sources are a repository variable

The GitHub owners whose repos a fleet may deploy are listed in the fleet repo's Actions variable `GAPP_ALLOWED_SOURCES`, not in a file. Only someone who can change the repo's settings can change a variable. A commit cannot, so neither can the register workflow (its token writes contents only), an agent session, or anyone with only push access. The register workflow checks the list before it writes an entry. The deploy workflow checks it again before it deploys, because `gapp/deployments.yaml` is a file in git: anyone who can push can add or edit an entry by hand and skip the register checks. Both checks read the variable from the workflow's `vars` context, so they cost no API call and no extra token scope.

### Deferred on purpose

Recorded as deferred, not overlooked:

- Deployments spanning several repos (the entry shape does not preclude a
  later `repos:` list).
- Environments as a first-class concept.
- Binding to or importing resources gapp did not create; a `--id` flag.
- Setting secrets through CI (`gapp ci secrets set <deployment> <name>`:
  stdin or hidden prompt, never an argument; a GitHub Environment secret
  that a workflow writes to Secret Manager). Also needed because `gapp
  secrets set` cannot find declared secrets for a fleet-configured
  deployment with no local gapp.yaml. A separate ticket.
- Collision when the same name is also deployed to the same project from
  a laptop with the root commands (a custom domain maps to one service,
  so that second mapping is refused; otherwise the rule is documented,
  not enforced).
- Renaming "solution" in gapp core (labels and buckets keep the solution
  name = the deployment's id).
- The output rule and `--json` for the root commands.

### How we got here

Before 4.0.0, `ci setup` registered a solution by dispatching a register
workflow but also carried the one-time per-project bootstrap (WIF pool,
provider, deploy account, binding, the project's GitHub Environment)
inside the same command, so it needed gcloud on the first run for a
project and never afterwards. A command whose dependencies change with
hidden state is hard to reason about and to design around, and it made
the question "which machine can run this?" unanswerable from the
command's name. It also read the working directory for the solution's
name and repo, so the same command meant different things in different
folders, and the fleet file was a list keyed by whatever name the repo's
gapp.yaml happened to declare, which made a second instance of one repo
impossible without editing that repo. Separately, `gapp status` with no
GCP identity crashed with a raw attribute error, the allowlist variable
was set by hand under a name nobody should have to remember, and a
refused GitHub read was reported as a missing file.

Considered and rejected:

- **(a) A per-machine XDG record of solution to project/system, with a
  `--force` override.** Per-machine, so it misses the real cross-machine
  case (direct setup on a laptop, CI setup from a cloud session); a third
  state store beside labels and the intent log; cross-talk in disguise.
- **(b) A machine-level mode set by `gapp init`.** The mode is a property
  of the solution and the operator, not the machine: a laptop
  legitimately does both, and a cloud session can only do `ci`. Separate
  command groups already say which system you touch by the name you type.
- **(c) Reading the CI intent log from the direct path, or GCP labels from
  the CI path.** Cross-talk, and the second needs broad project-list
  access the runner must not have.
- **(d) Deriving the deployment's name from the repo's gapp.yaml on
  GitHub.** It would make the register run read the deployed repo and
  tie the fleet's naming to a file the operator may not control; the
  operator names the deployment, and the entry's `id` governs the runner.
- **(e) A first-class environment concept.** Two entries for one repo
  already express it, with nothing new to learn.

What this settled in the commands: the per-project bootstrap became
`gapp ci project setup <project>`, the only `ci` command that touches GCP,
with its two requirements stated in `--help` and in every failure; the
unit became the deployment with the entry shape above; `ci setup` takes
`--repo` and a name and never uses gcloud; `ci init` alone sets the fleet
repo; the allowlist became `gapp ci sources add|remove|list` over
`GAPP_ALLOWED_SOURCES`; `ci trigger` became `ci deploy`, the same verb as
the direct system; `gapp status` reports the direct system only and never
crashes; and a read GitHub refused is "couldn't check", in GitHub's words.
