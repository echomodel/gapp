# CI/CD and Remote Deployment

This document captures the design for enabling gapp deployments without a local machine — via GitHub Actions, Claude Code on the web, Codespaces, or any stateless environment.

---

## The Problem

Today, gapp requires a local machine with `gcloud auth login` configured. Every `gcloud` subprocess call (and there are ~30 across setup, deploy, secrets, users, tokens, and status) relies on ambient gcloud authentication. Terraform gets its credentials via `gcloud auth print-access-token`, passed as `GOOGLE_OAUTH_ACCESS_TOKEN`.

This ties the operator to their laptop. You can't deploy from your phone, from Claude.ai, from a CI runner, or from a colleague's machine without first configuring gcloud credentials. For a tool whose philosophy is "four commands and you're deployed," this is an unnecessary anchor.

## The Goal

Enable `gapp deploy` (and eventually other commands) to run in any environment where GCP credentials can be provided — without changing how gapp works locally.

## Constraints

### Solution repos are products, not personal projects

Solution repos (e.g., monarch-access) are reusable, public, GCP-agnostic products. They contain application code and a `gapp.yaml`. They must not contain:

- Deployment workflows hardcoded to a specific operator's infrastructure
- Personal credentials or project IDs
- CI/CD configuration that couples them to gapp or any specific deployment tool

A solution repo should pass the test: "Can someone else deploy this to their own GCP project using only public repos?" The answer must always be yes.

### gapp is a product, not a personal project

gapp is a reusable CLI tool. It ships reusable assets (Terraform modules, Dockerfile templates, runtime wrapper, and now reusable GitHub workflows). It must not contain anyone's personal project IDs, credentials, or operator-specific configuration.

### Neither product repo should own operator-specific config

This is the key insight. There are three layers:

| Layer | Visibility | Contains |
|-------|-----------|----------|
| **Tool** (gapp) | Public product | CLI, Terraform modules, Dockerfile template, runtime wrapper, reusable GitHub workflows |
| **Application** (solution repo) | Public product | Application code, `gapp.yaml` |
| **Operator config** (private repo) | Private, per-operator | Project IDs, WIF references, workflow files that wire tool + application to infrastructure |

The operator config is the only place where "deploy this specific solution to this specific GCP project" is expressed. This is the `personal-projects` repo pattern.

### Industry precedent

This three-layer separation is standard:

- **Kubernetes**: Helm charts (product) are public. Your `fleet-config` repo (private) references them with your values.
- **Terraform**: Modules (product) are public. Your `infra-live` repo (private) calls modules with your project IDs.
- **Heroku/Vercel/Render**: The "operator config" is the platform's dashboard — you connect a public repo to your account.

## Authentication Architecture

### Current state: two auth channels

gapp uses GCP authentication in two ways:

1. **Ambient gcloud CLI auth** — the vast majority of operations. Every `subprocess.run(["gcloud", ...])` call relies on whatever `gcloud auth login` session is active. Used by: `setup.py` (enable APIs, create buckets, label projects), `deploy.py` (Artifact Registry, Cloud Build), `secrets.py` (Secret Manager CRUD), `tokens.py` (signing key access), `users.py` (GCS operations), `status.py`.

2. **Explicit OAuth token for Terraform** — `deploy.py:_get_access_token()` calls `gcloud auth print-access-token` once, then passes the result to Terraform via `GOOGLE_OAUTH_ACCESS_TOKEN` env var. This is the only place gapp extracts a token explicitly.

### Three GCP identities in the full lifecycle

| Identity | Used by | How authenticated | Permissions needed |
|----------|---------|-------------------|-------------------|
| **Human operator** (or CI principal) | `gapp setup`, `gapp deploy`, `gapp secret set`, all admin commands | `gcloud auth login` (local) or WIF (CI) | Broad: enable APIs, create buckets, manage secrets, submit builds, run Terraform |
| **Cloud Build service account** | Container builds inside `gapp deploy` | Automatic — `{project-number}@cloudbuild.gserviceaccount.com` | Pull base images, build Docker, push to Artifact Registry |
| **Cloud Run service account** | Running deployed service at runtime | Terraform creates `gapp-{name}@{project}.iam.gserviceaccount.com` | `secretmanager.secretAccessor` on its secrets, `storage.objectUser` on auth bucket |

