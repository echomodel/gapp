# CI/CD and Remote Deployment

This document captures the design for enabling gapp deployments without a local machine — via GitHub Actions, Claude Code on the web, Codespaces, or any stateless environment.

---

## The Problem

Today, gapp requires a local machine with `gcloud auth login` configured. Every `gcloud` subprocess call (and there are ~30 across setup, deploy, secrets, users, tokens, and status) relies on ambient gcloud authentication. Terraform gets its credentials via `gcloud auth print-access-token`, passed as `GOOGLE_OAUTH_ACCESS_TOKEN`.

This ties the operator to their laptop. You can't deploy from your phone, from Claude.ai, from a CI runner, or from a colleague's machine without first configuring gcloud credentials. For a tool whose philosophy is "four commands and you're deployed," this is an unnecessary anchor.

## Two Fleet Systems, One Rigid Line

gapp has **two fleet systems that do not talk to each other**:

- **Root commands** (`gapp setup`, `deploy`, `status`, `secrets`, ...) are the **direct** system. They need a Google Cloud identity. Their record of where a solution lives is the GCP project label. They never read GitHub.
- **`gapp ci` commands** are the **CI** system. They need GitHub (write access to the CI repo for `ci init`; workflow dispatch for the rest). Their record is `gapp/solutions.yaml` in the operator repo and the project's GitHub Environment. They never read GCP, except `gapp ci project setup`, the one command that touches both, once per project.

A solution is deployed by one system. No command consults the other system's state, there is no shared local store, and no `--force` reconciliation. Every command's requirements are fixed by its name; nothing is conditional on hidden state. CONTRIBUTING, "Two fleet systems", records how this was decided and what was rejected.

| Command | System | Fixed requirements | How often |
|---|---|---|---|
| `gapp setup`, `deploy`, `status`, `secrets`, `projects` | direct | gcloud signed in (deploy: terraform, clean tree) | per solution |
| `gapp ci init <owner>/<repo>` | CI | `gh` that can write to the CI repo (GitHub API) | once per operator |
| `gapp ci project setup <project>` | CI, and the one touch of GCP | gcloud signed in as an Owner of the project; `gh` signed in as an admin of the CI repo | once per project |
| `gapp ci sources add\|remove\|list [<owner>]` | CI | `gh` with write access to the CI repo (read for `list`) | once per source owner |
| `gapp ci setup --project <project> [--domain <d>]` | CI | `gh` that can dispatch workflows in the CI repo | once per solution |
| `gapp ci deploy [--ref <r>]` | CI | `gh` that can dispatch workflows in the CI repo | any time |
| `gapp ci status` | CI | `gh` that can read the CI repo | any time |

Every command ends in a plain outcome: it did the thing, or it states what stands in the way and the exact next step. No stack trace, raw HTTP error or raw gcloud error reaches the user; the SDK returns `ok` and `next_step` in every result, the CLI prints the step and exits 1, and the MCP tools return the same dict.

### The end-to-end story for a cloud session

A cloud agent session typically has no gcloud identity and a GitHub proxy that refuses API writes but allows dispatch. In that session the whole CI system is three commands: `gapp ci setup --project <project>` (register; the run writes the entry with its own token), `gapp ci deploy` (deploy), `gapp ci status` (where things stand). Nothing else is needed there. When one of the two one-time steps is missing, `ci setup` says so verbatim:

```
CI repo <owner>/<repo> is not yet trusted to deploy to GCP project <project>. One-time setup:

1. Run `gapp ci project setup <project>`
   - Requires gcloud signed in as an Owner of <project>
   - Requires `gh` signed in as an admin of <owner>/<repo>
2. Run this command again: gapp ci setup --project <project>
```

```
Source repos owned by <owner> are not yet allowed to deploy through CI repo <ci-owner>/<ci-repo>. One-time setup:

1. Run `gapp ci sources add <owner>`
   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo <ci-owner>/<ci-repo> --body <current-list>,<owner>
   - Requires `gh` signed in with write access to <ci-owner>/<ci-repo>
2. Run this command again: gapp ci setup --project <project>
```

