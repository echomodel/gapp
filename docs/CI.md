# CI/CD and Remote Deployment

This document captures the design for enabling gapp deployments without a local machine — via GitHub Actions, Claude Code on the web, Codespaces, or any stateless environment.

---

## The Problem

Today, gapp requires a local machine with `gcloud auth login` configured. Every `gcloud` subprocess call (and there are ~30 across setup, deploy, secrets, users, tokens, and status) relies on ambient gcloud authentication. Terraform gets its credentials via `gcloud auth print-access-token`, passed as `GOOGLE_OAUTH_ACCESS_TOKEN`.

This ties the operator to their laptop. You can't deploy from your phone, from Claude.ai, from a CI runner, or from a colleague's machine without first configuring gcloud credentials. For a tool whose philosophy is "four commands and you're deployed," this is an unnecessary anchor.

## Core Philosophy: Workstation Once, Then Never

The operator's local machine is needed exactly once — to run `gapp setup` and `gapp ci setup`, which require personal GCP and GitHub credentials. These are bootstrap commands that establish trust relationships (WIF, IAM bindings) and push configuration (workflow files). After that, the operator's workstation is never needed again for deployments. All subsequent deploys happen through GitHub Actions, triggered from any device — GitHub UI, mobile, `gh` CLI, or an API call. The local machine becomes optional for the entire deployment lifecycle.

This mirrors how `gapp setup` already works for GCP foundation — you run it once to establish the project, then `gapp deploy` works forever after. CI extends that pattern: run `gapp ci setup` once per solution, then deployments are fully automated and decoupled from any specific machine.

Once CI is configured, tools like Claude.ai, Claude mobile app, Claude Code on the web, or GitHub's web editor can make code changes to a solution repo, push them, and trigger deployment to GCP — all without access to GCP credentials or a local development environment. The code change and the deployment are fully decoupled from the operator's machine. The operator's role shifts from "person who deploys" to "person who configured the deployment pipeline once."

### Why bootstrap stays on the workstation

It would be possible to remove the one-time workstation step too: a hub project with a Workload Identity pool trusted by the operator's CI repo, a bootstrap service account granted admin roles at the folder level, and a workflow that runs `gapp setup` and `gapp ci setup` on a runner for any new project. That is deliberately not done. It replaces a per-project, deploy-only identity with one cross-project admin identity reachable from a GitHub repo, which contradicts goal 7 below (the deploy identity cannot set up new projects, access other projects, or escalate), and it saves only a few minutes per new solution. The per-solution `gapp setup` and `gapp ci setup` on a logged-in workstation are the intended cost of keeping each project's blast radius to itself. Revisit only if the bootstrap step itself can be scoped to one project at a time.

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

The operator config is the only place where "deploy this specific solution to this specific GCP project" is expressed. This is the `<your-ci-repo>` repo pattern.

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

1. **Ambient gcloud CLI auth** — the vast majority of operations. Every `subprocess.run(["gcloud", ...])` call relies on whatever `gcloud auth login` session is active. Used by: `core.py` (setup, deploy, status, project labels, Artifact Registry, Cloud Build, GCS) and `secrets.py` (Secret Manager CRUD).

2. **Explicit OAuth token for Terraform** — gapp calls `gcloud auth print-access-token` once during deploy and passes the result to Terraform via the `GOOGLE_OAUTH_ACCESS_TOKEN` env var. This is the only place gapp extracts a token explicitly.

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

### No code changes needed for auth

gapp's existing `_get_access_token()` calls `gcloud auth print-access-token` and passes the token to Terraform via `GOOGLE_OAUTH_ACCESS_TOKEN`. In CI, `google-github-actions/auth` (WIF exchange) + `google-github-actions/setup-gcloud` (installs and configures gcloud) make `gcloud auth print-access-token` work on the runner the same way it works locally. No env var fallback or alternative code path needed.

