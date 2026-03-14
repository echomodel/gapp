# CI/CD and Remote Deployment

This document captures the design for enabling gapp deployments without a local machine — via GitHub Actions, Claude Code on the web, Codespaces, or any stateless environment.

---

## The Problem

Today, gapp requires a local machine with `gcloud auth login` configured. Every `gcloud` subprocess call (and there are ~30 across setup, deploy, secrets, users, tokens, and status) relies on ambient gcloud authentication. Terraform gets its credentials via `gcloud auth print-access-token`, passed as `GOOGLE_OAUTH_ACCESS_TOKEN`.

This ties the operator to their laptop. You can't deploy from your phone, from Claude.ai, from a CI runner, or from a colleague's machine without first configuring gcloud credentials. For a tool whose philosophy is "four commands and you're deployed," this is an unnecessary anchor.

## Goals

1. **Untether deployment from the local machine.** After one-time setup, an operator should never need their laptop to deploy. GitHub UI, `gh` CLI, Claude Code on the web, mobile — any trigger point should work.
2. **Keep gapp a reusable product.** No personal config, no operator-specific values. gapp ships reusable assets (CLI, Terraform, workflows) that anyone can consume.
3. **Keep solution repos as reusable products.** No deployment workflows, no CI/CD config, no GCP coupling. A solution repo is application code + `gapp.yaml`.
4. **Make CI/CD copy/paste simple.** A new operator should be able to follow gapp's docs, copy an example workflow, fill in their project ID, and be running. No custom engineering.
5. **Don't break local workflow.** `gapp deploy` from your laptop continues to work exactly as before. CI/CD is additive, not a replacement.
6. **No stored credentials anywhere.** WIF eliminates JSON key files and stored secrets. The trust relationship is between GCP and a specific GitHub repo — no transferable artifacts.
7. **Operator controls the blast radius.** The deploy identity has only the permissions needed for deployment. It cannot set up new projects, access other projects, or escalate.

## Principles