`<current-list>` is rendered literally: `ci setup` makes no extra read for it, nothing from the list reaches run logs, and it makes plain that the `gh` route replaces the whole list. Step 2 repeats the user's exact command line.

## Core Philosophy: Workstation Once, Then Never

The operator's workstation is needed exactly once per project — to run `gapp setup` (direct system) and `gapp ci project setup` (CI system), which require personal GCP and GitHub credentials. These are bootstrap commands that establish trust relationships (WIF, IAM bindings) and record configuration (the project's Environment in the CI repo). After that, the workstation is never needed again: registering solutions and deploying both happen through GitHub Actions, triggered from any device — GitHub UI, mobile, `gh` CLI, an agent session, or an API call.

This mirrors how `gapp setup` already works for GCP foundation — you run it once to establish the project, then `gapp deploy` works forever after. CI follows the same shape: `gapp ci init` once per operator, `gapp ci project setup` once per project, `gapp ci sources add` once per source owner, and from then on every `gapp ci setup` and `gapp ci deploy` is a workflow dispatch.

Once CI is configured, tools like Claude.ai, Claude mobile app, Claude Code on the web, or GitHub's web editor can make code changes to a solution repo, push them, and trigger deployment to GCP — all without access to GCP credentials or a local development environment. The code change and the deployment are fully decoupled from the operator's machine. The operator's role shifts from "person who deploys" to "person who configured the deployment pipeline once."

### Why bootstrap stays on the workstation

It would be possible to remove the one-time workstation step too: a hub project with a Workload Identity pool trusted by the operator's CI repo, a bootstrap service account granted admin roles at the folder level, and a workflow that runs `gapp setup` and `gapp ci setup` on a runner for any new project. That is deliberately not done. It replaces a per-project, deploy-only identity with one cross-project admin identity reachable from a GitHub repo, which contradicts goal 7 below (the deploy identity cannot set up new projects, access other projects, or escalate), and it saves only a few minutes per new project. The per-project `gapp setup` and `gapp ci project setup` on a logged-in workstation are the intended cost of keeping each project's blast radius to itself. Revisit only if the bootstrap step itself can be scoped to one project at a time.

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
| **Operator config** (private repo) | Private, per-operator | The solutions data file (solution → repo, project, domain), per-project Environments holding the WIF references, and gapp's two workflows that wire tool + application to infrastructure |

The operator config is the only place where "deploy this specific solution to this specific GCP project" is expressed. This is the `<your-ci-repo>` repo pattern.

### The operator repo is not intellectual work

The operator's private repo is an address book, not a product. It maps "solution X → GCP project Y." There's nothing reusable or interesting in it. The interesting parts are in gapp (the reusable workflow) and in the solution repos (the applications). The private repo is configuration: one data file of entries, each saying something different (which solution, which repo, which project, which domain), plus two gapp-owned workflows that read and write that file.

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

The operator's private repo (e.g., `<your-ci-repo>`) holds three gapp-owned files, installed by `gapp ci init`:

| File | Written by | Role |
|---|---|---|
| `.github/workflows/gapp-deploy.yml` | `gapp ci init` (installed and updated) | The one deploy workflow. Dispatched with a solution name and a ref. Its first job reads the solution's entry from the data file; its second job runs in the GitHub Environment named after the entry's project and calls gapp's reusable workflow with the entry's values. |
| `.github/workflows/gapp-register.yml` | `gapp ci init` (installed and updated) | The register workflow. Dispatched by `gapp ci setup` with solution, repo, project and domain. Validates, then writes the entry into the data file with the run's own token. |
| `gapp/solutions.yaml` | `gapp ci init` (created empty), then the register workflow | The data file: one entry per solution. |

The data file looks like this:

```yaml
# gapp/solutions.yaml
solutions:
  - name: example-app
    repo: <owner>/example-app
    project: <your-project-id>        # the GitHub Environment of the same name holds the deploy identity
    domain: mcp.example.com           # optional; overrides gapp.yaml at deploy time
  - name: other-app
    repo: <owner>/other-app
    project: <your-project-id>
    gapp_ref: v3.9.1                  # optional; the deploy workflow's own pin otherwise
```