gapp needs `gcloud` on the runner because it shells out to `gcloud` for everything — `gcloud builds submit`, `gcloud storage`, `gcloud secrets`, `gcloud services enable`, `gcloud artifacts`, etc. (~30 subprocess calls across the SDK). If gapp used Google Cloud Python SDKs instead, Application Default Credentials alone would suffice and `gcloud` wouldn't be needed. But that's a potential future refactor, not a prerequisite for CI support.

## The Operator's Private Repo

The operator's private repo (e.g., `<your-ci-repo>`) is minimal. Per solution, it contains one workflow file, written by `gapp ci setup`:

```yaml
# .github/workflows/example-app.yml
on:
  workflow_dispatch:
    inputs:
      ref:
        description: 'Version/tag/SHA to deploy'
        default: 'main'

jobs:
  deploy:
    uses: <gapp-owner>/gapp/.github/workflows/deploy.yml@<gapp-release-tag>
    with:
      solution-repo: <owner>/example-app
      ref: ${{ inputs.ref }}
      # The CI repo's GitHub Environment named after the GCP project; it holds
      # the deploy identity (GAPP_WIF_PROVIDER, GAPP_DEPLOY_SERVICE_ACCOUNT),
      # recorded by the first `gapp ci setup` on that project:
      environment: <your-project-id>
      gapp-repo: <gapp-owner>/gapp
      gapp-ref: <gapp-release-tag>
      solution-name: example-app
      # Written by `gapp ci setup --domain`; overrides gapp.yaml at deploy time:
      domain: mcp.example.com
      # Written only when the solution repo is private (see "Private solution repos"):
      solution-repo-app-id: ${{ vars.HOSTING_GITHUB_APP_ID }}
    secrets:
      solution-repo-app-private-key: ${{ secrets.HOSTING_GITHUB_APP_PRIVATE_KEY }}
```

That's it. No boilerplate. Each file says something different — which solution, which project (through its Environment), which domain. The reusable workflow in gapp handles all the logic, and the per-project identity lives once, in the Environment, not copied into every file (see "Per-project deploy identity: the Environment record").

Deployment values such as the domain belong here, not in the solution repo's `gapp.yaml`: the `domain:` input wins over `gapp.yaml` at deploy time, so a public solution repo can carry a placeholder or no domain at all. Set it with `gapp ci setup --domain`, never by hand-editing the workflow: `gapp ci setup` rewrites the whole file, and it keeps the existing `domain:` only because it reads it back from the file first. Passing `--domain ""` removes it.

### Why deployments are manual by default

`gapp ci setup` generates workflows with `workflow_dispatch` only — no automatic triggers. This is deliberate:

1. **Operator controls when code reaches production.** A push to a solution repo is a code change, not a deployment decision. The operator explicitly decides when a new version is ready for their infrastructure via `gapp ci trigger` (which calls `workflow_dispatch`). This keeps deployments stable and intentional.

2. **Solution repos are not deployment pipelines.** Adding push-triggered workflows to solution repos would couple them to a specific operator's CI repo, violating the open/closed principle. Solution repos must remain clean, reusable products that anyone can deploy to their own infrastructure without modification.

3. **No access to the solution repo required.** An operator should be able to deploy any public solution repo — even one they don't own — without admin access to it. GitHub has no native mechanism to watch another repo for pushes without either (a) adding a webhook (requires admin), (b) installing a GitHub App (requires owner), or (c) adding a workflow to that repo (requires write access). The only zero-access approach is polling.

4. **Security by default.** Auto-deploying on push to a third-party public repo means someone else's commit deploys to your infrastructure. Even for repos you own, explicit triggering is the safer default.

### Optional: auto-deploy via polling

For operators who want automatic deployments, a scheduled poller workflow in the CI repo can check each solution repo for new commits and dispatch the corresponding workflow. This is the only approach that:

- Requires no access to the solution repo (public API read only)
- Keeps solution repos untouched
- Lives entirely in the operator's private CI repo
- Works for repos the operator doesn't own

The tradeoff is a polling interval (e.g., 5-15 minutes) rather than instant triggers. This is acceptable for personal tooling and stays well within GitHub Actions' free tier.

This is not yet implemented — see the GitHub issue for tracking.

### Trigger options (operator's choice)

For operators who want to customize beyond the default, the workflow YAML in the CI repo is plain GitHub Actions configuration. The operator can add triggers:

- **`workflow_dispatch`** (default) — manual trigger from GitHub UI, `gh` CLI, API, or `gapp ci trigger`. Best for third-party solution repos you don't own.
- **`schedule`** — poll for new versions on a cron. The only auto-deploy option that works without access to the solution repo.
- **Push-triggered** — only possible if the operator owns the solution repo AND moves the workflow there (breaks the three-layer separation, not recommended).
- **`repository_dispatch`** — webhook-triggered, but requires a sender (webhook on solution repo = requires admin access, or external infrastructure).

The default (`workflow_dispatch` only) is the right choice for most operators. It's the only trigger that works universally — regardless of who owns the solution repo, without infrastructure, and without compromising the product-level cleanliness of any public repo.

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
- **GitHub token scoping**: The run's built-in `GITHUB_TOKEN` can read only the CI repo it runs in. That is enough to clone a public solution repo. A private solution repo is cloned with a separate token minted at the start of the run from a GitHub App the operator installs on the solution repo's owner: limited to that one repo, read-only Contents, valid for an hour, and discarded with the run (see "Private solution repos"). Neither token can reach any other repo.
- **No setup permissions in CI**: The deploy service account should NOT have permissions to run `gapp setup`, create new WIF pools, access other GCP projects, or enable APIs. It can only deploy — build containers, apply Terraform, read secrets. This prevents a compromised workflow from bootstrapping access to other resources.
- **No runaway automation**: The reusable workflow in gapp runs `gapp deploy`, not `gapp setup`. Even if malicious code were injected into the workflow, the service account's scoped permissions prevent it from accessing other projects, creating new trust relationships, or escalating privileges via `gh` (since the GitHub token is also scoped).

## gapp Ships a Reusable GitHub Workflow

The reusable workflow lives in the gapp repo at `.github/workflows/deploy.yml`. It:

1. Accepts inputs: `repo`, `ref`, `project-id`, WIF config
2. Authenticates via WIF (`google-github-actions/auth` — exchanges GitHub OIDC token for GCP access token)
3. Installs and configures gcloud (`google-github-actions/setup-gcloud` — required because gapp shells out to `gcloud` for builds, secrets, storage, etc.)
4. Clones the solution repo at the specified ref
5. Installs gapp
6. Runs `gapp deploy --ref <ref>`

This is a product asset, like the Dockerfile template or Terraform modules. The operator's workflow file is a thin caller that passes values.

GitHub natively supports this via [reusable workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows). The called workflow declares `on: workflow_call` with inputs, and the caller uses `uses: owner/repo/.github/workflows/file.yml@ref`.

### Private solution repos

A private solution repo is supported. The principle "public repos must not depend on private repos" is untouched: gapp, its reusable workflow, and the pattern stay public, and a private solution repo is simply the operator's own deployable code. What changes is only how the runner clones it.

**The problem.** The reusable workflow checks the solution repo out with the run's built-in `GITHUB_TOKEN`, which can read only the CI repo itself. For a private solution repo the checkout fails with "Repository not found", and a caller cannot add steps inside a reusable workflow's job, so there was no way to pass another credential.

**The mechanism.** The reusable workflow takes an optional input `solution-repo-app-id` and an optional secret `solution-repo-app-private-key`. When both are set, the run mints an installation token with `actions/create-github-app-token`, scoped to the solution repo (owner and repo name), read-only Contents, one hour, and hands it to `actions/checkout`. The private key is read once by that step and never written to the checkout. When they are unset, the workflow behaves exactly as before.