For CI/CD, only **identity #1** needs to change. The other two are already automated.

### Target state: Workload Identity Federation

WIF eliminates stored credentials entirely. The trust relationship is:

```
GCP project ←—trusts—→ specific GitHub repo (via OIDC)
```

At runtime in GitHub Actions:

1. GitHub generates a short-lived OIDC token: "I am repo X, running workflow Y"
2. `google-github-actions/auth` action exchanges it with GCP
3. GCP validates the trust and issues a short-lived access token (~1 hour)
4. gcloud and Terraform use that token

No JSON key files. No stored secrets. No credentials in any repo. If someone forks the operator's repo, their fork can't authenticate — WIF is scoped to the specific repo.

### Required code change

Minimal. `_get_access_token()` in `deploy.py` should check the environment first:

```python
def _get_access_token() -> str:
    if token := os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN"):
        return token
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Failed to get access token. Run 'gcloud auth login' first.")
    return result.stdout.strip()
```

All other gcloud subprocess calls work as-is — the `google-github-actions/auth` action configures gcloud on the runner automatically.

## The Operator's Private Repo

The operator's private repo (e.g., `personal-projects`) is minimal. Per solution, it contains one workflow file:

```yaml
# .github/workflows/monarch-access.yml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: 'Version/tag/SHA to deploy'
        default: 'main'

jobs:
  deploy:
    uses: krisrowe/gapp/.github/workflows/deploy.yml@<pinned-sha>
    with:
      repo: krisrowe/monarch-access
      ref: ${{ inputs.ref }}
      project-id: my-gcp-project-123
      workload-identity-provider: projects/123/locations/global/workloadIdentityPools/github/providers/github
      service-account: gapp-deploy@my-gcp-project-123.iam.gserviceaccount.com
```

That's it. No boilerplate. Each file says something different — which solution, which project, which identity. The reusable workflow in gapp handles all the logic.

### Trigger options (operator's choice)

The operator decides when deployment happens:

- **`workflow_dispatch`** — manual trigger from GitHub UI, `gh` CLI, or API. Best for third-party solution repos you don't own.
- **Push-triggered** — if the operator owns the solution repo (e.g., you own monarch-access), pushing to main triggers deployment. Safe because only you can merge to main.
- **Scheduled** — poll for new versions on a cron.
- **`repository_dispatch`** — webhook-triggered from the solution repo.

This is operator-level configuration, not a gapp concern.

### Security model

- **WIF scoping**: GCP only trusts the specific operator repo. Forks can't authenticate.
- **Service account scoping**: The deploy service account has only the permissions needed for deployment (Cloud Build, Cloud Run, Artifact Registry, specific secrets). It cannot run `gapp setup` or access other GCP projects.
- **Workflow pinning**: The operator pins the reusable workflow to a specific SHA (`@abc123`), not `@main`. This prevents a compromised gapp repo from injecting malicious code into the deploy pipeline.
- **GitHub token scoping**: The `GITHUB_TOKEN` in the workflow is scoped to the operator's repo by default. It can't access other repos.

## gapp Ships a Reusable GitHub Workflow

The reusable workflow lives in the gapp repo at `.github/workflows/deploy.yml`. It:

1. Accepts inputs: `repo`, `ref`, `project-id`, WIF config
2. Clones the solution repo at the specified ref
3. Installs gapp
4. Authenticates via WIF (using `google-github-actions/auth`)
5. Runs `gapp deploy --ref <ref>`

This is a product asset, like the Dockerfile template or Terraform modules. The operator's workflow file is a thin caller that passes values.

GitHub natively supports this via [reusable workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows). The called workflow declares `on: workflow_call` with inputs, and the caller uses `uses: owner/repo/.github/workflows/file.yml@ref`.

## CLI Design: Where CI Commands Live

### Options considered

**Option A: Extend `gapp setup` with `--ci` flag.**
Rejected. Violates "no phase does double duty." Setup is GCP foundation; GitHub repo manipulation is a different domain with different failure modes. The `--ci` flag makes setup conditional and branching.

