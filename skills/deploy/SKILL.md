---
name: deploy
description: Deploy and manage Python web servers and MCP servers on Google Cloud Run using gapp. Use when asked to deploy a service, get something running on GCP or Cloud Run, push changes to production, set up CI/CD, check deployment status, manage users or access tokens, or any question about hosting a Python service on Google Cloud. Trigger on intent — "deploy this to cloud run", "help me get this hosted", "deploy my latest changes", "set up CI/CD", "how do I get this running in the cloud", "check my deployment status", etc.
disable-model-invocation: false
user-invocable: true
---

# Deploy Skill

## Overview

This skill guides users through the full lifecycle of deploying
and managing Python web servers and MCP servers on Google Cloud
Run using gapp. It handles everything from initial assessment
through ongoing operations — candidacy evaluation, setup,
deployment, CI/CD, user management, and redeployment.

gapp deploys containerized Python services to Cloud Run with
Terraform. Solutions remain cloud-agnostic — no GCP imports, no
framework dependencies. A `gapp.yaml` file is the only
touchpoint. gapp handles infrastructure, secrets, container
builds, multi-user auth, and credential management.

## Solution Lifecycle

`gapp_status` tells you where a solution is. Use this to
determine which phase to start from:

| State | `initialized` | `project.id` | `pending` | `next_step.action` | CLI | MCP tool | How you get here |
|-------|--------------|-------------|-----------|-------------------|-----|----------|-----------------|
| Not initialized | `false` | — | — | `init` | `gapp init` | `gapp_init` | Haven't run `gapp init` yet |
| Initialized, no project | `true` | `null` | `true` | `setup` | `gapp setup <project-id>` | `gapp_setup` | Ran `gapp init` but not `gapp setup` |
| Has project, not deployed | `true` | set | `true` | `deploy` | `gapp deploy` | `gapp_deploy` | Ran `gapp setup` but not `gapp deploy`, or infrastructure was destroyed |
| Deployed | `true` | set | `false` | — | — | — | Service URL available |

When `initialized` is `false`, skip straight to Phase 1. When
`initialized` is `true`, read `next_step.action` to know which
phase the user needs next. When `pending` is `false`, the
solution is fully deployed — jump to Ongoing Operations.

## Phase 0: Assess the Situation

Before doing anything, figure out where the user is. Call these
in parallel:

- `gapp_list` — see if any solutions are already registered
- `gapp_status` — if in a repo that looks like a solution, check
  its deployment status

Also inspect the current working directory:

- Does `gapp.yaml` exist? → already initialized
- Does `pyproject.toml` or `setup.py` exist? → Python package
- Is there an ASGI app or MCP server? Look for uvicorn
  entrypoints, FastMCP, Starlette, FastAPI
- Is there a `Dockerfile`? → may already be containerized

### If gapp.yaml exists (already initialized)

The repo is already a gapp solution. Check status and determine
what phase it's in:

1. Call `gapp_status` to get infrastructure health
2. The status response includes a `next_step` field — this tells
   you exactly what the user needs to do next
3. Present the current state and offer to help with the next step

### If no gapp.yaml (new candidate)

Evaluate whether the repo is a good candidate for gapp:

**Good candidates:**
- Python web server (FastAPI, Starlette, Flask with ASGI)
- Python MCP server (FastMCP with HTTP transport)
- Has a clear ASGI entrypoint (`module.path:app`)
- Needs to be accessible over the internet

**Not a fit:**
- Not Python
- CLI-only tool (no web server)
- No ASGI/HTTP interface
- Already deployed elsewhere and user doesn't want to move

### gapp.yaml — Minimal Configuration Philosophy

**Goal: as little in gapp.yaml as possible.** gapp derives what
it can. The solution's own files (Dockerfile, pyproject.toml)
take precedence over gapp.yaml.

#### What gapp needs to know (in priority order)

**How to build and run the container:**

1. Solution has a **Dockerfile** → gapp builds it as-is. No
   `service.entrypoint` or `service.cmd` needed.
2. Solution has its own serve command (e.g., a CLI entry point
   that starts the server) → `service.cmd` in gapp.yaml. gapp
   generates a Dockerfile with this as the CMD.
3. Solution exposes an ASGI app variable at module level →
   `service.entrypoint` in gapp.yaml (e.g.,
   `my_package.server:app`). gapp wraps it with uvicorn in
   the generated Dockerfile.
4. Neither → one of the above must be provided.

**Whether to allow public access:**