**Security model.** The GitHub App's installation list is the boundary: a token can only ever be minted for a repo the app is installed on, and the workflow further narrows each token to the one solution repo being deployed, read-only, for an hour. So:

- install the app on an organization that holds only deployable repos, and nothing else;
- never install it on all repositories of a personal account, where it would reach dotfiles, notes, and anything else that lives there;
- store the private key only as an Actions secret of the CI repo (GitHub never returns it), and delete the downloaded key file once it is stored;
- give the app Contents read-only and no webhook; it needs nothing else.

**App setup (once per operator, in a browser).**

1. On the GitHub organization that holds the private deployable repos: Settings → Developer settings → GitHub Apps → New GitHub App. Permissions: Repository → Contents: Read-only (Metadata read-only is implied). Webhook: off. Installation: only on this account.
2. Install the app on that organization, all repositories (or the deployable ones). Note the App ID.
3. Generate a private key; it downloads once as a `.pem` file.
4. Store both in the CI repo as an Actions variable and secret, then delete the key file:

   ```bash
   gh variable set HOSTING_GITHUB_APP_ID --repo <owner>/<ci-repo> --body <app-id>
   gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo <owner>/<ci-repo> < <downloaded-key>.pem
   rm <downloaded-key>.pem
   ```

`HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` are fixed names, like `GITHUB_TOKEN`: the credentials belong to the CI repo, one pair per operator, and nothing about a solution could want different ones, so there is no option to rename them. `gapp ci init` checks for both and prints the two commands above when either is missing, so the operator learns about the step before the first private solution repo needs it; it never sets or overwrites them. A second credential pair, if one were ever needed, would be per solution-repo owner (a second organization with its own app), not per solution; that is not supported.

**What gapp does with it.**

- `gapp ci init` reports whether the CI repo holds the variable and secret and, when either is missing, returns the two `gh` commands as `next_step`, noting that only private solution repos need them.
- `gapp ci setup` reads the solution repo's visibility from the GitHub API. For a private repo it writes the two lines into the caller workflow (`solution-repo-app-id` from the Actions variable, `solution-repo-app-private-key` from the Actions secret), checks that the variable and secret exist in the CI repo, and returns a `next_step` naming the missing one. The workflow is written either way; only the trigger has to wait.
- `gapp ci status` reports the solution repo's visibility, the variable and secret the workflow names, and whether each exists in the CI repo. A workflow written before this feature, for a repo that is now private, gets `next_step: ci setup`.
- `gapp ci trigger` refuses up front, with the same `next_step`, for a private repo whose workflow lacks the app inputs or whose CI repo lacks the variable or secret, instead of dispatching a run that fails at checkout.

### GitHub Enterprise compatibility