That's it. Each entry says something different — which solution, which repo, which project, which domain. Everything the entries have in common lives once: the reusable workflow in gapp handles the deploy logic, the generic deploy workflow pins the gapp release once for the whole repo, and the per-project identity lives in the project's Environment (see "Per-project deploy identity: the Environment record"). Nothing in the repo is hand-edited: `gapp ci init` writes the workflows and the register workflow writes the entries, so a re-run of `gapp ci init` after a gapp upgrade is safe (it rewrites only the two workflows and leaves the data file alone).

Deployment values such as the domain belong in the entry, not in the solution repo's `gapp.yaml`: the entry's domain wins over `gapp.yaml` at deploy time, so a public solution repo can carry a placeholder or no domain at all. Set it with `gapp ci setup --domain`; a re-run without `--domain` keeps the entry's domain, and `--domain ""` removes it.

### Why deployments are manual by default

The deploy workflow has `workflow_dispatch` only — no automatic triggers. This is deliberate:

1. **Operator controls when code reaches production.** A push to a solution repo is a code change, not a deployment decision. The operator explicitly decides when a new version is ready for their infrastructure via `gapp ci deploy` (which calls `workflow_dispatch`). This keeps deployments stable and intentional.

2. **Solution repos are not deployment pipelines.** Adding push-triggered workflows to solution repos would couple them to a specific operator's CI repo, violating the open/closed principle. Solution repos must remain clean, reusable products that anyone can deploy to their own infrastructure without modification.

3. **No access to the solution repo required.** An operator should be able to deploy any public solution repo — even one they don't own — without admin access to it. GitHub has no native mechanism to watch another repo for pushes without either (a) adding a webhook (requires admin), (b) installing a GitHub App (requires owner), or (c) adding a workflow to that repo (requires write access). The only zero-access approach is polling.

4. **Security by default.** Auto-deploying on push to a third-party public repo means someone else's commit deploys to your infrastructure. Even for repos you own, explicit triggering is the safer default.

### Optional: auto-deploy via polling

For operators who want automatic deployments, a scheduled poller workflow in the CI repo can check each registered solution repo for new commits and dispatch the deploy workflow with that solution's name. This is the only approach that:

- Requires no access to the solution repo (public API read only)
- Keeps solution repos untouched
- Lives entirely in the operator's private CI repo
- Works for repos the operator doesn't own

The tradeoff is a polling interval (e.g., 5-15 minutes) rather than instant triggers. This is acceptable for personal tooling and stays well within GitHub Actions' free tier.

This is not yet implemented — see the GitHub issue for tracking.

### Trigger options (operator's choice)

For operators who want to customize beyond the default, the CI repo is plain GitHub Actions configuration. gapp's two workflows are rewritten by `gapp ci init`, so customization goes in a workflow of the operator's own that dispatches `gapp-deploy.yml` (with `gh workflow run gapp-deploy.yml -f solution=<name> -f ref=<ref>`, or the `workflow_dispatch` API):