`public: true` grants allUsers Cloud Run IAM. Default is
non-public (locked down). Solutions handling their own auth
(mcp-app, custom) need `public: true`. Use `gapp_status` to
check current public state. `gapp_deploy(public=true)` can
override per-deploy without changing the yaml.

**Environment variables and secrets:**

Before writing `env` entries, understand what the app needs from
its environment. The app's documentation, README, or framework
should declare its runtime requirements — required env vars,
secrets, persistent storage paths, etc. Map each requirement to
the appropriate gapp.yaml pattern:

- **Secrets** (signing keys, API tokens, credentials) →
  `secret: true` or `secret: { generate: true }`. gapp stores
  the value in GCP Secret Manager and injects it as an env var
  at runtime. The value never appears in the repo.
- **Persistent storage paths** → use `{{SOLUTION_DATA_PATH}}`
  which resolves to the GCS FUSE mount path (`/mnt/data`). Data
  written here survives container restarts and redeploys.
- **Plain config values** → `value: "..."` for non-secret
  configuration.

If the app requires a secret, decide with the user: should gapp
generate it (`generate: true` — good for signing keys that just
need to be strong and random), or does the user need to provide
it (`secret: true` — for API keys, upstream credentials)?

If the app requires persistent storage, map the app's data path
env var to a subdirectory under `{{SOLUTION_DATA_PATH}}`. The
app will write to this path inside the container; gapp ensures
it's backed by durable GCS storage.

```yaml
env:
  - name: SIGNING_KEY
    secret:
      generate: true
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"
  - name: LOG_LEVEL
    value: INFO
```

- Plain values: `name` + `value`
- Secret-backed: `name` + `secret: true` (must exist) or
  `secret: { generate: true }` (auto-created if missing)
- `{{SOLUTION_DATA_PATH}}` → resolved to `/mnt/data` at
  deploy time (GCS FUSE mount path)
- `{{SOLUTION_NAME}}` → resolved to the solution name

#### Complete gapp.yaml reference

```yaml
# Required when no Dockerfile — pick one:
service:
  cmd: my-app-mcp serve           # app has its own serve command
  # OR
  entrypoint: my_package.server:app  # ASGI module:app for uvicorn

# Optional — default false:
public: true

# Optional — custom domain (subdomain, requires CNAME):
domain: mcp.example.com

# Optional — env vars and secrets:
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"
```

#### MCP tools for gapp.yaml management

- `gapp_init` — creates or updates gapp.yaml (entrypoint,
  auth, secrets, mcp_path, domain)
- `gapp_status` — shows current config, deployment state,
  public access, next step
- `gapp_secret_set` — stores a secret value in Secret Manager
- `gapp_deploy` — deploys (accepts `public` override arg)

Read gapp.yaml directly for full config. Use `gapp_status` for
a quick check of deployment state without reading files.

#### After deployment

Continue to Phase 5 for post-deploy verification and access
setup.

### Cloud Readiness Check (MCP servers)

Before proceeding to init, inspect how the MCP server
authenticates with its backend API. Look at the SDK layer (or
wherever the HTTP client is configured) for how backend
credentials are obtained. Common patterns:

**Reads `Authorization` header from incoming request**
→ Cloud-ready. The credential arrives per-request, which works
with both direct auth and gapp's bearer mediation.

**Reads credential from environment variable or local file**
(e.g., `os.getenv("SOME_TOKEN")` or `~/.config/tool/token`)
→ **Not cloud-ready.** This pattern assumes single-user local
execution (stdio). If deployed as-is to Cloud Run, you'd have
to bake the backend credential into the container environment —
which means every unauthenticated HTTP request gets full access
to the user's backend account. **Never do this.** Do not suggest
setting the backend credential as a Cloud Run secret env var as
a workaround — it creates an unauthenticated proxy to the
user's account.

**When you detect env-var/file-based credentials, propose a
refactor before proceeding.** Present it like this:

> Your service reads backend credentials from an environment
> variable / local file. That works for local stdio, but on
> Cloud Run it would mean any request to the URL gets full
> access to your account — there's no incoming auth.
>
> A small refactor makes it work for both local and cloud: the
> SDK accepts an optional token parameter. When provided (from
> an incoming request's Authorization header), it uses that.
> When not provided, it falls back to the environment variable
> for local/stdio use. If neither exists, it raises an auth
> error — not a "missing env var" error, but a proper "no
> credentials" error.
>
> This keeps local stdio working exactly as before, while
> making the service ready for cloud hosting with whatever
> auth layer the solution chooses. Want me to make this change?

If the user agrees, make these changes in order:

1. **SDK layer** — modify the function that obtains the backend
   credential (e.g., `get_token()`) to accept an optional
   `token` parameter. Logic:
   - If `token` is provided → use it (cloud/HTTP path)
   - Else if env var is set → use it (local/stdio path)
   - Else → raise an authentication error (not a "missing env
     var" error — the caller isn't authenticated)

2. **SDK client/operations** — thread the optional token through
   to wherever API calls are made, so callers can pass it in.

3. **MCP server layer** — when running in HTTP mode, extract
   the `Authorization: Bearer <token>` header from the incoming
   request and pass it to the SDK. When running in stdio mode,
   pass nothing (falls back to env var).

4. After the refactor, the service is ready to be deployed.
   Any client-auth layer the solution chooses will sit in
   front of the SDK's optional-token path.

#### DNS rebinding protection must be disabled on Cloud Run

FastMCP enables DNS rebinding protection by default, rejecting
requests whose `Host` header doesn't match expected values. On
Cloud Run, the load balancer sets a Host header that the MCP SDK
doesn't recognize, causing all requests to fail with HTTP 421
("Invalid Host header").

Check how `FastMCP` is constructed. If it's just
`FastMCP("name")` with no transport security override, the
service will fail on Cloud Run.

The fix is to disable DNS rebinding protection when running on
Cloud Run, detected via the `K_SERVICE` environment variable
that Cloud Run sets automatically:

```python
if os.environ.get("K_SERVICE"):
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = FastMCP("name", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ))
else:
    mcp = FastMCP("name")
```

This is safe because on Cloud Run, Google's load balancer
handles host validation. Locally, the MCP SDK's built-in
localhost-only protection remains active.

**Important:** Do not suggest putting the backend credential in
a Cloud Run secret environment variable as an alternative. That
creates an unauthenticated proxy — any request to the service
URL gets the user's backend access with no gatekeeping. The
whole point of this refactor is to ensure credentials arrive
per-request through a mediated auth layer.

**Present gapp to the user:**

> gapp deploys Python web servers and MCP servers to Google Cloud
> Run. Your solution stays cloud-agnostic — no GCP imports. You
> add a small `gapp.yaml` to your repo and gapp handles the
> infrastructure: container build, secrets in Secret Manager, data
> volumes, domain mapping, IAM.
>
> It takes four steps to go from repo to running service:
> init, setup, set secrets, deploy. Each step is idempotent
> and tells you what to do next.
>
> Want to set it up?

**Auth is the solution's responsibility, not gapp's.** gapp does
not ship auth middleware, does not manage users, and does not
mint tokens. If the service needs to authenticate clients, the
solution's own framework handles it — either directly in the
app code or via a framework the solution imports. Refer to that
framework's own skill or documentation for auth setup.

## Phase 1: Initialize

`gapp_init` both creates and configures. First call creates
`gapp.yaml`; subsequent calls update settings. Use it anytime
the user wants to change gapp configuration — entrypoint,
secrets, mcp_path, etc.

If the user wants to proceed and there's no `gapp.yaml`:

1. Help them identify the ASGI entrypoint — the `module:app`
   string that uvicorn would use. Look at the code to find it.
2. Call `gapp_init` to create `gapp.yaml` and register the
   solution locally.
3. If the service needs secrets (API keys, tokens), help them
   declare those in `gapp.yaml`'s `env` section with a `secret`
   entry (or `prerequisites.secrets` for prerequisite-only
   declarations).

## Phase 2: GCP Foundation

If `gapp_status` shows `deployment.project` is null, the user
needs a GCP project. Call `gapp_deployments_list` to discover
available projects.

The response has:
- `default` — project ID with the most gapp solutions (the
  primary gapp project)
- `projects` — all GCP projects with gapp solutions, each
  listing its deployed solutions with instance names

Present the results:

1. If the solution appears in a project's solutions list, it's
   already deployed there. Recommend that project:

   > This solution is already deployed to project `<id>`.
   > Want to attach to that?

2. If not deployed anywhere but `default` exists, recommend it:

   > Your other solutions (X, Y) are deployed to project
   > `<default>`. Want to use the same one?

3. If no projects found at all, ask the user to provide a
   project ID or create one in the Google Cloud Console.

Once confirmed, call `gapp_setup(project_id="the-project-id")`.

This enables APIs, creates a per-solution GCS bucket for Terraform
state, and labels the project. The project ID is remembered for
future commands.

## Phase 3: Secrets

Call `gapp_secret_list` to see what secrets are declared and
their status. For any that aren't populated, ask the user for the
value and call `gapp_secret_set(secret_name, value)`.

Secret values go in Secret Manager, never in the repo. Only
secret names (references) go in `gapp.yaml`.

## Phase 4: Deploy

Two paths — present both and let the user choose:

### Path A: Local Deploy

Requires `terraform` and `gcloud` locally.

**Always use the async build + deploy flow.** This gives the
user visibility into build progress instead of blocking silently
for minutes.

1. `gapp_build` — submits Cloud Build and returns immediately
   with a `build_id`. The image builds remotely while the agent
   can do other work (check secrets, review config, etc.).
2. `gapp_deploy(build_ref=<build_id>)` — polls the build status.
   Keep `build_check_timeout` short (10-30s) so the user sees
   progress updates. If the build is still running, returns
   `"status": "running"` with the same `build_ref` and log
   progress — call again to keep polling. When done, runs
   Terraform and returns the service URL.

**Do not use long timeouts.** The point of the async flow is to
report back frequently. Use 10-30s so each poll returns quickly
with updated log lines and status. The user should see build
progress, not a spinner.

**Fallback: blocking deploy.** `gapp_deploy` with no `build_ref`
does a full blocking build + terraform in one call. Avoid this —
it blocks with no progress feedback.

Both paths require a clean git tree — uncommitted changes block
the build. The build is skipped if the image for the current
commit already exists.

### Path B: CI/CD (recommended for ongoing use)

Set up once, then deploy from anywhere — GitHub UI, Claude.ai,
phone. No terraform or docker needed locally after setup.

1. `gapp_ci_init(repo="owner/ci-repo")` — designate a private
   CI repo
2. `gapp_ci_setup(solution="name")` — create WIF, SA, push
   workflow
3. `gapp_ci_trigger(solution="name")` — deploy via GitHub Actions

After CI setup, code changes and deployments are decoupled from
the user's machine. Any tool with GitHub access can trigger a
deploy.

**When to recommend CI/CD:**
- User wants to deploy from multiple machines
- User wants cloud-based agents (Claude.ai) to deploy
- User doesn't want terraform installed locally
- User wants automated deployments on push

## Phase 5: Post-Deploy Verification & Access

After deployment, verify the service is running and set up access:

1. **Check health** — `gapp_status` confirms the service is up
   and returns the service URL
2. **Admin access** — if the solution requires admin credentials
   (e.g., a JWT signing key or shared secret), the user needs to
   provide or locate them. They may have the value in a local
   keychain, password vault, or other personal secret store from
   when they originally created it. If the secret was auto-
   generated by gapp during deploy (`generate: true` in
   gapp.yaml), the user never had the value locally — use
   `gapp_secret_get` to retrieve it from GCP Secret Manager
3. **Verify and configure access** — follow the solution's
   framework documentation or admin tooling to connect an admin
   CLI, register users, issue access tokens, and test end-to-end.
   The specifics depend on the solution's auth framework — check
   the solution's README, CONTRIBUTING, or any framework-specific
   skills for guidance
4. **Get connection info** — `gapp_mcp_connect()` generates
   ready-to-use MCP client connection commands for Claude Code,
   Gemini CLI, and Claude.ai. With `user` param, mints a real
   access token inline

## Custom Domain Setup

If the user wants a custom domain (e.g., `mcp.example.com`):

1. **Set the domain** — `gapp_init(domain="mcp.example.com")`
   or edit `domain:` in gapp.yaml directly.
2. **Deploy** — the next `gapp deploy` creates the Cloud Run
   domain mapping. The default `.run.app` URL continues working
   throughout — the user is never without a working endpoint.
3. **Configure DNS** — the user needs to add a CNAME record at
   their domain registrar:
   ```
   CNAME  mcp.example.com  →  ghs.googlehosted.com
   ```
   Walk the user through this. Ask which registrar they use and
   help them find the DNS settings. This is the one manual step
   that gapp cannot automate.
4. **Check status** — `gapp_status` shows domain status:
   - `pending_dns` — CNAME not yet configured or not propagated
   - `pending_cert` — DNS is correct, SSL cert being provisioned
   - `active` — fully operational on the custom domain

DNS propagation can take minutes to hours. The `.run.app` URL
works immediately and always. The custom domain becomes usable
once DNS propagates and Cloud Run provisions the SSL cert
(automatic, no user action needed after DNS).

**Only subdomains are supported** (e.g., `mcp.example.com`),
not apex/bare domains (`example.com`). Apex domains require
A records instead of CNAME, which Cloud Run domain mapping
doesn't handle cleanly.

To **remove** a custom domain, clear the `domain` field from
gapp.yaml (or `gapp_init(domain="")`) and redeploy. Terraform
destroys the mapping.

To **change** a custom domain, update the `domain` field and
redeploy. Terraform replaces the old mapping with the new one.
Update the CNAME at the registrar to match.

## Ongoing Operations

### Check status

Use `gapp_status` to check infrastructure health. Returns:
- Deployment state (deployed, not deployed, needs redeploy)
- Service URL
- Health check results
- Guided next steps

Use `gapp_mcp_status` to check MCP-specific health:
- MCP endpoint availability
- Tool enumeration
- Auth status

### Redeploy with changes

Before redeploying, check whether the code changes affect how
the app reads or writes data. If the data format, directory
structure, or storage schema has changed, inspect the existing
data on the solution's GCS bucket to confirm compatibility.
Use `gapp_status` to get the project and solution name, then
inspect the data volume:

```bash
gsutil ls gs://gapp-{name}-{project-id}/data/
```

If existing data is incompatible with the new code, present
findings to the user before proceeding. They may need to
migrate, reorganize, or back up data before the redeploy.

After confirming data compatibility (or for code-only changes):

- **Path A:** `gapp_build` then `gapp_deploy(build_ref=...)` — rebuilds if the commit SHA changed
- **Path B:** `gapp_ci_trigger` — dispatches GitHub Actions

Remind the user: uncommitted changes won't be included. The build
uses `git archive HEAD` for source integrity.

### List and discover

- `gapp_list` — all registered solutions
- `gapp_list(available=True)` — include remote GitHub solutions
- `gapp_mcp_list` — solutions with MCP endpoints

## MCP Tools Reference

These are the plugin's MCP tools. Use them throughout the
workflow as needed:

| Tool | Purpose |
|------|---------|
| `gapp_init` | Initialize a solution (create gapp.yaml, register, GitHub topic) |
| `gapp_setup` | GCP foundation (APIs, bucket, labels) |
| `gapp_build` | Submit async Cloud Build, returns build_id |
| `gapp_deploy` | Poll build + Terraform apply (use with build_ref) |
| `gapp_schema` | Live gapp.yaml JSON Schema |
| `gapp_secret_list` | List declared secrets and their state in Secret Manager |
| `gapp_secret_set` | Store a secret value in Secret Manager (labeled gapp-solution=<name>) |
| `gapp_secret_get` | Read a secret value (or hash + length) |
| `gapp_ci_init` | Designate CI repo |
| `gapp_ci_setup` | Wire solution for CI/CD (WIF, SA, workflow) |
| `gapp_ci_trigger` | Trigger GitHub Actions deploy |
| `gapp_ci_status` | CI/CD readiness check |
| `gapp_status` | Infrastructure health check (local, fast) |
| `gapp_deployments_list` | Discover GCP projects with gapp solutions |
| `gapp_list` | List registered solutions |
| `gapp_mcp_status` | MCP health + tool enumeration (unauthenticated probe) |
| `gapp_mcp_list` | List MCP-enabled solutions |
| `gapp_mcp_connect` | Client connection commands (token placeholder is `<YOUR_PAT>`) |

## Important Reminders

- Every gapp operation is idempotent and returns a `next_step`
  field telling what to do next. Trust it.
- Solutions are cloud-agnostic. Never suggest adding GCP imports
  to the solution itself.
- One repo = one solution = one Cloud Run service.
- The build uses `git archive HEAD`. Uncommitted changes are never
  included. The working tree must be clean.
- Image tags are the HEAD commit SHA. If the image already exists,
  the build is skipped.
- Secret values go in Secret Manager, never in the repo. Only
  secret names (references) go in `gapp.yaml`. Every secret gapp
  creates is labeled `gapp-solution=<name>`; pre-existing
  unlabeled secrets at the same id are never silently adopted.
- gapp does not handle auth, mint tokens, or manage users. If the
  service needs auth, it is the solution's own framework's job —
  use that framework's admin tools for users and tokens. Refer
  the user to the solution's framework skill or docs.
- Guide users step by step. Don't dump the entire lifecycle at
  once. Assess where they are and help with the next phase.
- IAM-based auth on Cloud Run is not practical for MCP clients
  (Claude.ai, Gemini CLI, Claude Code can't attach IAM tokens),
  so most MCP solutions deploy with `public: true` and let the
  solution's own auth layer gate access at the application level.