All `gapp ci` commands interact with GitHub exclusively through the `gh` CLI, which handles multi-host authentication natively — including GitHub Enterprise Server and GitHub Enterprise Cloud. gapp never talks to GitHub directly, so it works with any GitHub instance that `gh` is authenticated against. No Enterprise-specific configuration or code paths are needed.

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
gapp ci init <repo>                               # optional: designate CI repo (once per operator)
gapp ci setup <repo>                              # optional: wire solution for CI (once per solution)
gapp ci status                                    # optional: check CI state
```

`gapp setup` is unchanged — pure GCP foundation (APIs, bucket, label).

`gapp ci` is a new command group that owns the entire CI concern, split into two phases:

#### `gapp ci init <repo>`

One-time setup per operator. Designates the CI repo — where deployment workflows live.

The `<repo>` argument accepts a repo name or owner/name. If only a name is given, the owner defaults to the authenticated `gh` user. Examples: `<your-ci-repo>`, `myuser/<your-ci-repo>`.

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

Unless `--local-only`, it also reports whether the CI repo holds the hosting app's fixed Actions variable `HOSTING_GITHUB_APP_ID` and secret `HOSTING_GITHUB_APP_PRIVATE_KEY`, which only private solution repos need, and prints the two commands that store them when either is missing (see "Private solution repos"). It reads, never writes, those values; re-running changes nothing.

Prerequisites: `gh` CLI authenticated (unless `--local-only`).

#### `gapp ci setup <repo>`

Per-solution CI wiring. The `<repo>` argument is the solution repo to wire up — accepts repo name or owner/name. Since the solution repo may not be owned by the operator, owner/name is typical (e.g., `someuser/monarch-access`). If only a name is given, the owner defaults to the authenticated `gh` user.

Does everything needed to deploy this solution via CI:

1. Discovers the operator's CI repo from local XDG config (errors if `gapp ci init` hasn't been run)
2. Decides whether GCP is needed. With `--project <id>` and that project's
   Environment already recorded in the CI repo (see "Per-project deploy
   identity: the Environment record"), steps 4 to 8 are skipped and no
   gcloud call is made. Otherwise the configured gapp account must be
   logged in, and the project is found by label (or verified, with
   `--project`)
3. Reads the solution repo's visibility; a private repo gets the hosting
   app's credentials wired in (see "Private solution repos")
4. Creates WIF pool + provider in the GCP project (idempotent, first run only per project)
5. Creates `gapp-deploy` service account with scoped permissions (idempotent, first run only per project)
6. Adds IAM binding: CI repo can impersonate the deploy SA (idempotent)
7. Records the deploy identity in the CI repo: a GitHub Environment named
   after the project id with `GAPP_WIF_PROVIDER` and
   `GAPP_DEPLOY_SERVICE_ACCOUNT` (created or updated through the API)
8. Reads the existing workflow file, if any, so its `domain:` survives a
   re-run; `--domain <host>` sets or changes it, `--domain ""` removes it
9. Generates the workflow file for this solution: `environment: <project-id>`,
   solution repo, domain, and (private repo) the app inputs; no per-project literals
10. Writes the workflow file to the CI repo through the GitHub Contents API
    (create or update by path; see "Why the workflow file is written through
    the API, not git" below)
11. For a private repo, checks that the CI repo holds the fixed Actions variable and secret, and returns a `next_step` naming any that is missing

Prerequisites: `gapp ci init` completed, `gapp setup <project-id>` completed for this solution, `gh` authenticated; `gcloud` authenticated as the configured account unless `--project` names an already-recorded project.

#### Resource scoping decisions

**WIF pool + provider: one per GCP project.** The pool is a container that says "this project accepts external identity federation." The provider points at GitHub's OIDC endpoint. Neither is tied to a specific repo or solution — they're project-level infrastructure that any number of solutions can share.

**Deploy service account: one per GCP project.** A single `gapp-deploy@{project}.iam.gserviceaccount.com` with the roles needed for deployment (Cloud Build, Cloud Run, Artifact Registry, etc.). Per-solution deploy SAs would be more isolated but add complexity with little benefit for a single operator. If multiple teams share a project and need isolation, per-solution SAs can be added later.

**IAM binding: one per operator repo.** This is the only repo-specific resource. It says "repo X can impersonate the deploy SA." Adding a binding is idempotent — adding the same one twice is a no-op.

**Workflow file: one per solution.** Generated by `gapp ci setup` and written to the operator's CI repo through the GitHub API.

Steps 2-4 are idempotent and skip if already done.

#### Per-project deploy identity: the Environment record

A project's deploy identity is two values every solution on that project shares: the WIF provider resource path and the deploy service account email. They are recorded once, as a GitHub **Environment in the CI repo, named after the GCP project id**, holding two Environment variables, rather than copied into each leaf workflow:

| Variable | Value |
|---|---|
| `GAPP_WIF_PROVIDER` | `projects/<project-number>/locations/global/workloadIdentityPools/github/providers/github` |
| `GAPP_DEPLOY_SERVICE_ACCOUNT` | `gapp-deploy@<project-id>.iam.gserviceaccount.com` |

- The first `gapp ci setup` on a project (gcloud required) creates or verifies the four GCP resources and writes the Environment through the GitHub API, the same way it writes the workflow file. Re-running is idempotent: unchanged values are left alone.
- Every generated workflow passes `environment: <project-id>` to the reusable workflow. A job that calls a reusable workflow cannot declare an environment itself, so the reusable workflow's job does (`environment: ${{ inputs.environment }}`), and reads the two variables from that Environment of the caller repo. The caller carries no per-project literals.
- `gapp ci setup --project <id>` on a project whose Environment is complete makes **no GCP call**: it writes the workflow and nothing else, so a new solution on an already-set-up project needs `gh` only. Without `--project`, or when the Environment or a variable is missing, the gcloud path runs and records (or repairs) the Environment on the way.
- Nothing is parsed out of existing workflows. The Environment, written by gapp and read by gapp, is the record. Existing workflows that carry literals keep working until regenerated; regenerating one moves it to the Environment form.
- `gapp ci status` shows the Environment a workflow deploys through and whether its two variables exist, and `gapp ci trigger` refuses up front when one is missing.

#### Why the workflow file is written through the API, not git

Earlier versions delivered the workflow file by cloning the CI repo to a
temp directory, committing with hooks disabled, and pushing. That made gapp
a git client acting as the operator on the operator's machine, and it broke
on any machine with a pre-push gate: the commit carried none of the marks
the gate required, the push was refused, and the temp clone was discarded,
so the operator could not even repair the commit. Disabling hooks at commit
time was itself the wrong instinct: an operator's hooks are a control they
chose, and a tool has no business switching them off.

The workflow file is generated configuration, not authored content. Every
value in it is already known to gapp (solution name and repo, project id
and number, gapp release tag); there is no free-form text and, by gapp's
secrets invariant, no credential can appear in it. Content of that kind
belongs to the same class as the topic `gapp ci init` writes: a tool
updating a config store. So gapp writes it the way it does every other
GitHub operation, with one API call that creates or updates the file by
path. GitHub makes the commit server-side; nothing on the operator's
machine is cloned, committed, or pushed, and the operator's local git
hooks are neither triggered nor bypassed, because no local git operation
occurs. Re-runs are idempotent for free: the API takes the file's current
SHA and either updates it or reports it unchanged.

What this deliberately does not cover: the operator's own content in the
CI repo (notes, design records, anything a human wrote). That still goes
through the operator's normal commit process and whatever scanning it
carries. gapp never writes anything but its own generated files.

In an environment where gapp has no GitHub transport (a cloud agent
session where `gh` is not logged in), an agent with GitHub tools makes
the same API call with the same rendered content. The GCP half of
`ci setup` (pool, provider, service account, binding) is unaffected and
still needs the operator's identity.

#### `gapp ci status`

Shows the state of CI configuration. Discovers the CI repo from local config, then the `gapp-ci` topic using `gh`, then reports for the current solution:

- Which repo is the CI repo
- Whether this solution has a workflow file
- The solution repo the workflow deploys and whether it is private
- The `domain:` the workflow passes, if any
- The GitHub Environment (project id) the workflow deploys through and whether its `GAPP_WIF_PROVIDER` and `GAPP_DEPLOY_SERVICE_ACCOUNT` variables exist
- For a private solution repo, the Actions variable and secret the workflow names and whether each exists in the CI repo
- `next_step`, when a CI deploy would fail as things stand

The readiness check behind `gapp ci status` is the one `gapp ci trigger` runs before dispatching, so the two never disagree.

Future: `gapp ci logs`.

The `next_step` after `gapp deploy` can suggest: `"To enable CI/CD: gapp ci init <repo-name>"`

## Context Resolution in CI

### How project ID resolution works

`resolve_solution()` resolves context in two ways:
1. Explicit name → looks up `solutions.yaml` for project_id and repo_path
2. Current directory → finds git root, reads `gapp.yaml`, looks up `solutions.yaml`

`solutions.yaml` is local XDG config (`~/.config/gapp/solutions.yaml`). It's a cache populated by `gapp setup`, not a source of truth. The source of truth is GCP project labels (`gapp-{name}=default`).

On a CI runner, there's no `solutions.yaml`. The reusable workflow handles this by:

1. Cloning the solution repo (restores cwd-based resolution — `gapp.yaml` is present)
2. Running `gapp setup` (no project ID arg — discovers the project via GCP label query, populates `solutions.yaml` on the runner, all steps are idempotent no-ops if already done)
3. Running `gapp deploy` (reads `solutions.yaml` populated by step 2, works as-is)

No `--project` flag needed. `gapp setup` already does GCP label discovery via `GappSDK.resolve_project_for_solution` in `core.py`. Running it first on the runner bootstraps the local cache that all other commands depend on. This is the same thing you'd do on a new workstation. If `gapp setup` has never been run for a project at all, `gapp deploy` errors with a clear message telling the operator to run setup first — that's existing behavior and it's fine.

### The `--solution` flag

The SDK's `resolve_solution(name)` supports explicit name lookup, but the CLI is inconsistent about exposing it:

| Accepts solution name | Hardcoded to cwd (no name parameter) |
|---|---|
| `status [name]` (positional arg) | `deploy` |
| `mcp status [name]` (positional arg) | `setup` |
| `mcp connect [name]` (positional arg) | `secrets list/set/add/remove` |
| `tokens create/revoke` (`--solution` option) | `users register/list/get/update/revoke` |

There's also an inconsistency in how the name is passed — sometimes a positional argument, sometimes `--solution`. The `tokens` commands use `--solution` because they already have a required positional argument (`email`).

The `--solution` flag (matching the existing `tokens` convention) should be added to commands that currently hardcode cwd:

- `gapp deploy --solution <name>`
- `gapp setup --solution <name>`
- `gapp secrets list/set/add/remove --solution <name>`
- `gapp users register/list/get/update/revoke --solution <name>`

This is optional everywhere — cwd remains the default. The flag is a convenience locally (operate on a solution without cd'ing into it) and becomes relevant for `gapp ci` commands that run from the operator's repo, not the solution repo.

For the commands that already use a positional `name` argument (`status`, `mcp status`, `mcp connect`), no change is needed — they already work.

## What Changes in gapp

### Code changes

1. **`gapp ci init` command** — designate and tag the operator's CI repo, write to XDG config
2. **`gapp ci status` command** — discover CI repo, report configuration state (SDK reused by `ci setup`)
3. **`gapp ci setup` command** — create WIF pool/provider/service account in GCP, add IAM binding, generate and push workflow file to CI repo
4. **Reusable workflow** — `.github/workflows/deploy.yml` in gapp repo
5. **`--solution` flag** — add to `deploy`, `setup`, `secrets *`, and `users *` (the commands that currently hardcode cwd)

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
3. Run `gapp ci setup --solution-repo <owner>/<repo> [--domain mcp.example.com]` (wire this solution for CI — creates WIF, SA, binding, records the project's Environment, writes the workflow; a private solution repo also needs the hosting app from "Private solution repos"). For the next solution on the same project, `gapp ci setup --project <id> ...` needs `gh` only.
4. Done. Trigger deployments from GitHub UI, CLI, or API. Check with `gapp ci status`.

For someone else using your public solution repo: clone nothing, fork nothing. Copy the example workflow from gapp's docs into your own repo, fill in your project ID and WIF config, and you're running. Two public products, one private glue repo of your own.