- **`workflow_dispatch`** (default) — manual trigger from GitHub UI, `gh` CLI, API, or `gapp ci deploy`. Best for third-party solution repos you don't own.
- **`schedule`** — poll for new versions on a cron and dispatch the deploy workflow. The only auto-deploy option that works without access to the solution repo.
- **Push-triggered** — only possible if the operator owns the solution repo AND adds a workflow there that dispatches the deploy workflow in the CI repo (couples the solution repo to one operator, not recommended).
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
- **Workflow pinning**: The deploy workflow pins gapp's reusable workflow to a release tag, written once per CI repo by `gapp ci init` and moved only by re-running it. Nothing in the data file can point a deploy at another copy of the reusable workflow.
- **Who can dispatch**: Dispatching a workflow in the CI repo requires write access to the CI repo. So "who can deploy" and "who can register a solution" are both exactly "who can write to the operator repo", and GitHub's audit log records every dispatch with its actor and inputs.
- **The source allowlist**: The register workflow accepts a solution repo only when its owner is listed in the CI repo's repository variable `GAPP_ALLOWED_SOURCES` (comma-separated; managed by `gapp ci sources`). Someone with write access to the CI repo cannot register, and therefore cannot deploy, code from an owner the operator has not named.
- **Inputs never reach a shell unquoted**: Both workflows pass their inputs to shell steps through `env:` and validate their shape (solution name, `owner/name`, project id, host name, git ref) before using them; nothing from an input is interpolated into a `run:` script.
- **The data file write is SHA-checked**: The register workflow writes `gapp/solutions.yaml` through the Contents API with the SHA it read at the start of the run, so two runs that race fail cleanly instead of overwriting each other.
- **A solution binds to one project**: The register workflow refuses to move a registered solution to a different project; the operator removes the entry first. A dispatch cannot quietly redirect a deployment.
- **New domain mappings need a verified owner**: Cloud Run maps a domain only for an identity that has verified it (or a parent) in Search Console. The deploy service account must be added there as a verified owner before a deploy can create a mapping for a new domain, so registering an entry with a new domain is not enough to take the domain; the Search Console step is the gate, and a redeploy that keeps the live domain needs nothing.
- **GitHub token scoping**: The run's built-in `GITHUB_TOKEN` can read only the CI repo it runs in. That is enough to clone a public solution repo. A private solution repo is cloned with a separate token minted at the start of the run from a GitHub App the operator installs on the solution repo's owner: limited to that one repo, read-only Contents, valid for an hour, and discarded with the run (see "Private solution repos"). Neither token can reach any other repo. The register run's token has `contents: write` on the CI repo alone, which is what it needs to write the data file.
- **No setup permissions in CI**: The deploy service account should NOT have permissions to run `gapp setup`, create new WIF pools, access other GCP projects, or enable APIs. It can only deploy — build containers, apply Terraform, read secrets. This prevents a compromised workflow from bootstrapping access to other resources.
- **No runaway automation**: The reusable workflow in gapp runs `gapp deploy`, not `gapp setup`. Even if malicious code were injected into the workflow, the service account's scoped permissions prevent it from accessing other projects, creating new trust relationships, or escalating privileges via `gh` (since the GitHub token is also scoped).

### Recommended operator protections

Two GitHub settings on the CI repo turn the model above into enforced policy. gapp does not set them; they are the operator's.

- **Branch protection with required pull requests on the default branch.** Then no one changes `gapp-deploy.yml`, `gapp-register.yml` or `gapp/solutions.yaml` by hand without a review. The register workflow's write and `gapp ci init`'s writes go through the same rule, so give them a way through: exempt the GitHub Actions app and the operator's own account in the rule's bypass list, or accept that each registration lands as a change the operator merges. The register run reports GitHub's refusal when the rule blocks it.
- **A required reviewer on each per-project Environment.** The deploy job runs in the Environment named after the project, so an Environment protection rule turns every deploy to that project into a run that waits for a named human's approval. This is the one-click way to get "a person approves each production deploy" without any change to gapp or the workflows.

## gapp Ships a Reusable GitHub Workflow

The reusable workflow lives in the gapp repo at `.github/workflows/deploy.yml`. It:

1. Accepts inputs: `repo`, `ref`, `project-id`, WIF config
2. Authenticates via WIF (`google-github-actions/auth` — exchanges GitHub OIDC token for GCP access token)
3. Installs and configures gcloud (`google-github-actions/setup-gcloud` — required because gapp shells out to `gcloud` for builds, secrets, storage, etc.)
4. Clones the solution repo at the specified ref
5. Installs gapp
6. Runs `gapp deploy --ref <ref>`

This is a product asset, like the Dockerfile template or Terraform modules. The operator repo's deploy workflow is a thin caller that looks a solution up and passes its values.

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