**Option B: Separate `gapp ci` command group.**
Strong option. Clean separation — `gapp ci setup` for WIF + service account (once per project), `gapp ci add` for adding a solution's workflow file (once per solution). Follows the existing pattern of command groups (`secrets`, `users`, `tokens`, `mcp`, `admin`). But adds more commands to learn.

**Option C: `gapp setup` creates WIF (always), separate `gapp ci init` for operator repo.**
Chosen approach. WIF and the deploy service account are GCP resources — they belong in `gapp setup`. They're idempotent and free. The GitHub repo manipulation (`gapp ci init --repo <repo>`) is a separate command for a separate domain.

**Option D: `gapp deploy --ci`.**
Rejected immediately. Massively violates "no phase does double duty." First run does irreversible things; subsequent runs don't. Error handling nightmare.

### Chosen design

```
gapp init                              # scaffold solution (existing)
gapp setup <project-id>                # GCP foundation + WIF + deploy SA (extended)
gapp secret set <name>                 # prerequisites (existing)
gapp deploy                            # local deploy, still works (existing)
gapp ci init --repo personal-projects  # optional: add workflow to operator repo (new)
```

`gapp setup` gains:
- WIF pool + provider creation (idempotent, always created)
- Deploy service account with scoped permissions (idempotent, always created)

`gapp ci` is a new command group:
- `gapp ci init --repo <repo>` — push workflow file to operator's repo for current solution
- Future: `gapp ci status`, `gapp ci trigger`, `gapp ci logs`

The `next_step` after `gapp deploy` can suggest: `"To enable CI/CD: gapp ci init --repo <your-repo>"`

## Context Resolution Gap

### Current state

`resolve_solution(name)` in `context.py` supports explicit name lookup, but no CLI command exposes it. Every command assumes cwd contains the solution repo. `deploy_solution()` calls `resolve_solution()` with no name argument.

### The problem in CI

In a CI runner (or any remote environment), there's no local `solutions.yaml` and the cwd isn't a solution repo. The workflow runs from the operator's private repo, not the solution repo.

### What's needed

Commands that run in CI need explicit arguments for everything that's normally derived:

```bash
gapp deploy --solution monarch-access --repo krisrowe/monarch-access --project my-project-123
```

Or, the reusable workflow handles this by cloning the solution repo and running gapp from within it — which restores the cwd-based resolution. This is simpler and requires fewer code changes, but means the runner must clone the solution repo.

The `--solution` flag should be added to relevant commands regardless — it's useful for local use too (operating on a solution without cd'ing into it) and becomes essential as gapp usage moves beyond local-only.

## What Changes in gapp

### Code changes

1. **`_get_access_token()` env var fallback** — check `GOOGLE_OAUTH_ACCESS_TOKEN` before calling `gcloud auth print-access-token`
2. **`gapp setup` extended** — create WIF pool, provider, and deploy service account (idempotent)
3. **`gapp ci init` command** — generate and push workflow file to operator repo
4. **`--solution` flag** — add to `deploy`, `status`, and other commands that currently assume cwd
5. **Reusable workflow** — `.github/workflows/deploy.yml` in gapp repo

### No changes needed

- All `gcloud` subprocess calls work as-is (the auth action configures gcloud on the runner)
- Terraform works as-is (already uses `GOOGLE_OAUTH_ACCESS_TOKEN`)
- Cloud Build works as-is (uses its own service account)
- Cloud Run service account works as-is (Terraform manages it)
- Solution repos — no changes at all

## Documentation for Users

The operator pattern is simple enough to be copy/paste documentation in gapp's README:

1. Run `gapp setup <project-id>` (creates WIF + deploy SA alongside existing foundation)
2. Run `gapp ci init --repo <your-private-repo>` (generates workflow file)
3. Done. Trigger deployments from GitHub UI, CLI, or API.

For someone else using your public solution repo: clone nothing, fork nothing. Copy the example workflow from gapp's docs into your own repo, fill in your project ID and WIF config, and you're running. Two public products, one private glue repo of your own.