- **Public repos must not depend on private repos** (existing gapp principle #11). The test: "Can someone deploy this app to their own GCP project using only public repos?" Always yes.
- **Don't hide reusable logic in private repos** (existing gapp principle #14). The CI/CD logic lives in gapp's reusable workflow. The private repo is just configuration.
- **Derive, don't configure** (existing gapp principle #8). WIF pool names, service accounts, and workflow content are all derivable from convention. The only truly unique inputs are the GCP project ID and the operator's repo name.
- **Each phase does one thing** (existing gapp principle). `gapp setup` handles GCP foundation (including WIF). `gapp ci init` handles GitHub wiring. They don't overlap.
- **GitHub is optional.** The core lifecycle — `gapp init`, `gapp setup`, `gapp secret set`, `gapp deploy` — works with any local git repo. No GitHub account, no GitHub API, no GitHub Actions. Some peripheral convenience features are GitHub-aware — for example, `gapp list --available` discovers solutions via GitHub topics, which is a shortcut for recovery scenarios (new machine, lost local config). But it's never required: the same recovery is possible without GitHub by cloning the repo manually and running `gapp setup` again, since GCP project labels (`gapp-{name}=default`) and the repo's `gapp.yaml` contain everything needed to reconstruct the local registry. CI/CD automation (`gapp ci`) also requires GitHub, but is entirely additive. The CI layer calls `gapp deploy` — not the other way around.
- **Security by scoping, not by obscurity.** Project IDs in a private repo aren't security — they're just configuration. Real security comes from WIF trust scoping, service account permissions, and workflow pinning.

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

### The operator repo is not intellectual work

The operator's private repo is an address book, not a product. It maps "solution X → GCP project Y." There's nothing reusable or interesting in it. The interesting parts are in gapp (the reusable workflow) and in the solution repos (the applications). The private repo is configuration — each file says something different (which solution, which project, which identity), so it's not boilerplate even though it looks repetitive.

The repo could be public — project IDs aren't sensitive, and WIF means no credentials are stored. But it's not a product. It's a worked example at best.

### Do you ever need the solution repo locally?

Only for development — writing and testing the code. For deployment, never. The flow becomes:

1. Write code anywhere (local, Codespaces, Claude Code on the web)
2. Push to GitHub
3. Deployment happens via CI (or manual `gapp deploy` if you prefer)

After the one-time `gapp setup` + `gapp ci init`, your laptop is optional for the entire deployment lifecycle. gapp already builds from `git archive HEAD` via Cloud Build — it never needed local Docker. The only thing anchoring you locally was `gcloud auth`.

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
- **Push-triggered** — if the operator owns the solution repo (e.g., you own monarch-access), pushing to main triggers deployment. Safe because only you can merge to main via branch protection.
- **Scheduled** — poll for new versions on a cron.
- **`repository_dispatch`** — webhook-triggered from the solution repo.

This is operator-level configuration, not a gapp concern. A push to a public third-party solution repo must NOT auto-trigger deployment to your project — that would let someone else's commit deploy to your infrastructure. But if you own the solution repo, auto-deploy on push to main is perfectly safe because only you control what gets merged. The same mechanism supports both — the operator just configures a different trigger.

### What the operator does NOT want

- Credentials or project IDs in any public repo — even if they're "just config," it doesn't serve as a reusable pattern
- CI/CD boilerplate rebuilt per solution — the logic lives in gapp's reusable workflow, not in the operator repo
- CI/CD boilerplate rebuilt per user of gapp — the pattern should be copy/paste from gapp's docs
- A public repo that has their personal GCP secrets configured, even if only they can access them — because it stops being a reusable product at that point
- Their local machine required for routine deployments after initial setup

### Security model

- **WIF scoping**: GCP only trusts the specific operator repo. Forks can't authenticate.
- **Service account scoping**: The deploy service account has only the permissions needed for deployment (Cloud Build, Cloud Run, Artifact Registry, specific secrets). It cannot run `gapp setup` or access other GCP projects.
- **Workflow pinning**: The operator pins the reusable workflow to a specific SHA (`@abc123`), not `@main`. This prevents a compromised gapp repo from injecting malicious code into the deploy pipeline.
- **GitHub token scoping**: The `GITHUB_TOKEN` in the workflow is scoped to the operator's repo by default. It can't access other repos.
- **No setup permissions in CI**: The deploy service account should NOT have permissions to run `gapp setup`, create new WIF pools, access other GCP projects, or enable APIs. It can only deploy — build containers, apply Terraform, read secrets. This prevents a compromised workflow from bootstrapping access to other resources.
- **No runaway automation**: The reusable workflow in gapp runs `gapp deploy`, not `gapp setup`. Even if malicious code were injected into the workflow, the service account's scoped permissions prevent it from accessing other projects, creating new trust relationships, or escalating privileges via `gh` (since the GitHub token is also scoped).

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
Rejected on further analysis. WIF pool, provider, and deploy service account are CI infrastructure — they serve no purpose if the operator isn't using GitHub Actions. Putting them in `gapp setup` would pollute GCP foundation with CI-specific resources and violate "each phase does one thing." The original rationale ("they're GCP resources, they belong in setup") was wrong — they're CI-specific GCP resources.

**Option D: `gapp deploy --ci`.**
Rejected immediately. Massively violates "no phase does double duty." First run does irreversible things; subsequent runs don't. Error handling nightmare.

### Chosen design

```
gapp init                                        # scaffold solution (existing)
gapp setup <project-id>                           # GCP foundation only (unchanged)
gapp secret set <name>                            # prerequisites (existing)
gapp deploy                                       # local deploy, still works (existing)
gapp ci init <repo-name>                          # optional: designate CI repo (once per operator)
gapp ci setup <solution-repo-url>                 # optional: wire solution for CI (once per solution)
gapp ci status                                    # optional: check CI state
```

`gapp setup` is unchanged — pure GCP foundation (APIs, bucket, label).

`gapp ci` is a new command group that owns the entire CI concern, split into two phases:

#### `gapp ci init <repo-name>`

One-time setup per operator. Designates the CI repo — where deployment workflows live.

What it does:
1. Writes the CI repo name to local XDG config (`~/.config/gapp/ci.yaml` or a `ci` section in `solutions.yaml`). This is the authoritative local setting.
2. Tags the repo with a `gapp-ci` GitHub topic (for discoverability on other machines).
3. Ensures exactly one repo is tagged for the authenticated `gh` user. If a repo with the topic already exists and the name doesn't match, it errors — one CI repo per operator.

**`--local-only`**: Skips the GitHub topic tagging. Only writes to XDG config. Useful when:
- You don't want to modify topics on the repo
- You're in a GitHub organization or enterprise where topic management is restricted
- You want to work without `gh` CLI configured
- You're testing or working across multiple GitHub accounts

This is the prerequisite for all other `gapp ci` commands. It establishes "where do my deployment workflows live?" — locally via XDG config (always), and remotely via GitHub topic (optionally).

Prerequisites: `gh` CLI authenticated (unless `--local-only`).

#### `gapp ci setup <solution-repo-url>`

Per-solution CI wiring. Run from the solution repo directory (or with `--solution`). Does everything needed to deploy this solution via CI:

1. Discovers the operator's CI repo via `gapp-ci` topic (errors if `gapp ci init` hasn't been run)
2. Creates WIF pool + provider in the GCP project (idempotent, first run only per project)
3. Creates `gapp-deploy` service account with scoped permissions (idempotent, first run only per project)
4. Adds IAM binding: CI repo can impersonate the deploy SA (idempotent)
5. Generates workflow file for this solution with WIF references, project ID, and solution repo URL baked in
6. Commits and pushes the workflow file to the CI repo

Prerequisites: `gapp ci init` completed, `gapp setup <project-id>` completed for this solution, `gh` and `gcloud` authenticated.

The one-to-many relationship: one CI repo per operator, one WIF pool + provider + deploy SA per GCP project, one workflow file per solution. Steps 2-4 are idempotent and skip if already done.

#### `gapp ci status`

Shows the state of CI configuration. Discovers the CI repo via `gapp-ci` topic using `gh`, then reports:

- Which repo is the CI repo
- Which solutions have workflow files
- Whether WIF, SA, and bindings are configured
- Whether workflows are passing/failing

The SDK operation behind `gapp ci status` is reused by `gapp ci setup` to verify the CI repo exists before proceeding. Same pattern as `gapp status` being reusable infrastructure health checking.

Future: `gapp ci trigger`, `gapp ci logs`.

The `next_step` after `gapp deploy` can suggest: `"To enable CI/CD: gapp ci init <repo-name>"`

## Context Resolution: The `--solution` Flag

### Current state

The SDK's `resolve_solution(name)` supports explicit name lookup — if a name is provided, it looks it up in `solutions.yaml` and returns the project_id and repo_path. If no name is provided, it falls back to cwd (finds the git root, reads `gapp.yaml`).

But the CLI is inconsistent about whether it exposes this:

| Accepts solution name | Hardcoded to cwd (no name parameter) |
|---|---|
| `status [name]` (positional arg) | `deploy` |
| `mcp status [name]` (positional arg) | `setup` |
| `mcp connect [name]` (positional arg) | `secrets list/set/add/remove` |
| `tokens create/revoke` (`--solution` option) | `users register/list/get/update/revoke` |

There's also an inconsistency in how the name is passed — sometimes a positional argument, sometimes `--solution`. The `tokens` commands use `--solution` because they already have a required positional argument (`email`).

### The problem in CI

In a CI runner (or any remote environment), there's no local `solutions.yaml` and the cwd isn't a solution repo. The workflow runs from the operator's private repo, not the solution repo.

There are two approaches:

1. **Clone the solution repo on the runner and cd into it.** This restores cwd-based resolution and requires fewer code changes. The reusable workflow handles the clone. `gapp deploy` works as-is.

2. **Pass everything explicitly via flags.** Commands accept `--solution`, `--project`, and `--repo` flags so no local state or cwd is needed.

Approach 1 is simpler and should be the default path — the reusable workflow clones the solution repo before running `gapp deploy`. But approach 2 is still valuable: locally, `--solution` lets you operate on a solution without cd'ing into it, and in CI it provides flexibility when the clone-and-cd pattern doesn't fit.

### What should change

The `--solution` flag (not `--name`, to match the existing convention in `tokens`) should be added to all commands that currently hardcode cwd:

- `gapp deploy --solution <name>`
- `gapp setup --solution <name>`
- `gapp secrets list/set/add/remove --solution <name>`
- `gapp users register/list/get/update/revoke --solution <name>`

This is optional everywhere — cwd remains the default. The flag is a convenience locally and becomes important as gapp usage extends beyond local-only development.

For the commands that currently use a positional `name` argument (`status`, `mcp status`, `mcp connect`), no change is needed — they already work. Whether to also accept `--solution` as an alias for consistency is a minor style question.

### Why this matters for CI

Even if the reusable workflow clones the solution repo (approach 1), the `--solution` flag is still relevant for:

- **`gapp ci init`** — runs from the operator's repo, not the solution repo. Needs to know which solution to generate a workflow for.
- **MCP server tools** — the gapp admin MCP server may be asked about solutions the operator isn't currently cd'd into.
- **Future commands** — `gapp ci status`, `gapp ci trigger` will need to reference solutions by name.

## What Changes in gapp

### Code changes

1. **`_get_access_token()` env var fallback** — check `GOOGLE_OAUTH_ACCESS_TOKEN` before calling `gcloud auth print-access-token`
2. **`gapp ci init` command** — designate and tag the operator's CI repo
3. **`gapp ci setup` command** — create WIF pool/provider/service account in GCP, add IAM binding, generate and push workflow file to CI repo
4. **`gapp ci status` command** — discover CI repo via topic, report configuration state (SDK reused by `ci setup`)
4. **`--solution` flag** — add to `deploy`, `setup`, `secrets *`, and `users *` (the commands that currently hardcode cwd). See "Context Resolution" section for details.
5. **Reusable workflow** — `.github/workflows/deploy.yml` in gapp repo

### No changes needed

- All `gcloud` subprocess calls work as-is (the auth action configures gcloud on the runner)
- Terraform works as-is (already uses `GOOGLE_OAUTH_ACCESS_TOKEN`)
- Cloud Build works as-is (uses its own service account)
- Cloud Run service account works as-is (Terraform manages it)
- Solution repos — no changes at all

## Documentation for Users

The operator pattern is simple enough to be copy/paste documentation in gapp's README:

1. Run `gapp setup <project-id>` (GCP foundation, same as always)
2. Run `gapp ci init <your-repo-name>` (designate your CI repo, once per operator)
3. Run `gapp ci setup <solution-repo-url>` (wire this solution for CI — creates WIF, SA, binding, workflow)
4. Done. Trigger deployments from GitHub UI, CLI, or API. Check with `gapp ci status`.

For someone else using your public solution repo: clone nothing, fork nothing. Copy the example workflow from gapp's docs into your own repo, fill in your project ID and WIF config, and you're running. Two public products, one private glue repo of your own.