- `gapp ci init` reports whether the CI repo holds the variable and secret and, when either is missing, returns the two `gh` commands as `next_step`, noting that only private solution repos need them. Where the Actions variables and secrets API is not reachable from the machine, the check is reported as not performed; the register and deploy runs perform it.
- The register workflow checks that the solution repo is reachable: public (readable with the run's token), or otherwise with both the variable and the secret present in the CI repo. A private repo without them is refused with a structured result (`repo-unreachable`) naming the two, which `gapp ci setup` prints verbatim with the run link.
- The deploy workflow's first job reads the solution repo's visibility at run time. A repo the run's token cannot read gets the hosting app's id passed to the reusable workflow, which mints the token and clones with it; a public repo is cloned with the run's own token. No per-solution flag records visibility, so a repo that turns private later keeps deploying once the app covers it. A missing `HOSTING_GITHUB_APP_ID` is named in words on that first step.
- `gapp ci status` reports whether the variable and secret exist in the CI repo (or that they could not be checked from this machine).

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
gapp setup <project-id>                           # GCP foundation only (direct system)
gapp secret set <name>                            # prerequisites (direct system)
gapp deploy                                       # local deploy (direct system)
gapp ci init <repo>                               # CI: designate the CI repo and install gapp's files (once per operator)
gapp ci project setup <project>                   # CI: trust the CI repo for a project (once per project; the one gcloud step)
gapp ci sources add <owner>                       # CI: allow a source owner (once per owner)
gapp ci setup --project <project>                 # CI: register a solution (once per solution)
gapp ci deploy                                    # CI: deploy a registered solution
gapp ci status                                    # CI: where things stand
```

`gapp setup` is unchanged — pure GCP foundation (APIs, bucket, label).

`gapp ci` is a command group that owns the entire CI system:

#### `gapp ci init <repo>`

One-time setup per operator. Designates the CI repo and installs gapp's files in it.

The `<repo>` argument accepts a repo name or owner/name. If only a name is given, the owner defaults to the authenticated `gh` user. Examples: `<your-ci-repo>`, `myuser/<your-ci-repo>`.

What it does:
1. Writes the CI repo name to local XDG config (`~/.config/gapp/ci.yaml`). This is the authoritative local setting.
2. Tags the repo with a `gapp-ci` GitHub topic (for discoverability on other machines), creating the repo as private if it does not exist.
3. Ensures exactly one repo is tagged for the authenticated `gh` user. If a repo with the topic already exists and the name doesn't match, it errors — one CI repo per operator.
4. Installs `.github/workflows/gapp-deploy.yml` and `.github/workflows/gapp-register.yml` through the GitHub Contents API, pinned to the installed gapp's release tag, writing each only when missing or different (so re-running after a gapp upgrade updates them and re-running otherwise changes nothing); and creates `gapp/solutions.yaml` empty when missing, never touching it otherwise.
5. Reports whether the CI repo holds the repository variable `GAPP_ALLOWED_SOURCES`, the Actions variable `HOSTING_GITHUB_APP_ID` and the Actions secret `HOSTING_GITHUB_APP_PRIVATE_KEY`, and returns the commands for the missing ones as `next_step` (`gapp ci sources add <owner>` for the first; `gh variable set` / `gh secret set` for the other two). It reads, never writes, those values. The allowlist is needed before the first registration; the hosting app's pair only for private solution repos.

**`--local-only`**: Skips everything on GitHub. Only writes to XDG config. Useful when:
- You don't want to modify topics on the repo
- You're in a GitHub organization or enterprise where topic management is restricted
- You want to work without `gh` CLI configured
- You're testing or working across multiple GitHub accounts

This is the prerequisite for all other `gapp ci` commands. It establishes "where do my deployment workflows live?" — locally via XDG config (always), and remotely via GitHub topic (optionally).

Prerequisites: `gh` CLI authenticated with write access to the CI repo (unless `--local-only`); the help text says so. A machine whose GitHub access refuses API writes (some agent sessions) cannot install the files; `next_step` then names them and says to run `gapp ci init` from a machine that can.

#### `gapp ci project setup <project>`

Once per project, idempotent, and the only `gapp ci` command that touches GCP. Makes the CI repo trusted to deploy to the project:

1. Checks that `gh` is signed in as an admin of the CI repo (its owner, for a personal repo), before any GCP call
2. Checks that gcloud is installed and that the configured gapp account has a login
3. Creates or confirms the Workload Identity pool and provider and the `gapp-deploy` service account in the project, and binds the pool to the CI repo (idempotent)
4. Records the deploy identity in the CI repo as the GitHub Environment named `<project>` with `GAPP_WIF_PROVIDER` and `GAPP_DEPLOY_SERVICE_ACCOUNT` (see "Per-project deploy identity: the Environment record")

Requirements, stated in `--help` and in every failure: gcloud signed in as an Owner of the project; `gh` signed in as an admin of the CI repo. Every failure is a plain outcome: gcloud missing (run it where gcloud is; every other `ci` command works here), gcloud not an Owner (permission denied, translated), gcloud without a working login, project not found, `gh` not signed in or not an admin, and a refused Environment write after the GCP steps (which are idempotent, so the command is simply run again once `gh` is right).

#### `gapp ci sources add|remove|list [<owner>]`

Manages the CI repo's repository variable `GAPP_ALLOWED_SOURCES`, the comma-separated GitHub owners whose repos the register workflow accepts. `add` creates the variable or appends; `remove` rewrites it, deleting it when empty; `list` reads it. Adding an owner already present, or removing one absent, is a no-op with a note. Needs `gh` with write access to the CI repo (read access for `list`); a refused write is an outcome that names the requirement and the equivalent `gh variable set` command.

#### `gapp ci setup --project <project>`

Per-solution registration. Never uses gcloud. The solution is the current checkout's, or `--solution <name>`; its repo is read from the checkout's `origin` remote, or given as `--solution-repo <owner/name>` (owner/name is typical, since the solution repo may not be the operator's; a bare name defaults to the authenticated `gh` user). `--project` is required: the CI system never looks a project up in GCP.

What it does:

1. Discovers the operator's CI repo from local XDG config (`next_step: ci init` if `gapp ci init` hasn't been run) and checks that it holds the register workflow (otherwise `next_step: ci init`)
2. Checks that the CI repo has an Environment named after the project, through the repo environments endpoint (readable wherever the repo is). If not, stops with the "not yet trusted" message above, verbatim, with the user's exact command line in step 2
3. Reads the solution's existing entry, if any: a solution already registered on a different project is refused (a solution deploys to one project per CI repo; remove the entry first); the entry's domain survives a re-run, `--domain <host>` sets or changes it, `--domain ""` removes it
4. Dispatches `gapp-register.yml` with `solution`, `solution-repo`, `project` and `domain`. A refused dispatch is an outcome in words (`gh` not signed in; this machine's GitHub access not allowed to dispatch)
5. Waits for the run (`--no-watch` returns after the dispatch) and reads its structured result. `added`, `updated` or `unchanged`: registered. `source-owner-not-allowed`: the "not yet allowed" message above, verbatim. Any other refusal: the run's own words, verbatim, with the run link. A run that does not finish within the wait: the run link and what to check

Nothing is written through the GitHub API from this machine; the register run writes the entry with its own token. `ci setup` makes no read of the allowlist either: the run is the check, and the message renders `<current-list>` literally.

Prerequisites: `gapp ci init` and `gapp ci project setup <project>` completed, the source owner allowed, `gapp setup <project>` completed for this solution (direct system), `gh` authenticated to dispatch.

#### Resource scoping decisions

**WIF pool + provider: one per GCP project.** The pool is a container that says "this project accepts external identity federation." The provider points at GitHub's OIDC endpoint. Neither is tied to a specific repo or solution — they're project-level infrastructure that any number of solutions can share.

**Deploy service account: one per GCP project.** A single `gapp-deploy@{project}.iam.gserviceaccount.com` with the roles needed for deployment (Cloud Build, Cloud Run, Artifact Registry, etc.). Per-solution deploy SAs would be more isolated but add complexity with little benefit for a single operator. If multiple teams share a project and need isolation, per-solution SAs can be added later.

**IAM binding: one per operator repo.** This is the only repo-specific resource. It says "repo X can impersonate the deploy SA." Adding a binding is idempotent — adding the same one twice is a no-op.

**Workflows: two per CI repo.** The generic deploy workflow and the register workflow, installed by `gapp ci init`, shared by every solution.

**Data file entry: one per solution.** Written by the register workflow.

#### Per-project deploy identity: the Environment record

A project's deploy identity is two values every solution on that project shares: the WIF provider resource path and the deploy service account email. They are recorded once, as a GitHub **Environment in the CI repo, named after the GCP project id**, holding two Environment variables:

| Variable | Value |
|---|---|
| `GAPP_WIF_PROVIDER` | `projects/<project-number>/locations/global/workloadIdentityPools/github/providers/github` |
| `GAPP_DEPLOY_SERVICE_ACCOUNT` | `gapp-deploy@<project-id>.iam.gserviceaccount.com` |

- `gapp ci project setup <project>` (gcloud as an Owner required) creates or verifies the four GCP resources and writes the Environment through the GitHub API. Re-running is idempotent: unchanged values are left alone.
- The deploy workflow's deploy job passes `environment: <project>` (from the solution's entry) to the reusable workflow. A job that calls a reusable workflow cannot declare an environment itself, so the reusable workflow's job does (`environment: ${{ inputs.environment }}`), and reads the two variables from that Environment of the caller repo. Nothing in the CI repo carries per-project literals.
- The register workflow refuses a project whose Environment does not exist (`project-not-trusted`), so a solution can only be registered on a project the CI repo is trusted for. `gapp ci setup` makes the same check before dispatching, through the repo environments endpoint, which is readable wherever the repo is, and prints the "not yet trusted" message. The two variables are read through the Actions variables API only where that API is reachable; elsewhere they are reported as not checked from this machine, and the reusable workflow's first step names a missing one in words.
- `gapp ci status` shows, per registered solution, whether its project's Environment exists, and the deploy workflow's first job refuses a project without one, naming `gapp ci project setup`.

#### Why gapp's files are written through the API, not git

gapp is not a git client for the operator's repo. The two workflows and the data file are generated configuration, not authored content: every value in them is already known to gapp (the gapp release tag; a solution's name, repo, project and domain); there is no free-form text and, by gapp's secrets invariant, no credential can appear in them. Content of that kind belongs to the same class as the topic `gapp ci init` writes: a tool updating a config store. So they are written the way gapp does every other GitHub operation, with one API call that creates or updates the file by path. GitHub makes the commit server-side; nothing on the operator's machine is cloned, committed, or pushed, and the operator's local git hooks are neither triggered nor bypassed, because no local git operation occurs. Re-runs are idempotent for free: the API takes the file's current SHA and either updates it or reports it unchanged.

Two writers use that path. `gapp ci init` writes the workflows and creates the data file from the operator's machine, which therefore needs a `gh` login that can write to the CI repo. The register workflow writes the data file's entries from the runner with the run's own token (`contents: write`), which is why registering a solution needs no API write from the machine that asks for it: a session behind a proxy that refuses GitHub API writes, or that has no gcloud identity, still completes `gapp ci setup` by dispatching the run. There is no separate verify or pre-flight workflow: the register run validates what it writes, and the deploy run verifies the credential (its first step) and the project (`gapp setup` on the runner).

What this deliberately does not cover: the operator's own content in the CI repo (notes, design records, anything a human wrote). That still goes through the operator's normal commit process and whatever scanning it carries. gapp never writes anything but its own generated files.

#### `gapp ci status`

The CI system's complete report. Never touches GCP. Discovers the CI repo from local config, then the `gapp-ci` topic using `gh`, then reports:

- Which repo is the CI repo, and whether it holds gapp's two workflows and the data file, and whether the workflows are current with this gapp's templates
- Whether `GAPP_ALLOWED_SOURCES`, `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` exist (or that they could not be checked from this machine)
- Every registered solution with its repo, project, domain and gapp ref, and whether each project's Environment exists
- Solutions still deployed by a legacy per-solution workflow in the CI repo (see "Migration")
- For the current solution: whether it is registered, its project's Environment and the presence of the two variables
- `next_step`, naming the gap: missing or outdated files (`ci init`), no allowed sources (`ci sources add`), the solution not registered (`ci setup`), its project not trusted (`ci project setup`)

`gapp status` is the other system's report: it reads GCP labels and Terraform state, never GitHub, and never crashes for want of an identity: with no usable Google Cloud identity it says so and names the login command.

#### `gapp ci deploy`

Dispatches `gapp-deploy.yml` in the CI repo with `solution` (the current checkout's, or `--solution <name>`) and `ref`, and streams the run. A solution with no entry in the data file is not deployed: the outcome names `gapp ci setup`. A refused dispatch, a missing deploy workflow and a failed run (with its link) are outcomes too; the run's first steps name a missing Environment, Environment variable or App credential in words. `gapp ci trigger` is a hidden alias for one release that prints the new name.

#### Migration

An operator repo written by earlier gapp releases holds one caller workflow per solution. Those keep working untouched from the Actions tab: GitHub runs them as before. `gapp ci init` on such a repo adds the three gapp files beside them (and re-running it after a gapp upgrade updates the workflows, which the register workflow's variable rename needs). `gapp ci project setup <project>`, run once per existing project with gcloud, creates the project's Environment; `gapp ci sources add <owner>` allows the owner; registering an existing solution with `gapp ci setup --project <project>` writes its entry (the register run sees no conflicting entry), after which `gapp ci deploy` uses the generic deploy workflow and the operator deletes the old file when convenient. `gapp ci status` lists the solutions still on their own workflow.

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

1. **`gapp ci init` command** — designate and tag the operator's CI repo, write to XDG config, install the two workflows and the data file
2. **`gapp ci project setup` command** — create WIF pool/provider/service account in GCP, add IAM binding, record the Environment (the one GCP touch)
3. **`gapp ci sources` command** — manage the source allowlist variable
4. **`gapp ci setup` command** — dispatch the register workflow and print its structured result
5. **`gapp ci deploy` and `gapp ci status` commands** — dispatch the deploy workflow; report the CI system's state
6. **Reusable workflow** — `.github/workflows/deploy.yml` in gapp repo, and the two operator-repo workflow templates under `gapp/templates/ci/`
5. **`--solution` flag** — add to `deploy`, `setup`, `secrets *`, and `users *` (the commands that currently hardcode cwd)

### No changes needed

- All `gcloud` subprocess calls work as-is (the auth action configures gcloud on the runner)
- Terraform works as-is (already uses `GOOGLE_OAUTH_ACCESS_TOKEN`)
- Cloud Build works as-is (uses its own service account)
- Cloud Run service account works as-is (Terraform manages it)
- Solution repos — no changes at all

## Documentation for Users

The operator pattern is simple enough to be copy/paste documentation in gapp's README:

1. Run `gapp setup <project-id>` (GCP foundation, the direct system, same as always)
2. Run `gapp ci init <your-repo-name>` (designate your CI repo and install gapp's files, once per operator)
3. Run `gapp ci project setup <project-id>` (once per project; gcloud as an Owner, `gh` as an admin of the CI repo) and `gapp ci sources add <owner>` (once per GitHub owner whose repos may deploy)
4. Run `gapp ci setup --project <project-id> --solution-repo <owner>/<repo> [--domain mcp.example.com]` (register this solution; from any machine with `gh`). A private solution repo also needs the hosting app from "Private solution repos".
5. Done. `gapp ci deploy` from anywhere, or GitHub UI, CLI, or API with the solution's name. Check with `gapp ci status`.

For someone else using your public solution repo: clone nothing, fork nothing. Run the same steps against your own CI repo and project, and you're running. Two public products, one private glue repo of your own.
