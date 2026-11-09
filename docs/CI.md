# CI/CD and Remote Deployment

This document captures the design for enabling gapp deployments without a local machine — via GitHub Actions, Claude Code on the web, Codespaces, or any stateless environment.

---

## The Problem

Today, gapp requires a local machine with `gcloud auth login` configured. Every `gcloud` subprocess call (and there are ~30 across setup, deploy, secrets, users, tokens, and status) relies on ambient gcloud authentication. Terraform gets its credentials via `gcloud auth print-access-token`, passed as `GOOGLE_OAUTH_ACCESS_TOKEN`.

This ties the operator to their laptop. You can't deploy from your phone, from Claude.ai, from a CI runner, or from a colleague's machine without first configuring gcloud credentials. For a tool whose philosophy is "four commands and you're deployed," this is an unnecessary anchor.

## Two Fleet Systems, Kept Apart

gapp has **two fleet systems that do not talk to each other**:

- **Root commands** (`gapp setup`, `deploy`, `status`, `list`, `secrets`) deploy the local checkout to GCP with gcloud. Their record of where a solution lives is its labels in GCP (`gapp=hosted` on the project, `gapp-deployment` on its bucket and services). They never read GitHub.
- **`gapp ci` commands** manage the fleet through GitHub only. They never use gcloud (except `gapp ci project setup`, once per project) and **never read the working directory**. Their record is `gapp/deployments.yaml` in the fleet repo and the project's GitHub Environment.

No command consults the other system's state, there is no shared local store, and no `--force` reconciliation. Every command's requirements are fixed by its name; nothing is conditional on hidden state. The `ci` group mirrors the root group's verbs (`setup`, `deploy`, `status`, `list`) wherever both have the operation, and differs only where the systems really differ: `ci` has no working-directory default, so it takes names. CONTRIBUTING, "Two fleet systems" and "Rules of the CI system", records how this was decided, what was rejected and what is deferred.

| Command | Does | Needs |
|---|---|---|
| `gapp ci init` | Shows the saved fleet repo and how it was set; with none saved, finds the one repo under your account tagged `gapp-ci` (or lists several, or says how to create one) | `gh` |
| `gapp ci init --owner <org>` | The same search under that owner | `gh` |
| `gapp ci init <owner>/<repo>` | Sets an existing repo as the fleet repo for this machine and installs or updates gapp's files in it; a repo that does not exist stops with the `--create` command | `gh` that can write to the repo |
| `gapp ci init <owner>/<repo> --create` | Creates a private fleet repo, tagged, with the workflows and an empty deployments file, and sets it; a repo that exists stops with the plain `init` command | `gh` that can create repos |
| `gapp ci sources add\|remove\|list <owner>` | The GitHub owners whose repos may be deployed from the fleet | `gh` write |
| `gapp ci project setup <project>` | Once per GCP project: trust between the fleet repo and the project (WIF pool, provider, deploy account, binding, the project's Environment), and the project's `gapp=hosted` mark | gcloud as an Owner, `gh` as an admin |
| `gapp ci setup [<name>] --repo <owner>/<repo> --project <p> [--domain <d>]` | Adds or updates a deployment; the name defaults to the repo's name | `gh` that can dispatch |
| `gapp ci setup <name> --copy-of <existing> [--project <p>] [--domain <d>]` | A new deployment from an existing one | `gh` that can dispatch |
| `gapp ci rename <old> <new>` | Changes the name only | `gh` that can dispatch |
| `gapp ci deploy <name>` | Dispatches the generic deploy workflow | `gh` that can dispatch |
| `gapp ci status <name>` | One deployment: project, domain, last deploy; GitHub only, fast | `gh` that can read |
| `gapp ci status <name> --live` | Dispatches the read-only status workflow, which runs `gapp status` for the deployment on a runner with the project's deploy identity and returns the result | `gh` that can dispatch |
| `gapp ci list [--repo <owner>/<repo>]` | The deployments, optionally one repo's | `gh` that can read |

`--repo` always means the deployed app's repo, never the fleet repo. Every `ci` command takes `--verbose` (the fleet detail) and `--json` (the result dict, as the MCP tool returns it, and nothing else).

Every command ends in a plain outcome: it did the thing, or it states what stands in the way and the exact fix. No stack trace, raw HTTP error or raw gcloud error reaches the user; the SDK returns `ok` and `next_step` in every result, the CLI prints the step and exits 1, and the MCP tools return the same dict.

### From nothing to first deploy

```
gapp ci init                                   # finds your fleet repo, or says how to create one
gapp ci init <owner>/<fleet-repo> --create     # creates a private fleet repo and sets it
gapp ci sources add <owner>                    # allows the GitHub owner whose repos you deploy
gapp ci project setup <project>                # once per GCP project (gcloud as an Owner)
gapp ci setup <name> --repo <owner>/<repo> --project <project>
gapp ci deploy <name>
```

Each step's output names the next. Three fleet-repo messages are fixed:

```
No fleet repo is set on this machine. Start here: gapp ci init (It finds your existing fleet repo, or tells you how to create one.)
<owner>/<repo> doesn't exist. Create it as a new private fleet repo: gapp ci init <owner>/<repo> --create
<owner>/<repo> already exists, so it wasn't created. To use it as your fleet repo: gapp ci init <owner>/<repo>
```

### The end-to-end story for a cloud session

A cloud agent session typically has no gcloud identity and a GitHub proxy that refuses API writes but allows reads and dispatch. In that session the whole CI system is `gapp ci setup` (the register run writes the entry with its own token), `gapp ci deploy`, `gapp ci status` and `gapp ci list`. When one of the two one-time steps is missing, `ci setup` says so verbatim, after a first line naming the repo:

```
<owner>/<repo> can't be set up yet: your fleet isn't trusted to deploy to <project>.
Fleet repo <fleet-owner>/<fleet-repo> is not yet trusted to deploy to GCP project <project>. One-time setup:

1. Run `gapp ci project setup <project>`
   - Requires gcloud signed in as an Owner of <project>
   - Requires `gh` signed in as an admin of <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

```
<owner>/<repo> can't be set up yet: apps owned by <owner> aren't allowed in your fleet.
Apps owned by <owner> aren't allowed in your fleet yet (fleet repo <fleet-owner>/<fleet-repo>). One-time setup:

1. Run `gapp ci sources add <owner>`
   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo <fleet-owner>/<fleet-repo> --body <current-list>,<owner>
   - Requires `gh` signed in with write access to <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

`<current-list>` is rendered literally: `ci setup` makes no extra read for it, nothing from the list reaches run logs, and it makes plain that the `gh` route replaces the whole list. Step 2 repeats the user's exact command line.

## Core Philosophy: Workstation Once, Then Never

The operator's workstation is needed exactly once per project — to run `gapp ci project setup`, which requires personal GCP and GitHub credentials. That bootstrap command establishes a trust relationship (WIF, IAM bindings) and records configuration (the project's Environment in the fleet repo). After that, the workstation is never needed again: adding deployments and deploying both happen through GitHub Actions, triggered from any device — GitHub UI, mobile, `gh` CLI, an agent session, or an API call.

This mirrors how `gapp setup` already works for the direct system — you run it once to establish the project, then `gapp deploy` works forever after. CI follows the same shape: `gapp ci init` once per operator, `gapp ci project setup` once per project, `gapp ci sources add` once per source owner, and from then on every `gapp ci setup` and `gapp ci deploy` is a workflow dispatch.

Once CI is configured, tools like Claude.ai, Claude mobile app, Claude Code on the web, or GitHub's web editor can make code changes to a deployed repo, push them, and trigger deployment to GCP — all without access to GCP credentials or a local development environment. The code change and the deployment are fully decoupled from the operator's machine. The operator's role shifts from "person who deploys" to "person who configured the deployment pipeline once."

### Why bootstrap stays on the workstation

It would be possible to remove the one-time workstation step too: a hub project with a Workload Identity pool trusted by the operator's fleet repo, a bootstrap service account granted admin roles at the folder level, and a workflow that runs `gapp ci project setup` on a runner for any new project. That is deliberately not done. It replaces a per-project, deploy-only identity with one cross-project admin identity reachable from a GitHub repo, which contradicts goal 7 below (the deploy identity cannot set up new projects, access other projects, or escalate), and it saves only a few minutes per new project. The per-project `gapp ci project setup` on a logged-in workstation is the intended cost of keeping each project's blast radius to itself. Revisit only if the bootstrap step itself can be scoped to one project at a time.

## Goals

1. **Untether deployment from the local machine.** After one-time setup, an operator should never need their laptop to deploy. GitHub UI, `gh` CLI, Claude Code on the web, mobile — any trigger point should work.
2. **Keep gapp a reusable product.** No personal config, no operator-specific values. gapp ships reusable assets (CLI, Terraform, workflows) that anyone can consume.
3. **Keep deployed repos as reusable products.** No deployment workflows, no CI/CD config, no GCP coupling. A deployed repo is application code, with or without a `gapp.yaml`.
4. **Make CI/CD copy/paste simple.** A new operator should be able to follow gapp's docs, run six commands, and be running. No custom engineering.
5. **Don't break local workflow.** `gapp deploy` from your laptop continues to work exactly as before. CI/CD is additive, not a replacement.
6. **No stored credentials anywhere.** WIF eliminates JSON key files and stored secrets. The trust relationship is between GCP and a specific GitHub repo — no transferable artifacts.
7. **Operator controls the blast radius.** The deploy identity has only the permissions needed for deployment. It cannot set up new projects, access other projects, or escalate.

## Principles

- **Public repos must not depend on private repos** (existing gapp principle #11). The test: "Can someone deploy this app to their own GCP project using only public repos?" Always yes.
- **Don't hide reusable logic in private repos** (existing gapp principle #14). The CI/CD logic lives in gapp's reusable workflow. The private repo is just configuration.
- **Derive, don't configure** (existing gapp principle #8). WIF pool names, service accounts, and workflow content are all derivable from convention. The only truly unique inputs are the GCP project ID and the operator's fleet repo name.
- **Each phase does one thing** (existing gapp principle). `gapp setup` handles GCP foundation for the direct system. `gapp ci init` handles GitHub wiring. They don't overlap.
- **GitHub is optional.** The core lifecycle — `gapp init`, `gapp setup`, `gapp secret set`, `gapp deploy` — works with any local git repo. No GitHub account, no GitHub API, no GitHub Actions. CI/CD automation (`gapp ci`) requires GitHub, but is entirely additive. The CI layer calls `gapp deploy` — not the other way around.
- **Security by scoping, not by obscurity.** Project IDs in a private repo aren't security — they're just configuration. Real security comes from WIF trust scoping, service account permissions, and workflow pinning.

## Constraints

### Deployed repos are products, not personal projects

Deployed repos are reusable, public, GCP-agnostic products. They contain application code and, optionally, a `gapp.yaml`. They must not contain:

- Deployment workflows hardcoded to a specific operator's infrastructure
- Personal credentials or project IDs
- CI/CD configuration that couples them to gapp or any specific deployment tool

A deployed repo should pass the test: "Can someone else deploy this to their own GCP project using only public repos?" The answer must always be yes. The fleet entry can even supply the whole `gapp.yaml`, so a repo that runs on Cloud Run can be deployed from a fleet without knowing about gapp.

### gapp is a product, not a personal project

gapp is a reusable CLI tool. It ships reusable assets (Terraform modules, Dockerfile templates, runtime wrapper, and reusable GitHub workflows). It must not contain anyone's personal project IDs, credentials, or operator-specific configuration.

### Neither product repo should own operator-specific config

This is the key insight. There are three layers:

| Layer | Visibility | Contains |
|-------|-----------|----------|
| **Tool** (gapp) | Public product | CLI, Terraform modules, Dockerfile template, runtime wrapper, reusable GitHub workflows |
| **Application** (deployed repo) | Public product | Application code, optionally `gapp.yaml` |
| **Operator config** (fleet repo) | Private, per-operator | The deployments file (deployment → repo, project, gapp.yaml keys), per-project Environments holding the WIF references, and gapp's two workflows that wire tool + application to infrastructure |

The fleet repo is the only place where "deploy this specific repo to this specific GCP project" is expressed.

### The fleet repo is not intellectual work

The fleet repo is an address book, not a product. It maps "deployment X → repo Y on GCP project Z, with these gapp.yaml keys." There's nothing reusable or interesting in it. The interesting parts are in gapp (the reusable workflow) and in the deployed repos (the applications). The fleet repo is configuration: one data file of entries, each saying something different, plus two gapp-owned workflows that read and write that file.

The repo could be public — project IDs aren't sensitive, and WIF means no credentials are stored. But it's not a product. It's a worked example at best.

### Do you ever need the deployed repo locally?

Only for development — writing and testing the code. For deployment, never. The flow becomes:

1. Write code anywhere (local, Codespaces, Claude Code on the web)
2. Push to GitHub
3. Deployment happens via CI (or manual `gapp deploy` if you prefer)

gapp already builds from `git archive HEAD` via Cloud Build — it never needed local Docker. The only thing anchoring you locally was `gcloud auth`.

### Industry precedent

This three-layer separation is standard:

- **Kubernetes**: Helm charts (product) are public. Your `fleet-config` repo (private) references them with your values.
- **Terraform**: Modules (product) are public. Your `infra-live` repo (private) calls modules with your project IDs.
- **Heroku/Vercel/Render**: The "operator config" is the platform's dashboard — you connect a public repo to your account.

## Authentication Architecture

### Current state: two auth channels

gapp uses GCP authentication in two ways:

1. **Ambient gcloud CLI auth** — the vast majority of operations. Every `subprocess.run(["gcloud", ...])` call relies on whatever `gcloud auth login` session is active. Used by: `core.py` (setup, deploy, status, project and deployment labels, Artifact Registry, Cloud Build, GCS) and `secrets.py` (Secret Manager CRUD).

2. **Explicit OAuth token for Terraform** — gapp calls `gcloud auth print-access-token` once during deploy and passes the result to Terraform via the `GOOGLE_OAUTH_ACCESS_TOKEN` env var. This is the only place gapp extracts a token explicitly.

### Three GCP identities in the full lifecycle

| Identity | Used by | How authenticated | Permissions needed |
|----------|---------|-------------------|-------------------|
| **Human operator** (or CI principal) | `gapp setup`, `gapp deploy`, `gapp secret set`, all admin commands | `gcloud auth login` (local) or WIF (CI) | Broad: enable APIs, create buckets, manage secrets, submit builds, run Terraform |
| **Cloud Build service account** | Container builds inside `gapp deploy` | Automatic — `{project-number}@cloudbuild.gserviceaccount.com` | Pull base images, build Docker, push to Artifact Registry |
| **Cloud Run service account** | Running deployed service at runtime | Terraform creates `gapp-{id}@{project}.iam.gserviceaccount.com` | `secretmanager.secretAccessor` on its secrets, `storage.objectUser` on auth bucket |

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

No JSON key files. No stored secrets. No credentials in any repo. If someone forks the fleet repo, their fork can't authenticate — WIF is scoped to the specific repo.

### No code changes needed for auth

gapp's existing `_get_access_token()` calls `gcloud auth print-access-token` and passes the token to Terraform via `GOOGLE_OAUTH_ACCESS_TOKEN`. In CI, `google-github-actions/auth` (WIF exchange) + `google-github-actions/setup-gcloud` (installs and configures gcloud) make `gcloud auth print-access-token` work on the runner the same way it works locally. No env var fallback or alternative code path needed.

gapp needs `gcloud` on the runner because it shells out to `gcloud` for everything. If gapp used Google Cloud Python SDKs instead, Application Default Credentials alone would suffice and `gcloud` wouldn't be needed. But that's a potential future refactor, not a prerequisite for CI support.

## The Fleet Repo

The operator's private fleet repo holds four gapp-owned files, installed by `gapp ci init`:

| File | Written by | Role |
|---|---|---|
| `.github/workflows/gapp-deploy.yml` | `gapp ci init` (installed and updated) | The one deploy workflow. Dispatched with a deployment name and a ref; its runs are named `Deploy <name> (<ref>) <request id>`, the id gapp passed so it can find the run its dispatch created (empty when dispatched by hand). Its first job reads the deployment's entry from the deployments file and refuses it when the repo's owner is not in `GAPP_ALLOWED_SOURCES`; its second job runs in the GitHub Environment named after the entry's project and calls gapp's reusable workflow with the entry's repo, id and gapp.yaml keys. |
| `.github/workflows/gapp-register.yml` | `gapp ci init` (installed and updated) | The register workflow. Dispatched by `gapp ci setup` and `gapp ci rename`; its runs are named `Register <name> <request id>`. Installs gapp at the pinned release and runs `gapp ci runner register`, the same code the operator's command ran before dispatching, which validates and writes the entry with the run's own token. |
| `.github/workflows/gapp-status.yml` | `gapp ci init` (installed and updated) | The read-only status workflow. Dispatched by `gapp ci status <name> --live`; its runs are named `Status <name> <request id>`. Its first job reads the deployment's entry; its second runs in the project's Environment, authenticates exactly as a deploy does, runs `gapp status` for the deployment's id (`gapp ci runner status`) and reports the result as one line. It writes nothing. |
| `gapp/deployments.yaml` | `gapp ci init` (created empty), then the register workflow | The fleet file: one entry per deployment, keyed by its name. Intent only: no live state is ever recorded here. |

### The deployments file

```yaml
# gapp/deployments.yaml
my-app:
  id: my-app
  repo: <owner>/my-app
  project: <project-id>              # the GitHub Environment of the same name holds the deploy identity
  domain: app.example.com            # a gapp.yaml key, merged over the repo's gapp.yaml at deploy time
  env:
    - name: LOG_LEVEL
      value: INFO
my-app-staging:
  id: my-app-staging
  repo: <owner>/my-app
  project: <staging-project-id>
```

**One entry per deployment, keyed by an operator-chosen name; flat shape, no nested block.** `repo` and `project` are the entry's own fields (gapp.yaml has neither). **Every other key is a gapp.yaml key**, deep-merged over the repo's `gapp.yaml` by the deploy run's overlay step (mappings merge key by key, lists of named entries such as `env` merge by name, anything else is replaced), or written as the whole file when the repo has none. Keys the entry doesn't set come from the repo. gapp core is unchanged: it still reads a `gapp.yaml` on the runner; the ci layer builds it.

**`id`** is what every GCP resource is keyed by: the service and its service account, the bucket, secrets (`<id>-<secret>`), the Terraform state prefix, the label. It is passed to the runner as the solution name (the overlay sets `name: <id>`), so the entry governs whatever the repo's `gapp.yaml` says. Set once, it defaults to the deployment name; when that id is already used by another entry on the same project, the next free variant (`<name>-2`, ...). At most 27 characters (the service account id `<id>-sa` is at most 30); `ci setup` refuses a longer name with a plain message. It is never shown in default output, only in `ci status <name> --verbose`. There is no `--id` flag: binding to arbitrary existing resources is deferred.

**Environments and instances** are more entries for the same repo, typically on other projects. There is no first-class environment concept. `gapp ci setup <new> --copy-of <existing>` makes one: the same repo and gapp.yaml keys, its own id, no domain (a domain maps to one service), no deploy history, no secret values; `--project` defaults to the source's, which is safe because the copy's own id gives it its own service, account, bucket and secrets. The output names the env vars copied ("check them for this environment") and the secret names the new deployment needs set on its project before its first deploy. There is no lasting link between the two.

**Names** are unique across the fleet file (they are its keys), so `gapp ci deploy <name>` always means one deployment. `gapp ci rename <old> <new>` changes the name only; the id and every GCP resource stay as they are.

Deployment values such as the domain belong in the entry, not in the deployed repo's `gapp.yaml`: the entry's keys win at deploy time, so a public repo can carry a placeholder or no domain at all. Set it with `gapp ci setup <name> ... --domain <host>`; a re-run without `--domain` keeps the entry's domain, and `--domain ""` removes it. Every other setting comes from the app repo's own `gapp.yaml`; no option merges a local file into an entry, because such a change could not be repeated or reviewed. Re-running `gapp ci setup <name> --repo <repo> --project <project>` also drops any key gapp.yaml does not have, which is the repair `gapp ci status` names for an entry a deploy would refuse.

### Why deployments are manual by default

The deploy workflow has `workflow_dispatch` only — no automatic triggers. This is deliberate:

1. **Operator controls when code reaches production.** A push to a deployed repo is a code change, not a deployment decision. The operator explicitly decides when a new version is ready for their infrastructure via `gapp ci deploy` (which calls `workflow_dispatch`).
2. **Deployed repos are not deployment pipelines.** Adding push-triggered workflows to them would couple them to a specific operator's fleet repo, violating the open/closed principle.
3. **No access to the deployed repo required.** An operator should be able to deploy any public repo — even one they don't own — without admin access to it. GitHub has no native mechanism to watch another repo for pushes without a webhook (admin), a GitHub App (owner), or a workflow in that repo (write access). The only zero-access approach is polling.
4. **Security by default.** Auto-deploying on push to a third-party public repo means someone else's commit deploys to your infrastructure.

### Optional: auto-deploy via polling

For operators who want automatic deployments, a scheduled poller workflow in the fleet repo can check each deployed repo for new commits and dispatch the deploy workflow with that deployment's name. It requires no access to the deployed repo, keeps deployed repos untouched, lives entirely in the fleet repo and works for repos the operator doesn't own, at the cost of a polling interval. Not yet implemented.

### Trigger options (operator's choice)

The fleet repo is plain GitHub Actions configuration. gapp's three workflows are rewritten by `gapp ci init`, so customization goes in a workflow of the operator's own that dispatches `gapp-deploy.yml` (with `gh workflow run gapp-deploy.yml -f deployment=<name> -f ref=<ref>`, or the `workflow_dispatch` API): `workflow_dispatch` (default), `schedule` (polling), push-triggered from a deployed repo the operator owns (couples that repo to one operator, not recommended), or `repository_dispatch` (needs a sender).

### Security model

- **WIF scoping**: GCP only trusts the specific fleet repo. Forks can't authenticate.
- **Service account scoping**: The deploy service account has only the permissions needed for deployment. It cannot run `gapp ci project setup` or access other GCP projects. It never writes project labels: a deploy labels only the deployment's own bucket and services, and the deploy workflow passes the project from the fleet entry, so the runner never searches for it.
- **Workflow pinning**: The deploy workflow pins gapp's reusable workflow to a release tag, written once per fleet repo by `gapp ci init` and moved only by re-running it. Nothing in the deployments file can point a deploy at another copy of the reusable workflow, and the register run installs the same pinned gapp.
- **Who can dispatch**: Dispatching a workflow in the fleet repo requires write access to it. So "who can deploy" and "who can add a deployment" are both exactly "who can write to the fleet repo", and GitHub's audit log records every dispatch with its actor and inputs.
- **The source allowlist**: The register run accepts a repo only when its owner is listed in the fleet repo's repository variable `GAPP_ALLOWED_SOURCES` (comma-separated; managed by `gapp ci sources`), and the deploy run checks the entry's owner against it again before deploying. Someone with write access to the fleet repo can edit the deployments file by hand, but cannot deploy code from an owner the operator has not named.
- **Inputs never reach a shell unquoted**: Both workflows pass their inputs to their steps through `env:`; the register run validates every input's shape in Python and the deploy workflow's lookup step reads them with `strenv()`; nothing from an input is interpolated into a `run:` script.
- **The deployments file write is SHA-checked**: The register run writes `gapp/deployments.yaml` through the Contents API with the SHA it read at the start of the run, so two runs that race fail cleanly instead of overwriting each other.
- **A deployment binds to one repo and one project**: The same name with another repo or project is refused; the operator renames, or adds a deployment with `--copy-of`. A dispatch cannot quietly redirect a deployment.
- **New domain mappings need a verified owner**: Cloud Run maps a domain only for an identity that has verified it (or a parent) in Search Console. The deploy service account must be added there as a verified owner before a deploy can create a mapping for a new domain.
- **GitHub token scoping**: The run's built-in `GITHUB_TOKEN` can read only the fleet repo it runs in. That is enough to clone a public repo. A private repo is cloned with a separate token minted from a GitHub App the operator installs on the repo's owner: limited to that one repo, read-only Contents, valid for an hour, and discarded with the run (see "Private repos"). The register run's token has `contents: write` on the fleet repo alone.
- **No setup permissions in CI**: The deploy service account cannot run `gapp ci project setup`, create new WIF pools, access other GCP projects, or enable APIs. It can only deploy.

### Recommended operator protections

Two GitHub settings on the fleet repo turn the model above into enforced policy. gapp does not set them; they are the operator's.

- **Branch protection with required pull requests on the default branch.** Then no one changes `gapp-deploy.yml`, `gapp-register.yml` or `gapp/deployments.yaml` by hand without a review. The register run's write and `gapp ci init`'s writes go through the same rule, so give them a way through: exempt the GitHub Actions app and the operator's own account in the rule's bypass list, or accept that each registration lands as a change the operator merges. The register run reports GitHub's refusal when the rule blocks it.
- **A required reviewer on each per-project Environment.** The deploy job runs in the Environment named after the project, so an Environment protection rule turns every deploy to that project into a run that waits for a named human's approval.

## gapp Ships a Reusable GitHub Workflow

The reusable workflow lives in the gapp repo at `.github/workflows/deploy.yml`. It:

1. Accepts inputs: the repo and ref, the Environment (or explicit WIF config), the solution name (the deployment's id), the `overlay` (the entry's gapp.yaml keys as JSON) and the hosting app's id
2. Authenticates via WIF (`google-github-actions/auth`)
3. Installs and configures gcloud, terraform and gapp
4. Clones the deployed repo at the specified ref into a directory named after the id
5. Applies the overlay with `gapp ci runner overlay gapp.yaml`: merges the entry's keys over the repo's `gapp.yaml`, or writes them as the whole file
6. Runs `gapp setup` and `gapp deploy --ref <ref>`

This is a product asset, like the Dockerfile template or Terraform modules. The fleet repo's deploy workflow is a thin caller that looks a deployment up and passes its values. The older one-key `domain` input is kept for callers written before `overlay` existed.

### Private repos

A private deployed repo is supported. The principle "public repos must not depend on private repos" is untouched: gapp, its reusable workflow and the pattern stay public, and a private repo is simply the operator's own deployable code. What changes is only how the runner clones it.

**The mechanism.** The reusable workflow takes an optional input `solution-repo-app-id` and an optional secret `solution-repo-app-private-key`. When both are set, the run mints an installation token with `actions/create-github-app-token`, scoped to the deployed repo (owner and repo name), read-only Contents, one hour, and hands it to `actions/checkout`. The private key is read once by that step and never written to the checkout.

**Security model.** The GitHub App's installation list is the boundary: a token can only ever be minted for a repo the app is installed on, and the workflow further narrows each token to the one repo being deployed, read-only, for an hour. So: install the app on an organization that holds only deployable repos; never install it on all repositories of a personal account; store the private key only as an Actions secret of the fleet repo and delete the downloaded key file; give the app Contents read-only and no webhook.

**App setup (once per operator, in a browser).**

1. On the GitHub organization that holds the private deployable repos: Settings → Developer settings → GitHub Apps → New GitHub App. Permissions: Repository → Contents: Read-only. Webhook: off. Installation: only on this account.
2. Install the app on that organization. Note the App ID.
3. Generate a private key; it downloads once as a `.pem` file.
4. Store both in the fleet repo as an Actions variable and secret, then delete the key file:

   ```bash
   gh variable set HOSTING_GITHUB_APP_ID --repo <owner>/<fleet-repo> --body <app-id>
   gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo <owner>/<fleet-repo> < <downloaded-key>.pem
   rm <downloaded-key>.pem
   ```

`HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` are fixed names, like `GITHUB_TOKEN`: the credentials belong to the fleet repo, one pair per operator. `gapp ci init --verbose` and `gapp ci list --verbose` report whether both exist (or that they could not be checked); the register run refuses a repo it cannot read without them (`repo-unreachable`), naming the two; the deploy workflow's first job passes the app's id for a repo the run's token cannot read. No per-deployment flag records visibility, so a repo that turns private later keeps deploying once the app covers it.

### GitHub Enterprise compatibility

All `gapp ci` commands interact with GitHub exclusively through `gh api` REST calls, never GraphQL (some sessions, a Claude Code cloud session among them, refuse GraphQL), and `gh` handles multi-host authentication natively. gapp never talks to GitHub directly, so it works with any GitHub instance that `gh` is authenticated against.

## CLI Design: Where CI Commands Live

### Options considered

**Option A: Extend `gapp setup` with `--ci` flag.** Rejected. Violates "no phase does double duty."

**Option B: Separate `gapp ci` command group.** Chosen. Clean separation, and the group can mirror the root verbs.

**Option C: `gapp setup` creates WIF (always).** Rejected: WIF pool, provider and deploy service account are CI infrastructure.

**Option D: `gapp deploy --ci`.** Rejected: first run does irreversible things; subsequent runs don't.

### Chosen design

```
gapp init                                         # scaffold a gapp.yaml (direct system)
gapp setup --project <project-id>                 # GCP foundation (direct system)
gapp deploy                                       # local deploy (direct system)
gapp ci init [<owner>/<repo>] [--create]          # CI: the fleet repo (once per operator)
gapp ci sources add <owner>                       # CI: allow a source owner (once per owner)
gapp ci project setup <project>                   # CI: trust the fleet repo for a project (once per project; the one gcloud step)
gapp ci setup [<name>] --repo <owner>/<repo> --project <project>   # CI: a deployment
gapp ci deploy <name>                             # CI: deploy it
gapp ci status <name>  /  gapp ci list            # CI: where things stand
```

`gapp ci` is a command group that owns the entire CI system, mirroring the root verbs. Each command:

#### `gapp ci init`

The only command that sets the fleet repo, stored in gapp's local config (the active profile of `~/.config/gapp/config.yaml`, keys `fleet_repo` and `fleet_repo_set_by`). No other command looks it up anywhere else, caches it or writes it; when it is unset they stop with the one message above.

- Without a repo: shows the saved fleet repo and how it was set. With none saved (or with `--owner`), one `gh search repos --topic gapp-ci --owner <owner>` call: one match is saved and shown; several are listed and the command stops; none gives the `--create` guidance.
- `gapp ci init <owner>/<repo>`: the repo must exist (otherwise the `--create` message). Sets it, adds the `gapp-ci` topic (so other machines find it), installs or updates the three workflows (pinned to the installed gapp's release tag) and creates `gapp/deployments.yaml` when missing, never overwriting it. Removes an empty `gapp/solutions.yaml` left by gapp 3.9 and reports a non-empty one with the plain next step. Reports the three variables with `--verbose`.
- `gapp ci init <owner>/<repo> --create`: the repo must not exist (otherwise the plain `init` message). Creates it private, then as above.

Default output: "gapp can now deploy your apps through GitHub Actions.", what changed (or "Nothing changed."), "(Your fleet is kept in <fleet-repo>.)", then `Next: gapp ci sources add <owner>` (or `gapp ci project setup <project>` once the allowlist exists).

#### `gapp ci project setup <project>`

Once per project, idempotent, and the only `gapp ci` command that touches GCP: checks that `gh` is an admin of the fleet repo before any GCP call, that gcloud is installed and the configured gapp account has a login, creates or confirms the Workload Identity pool and provider and the `gapp-deploy` service account, binds the pool to the fleet repo, and records the deploy identity as the GitHub Environment named `<project>` with `GAPP_WIF_PROVIDER` and `GAPP_DEPLOY_SERVICE_ACCOUNT`. Output: "<project> now trusts your fleet to deploy to it." (or "already trusts ... Nothing changed."), then the `ci setup` command.

#### `gapp ci sources add|remove|list <owner>`

Manages the fleet repo's repository variable `GAPP_ALLOWED_SOURCES`. Output: "Apps owned by <owner> are now allowed in your fleet.", then `Next: gapp ci project setup <project>`.

#### `gapp ci setup`

Adds or updates a deployment; never gcloud, never the working directory. The repo is `--repo <owner>/<repo>`, the name defaults to its repo name, `--project` is required (the CI system never looks a project up in GCP). Before dispatching, the command reads the fleet file and runs the same `fleet.py` checks the register run will: same name and repo is an update (an identical entry is "Nothing changed." with no run); the same name with another repo or project is refused; a repo already deployed under other names is refused, listing them with "Update it: gapp ci setup <existing> ..." and "Add another: gapp ci setup <new> --copy-of <existing> ..."; a name whose id would exceed 27 characters is refused. Then it checks the project's Environment through the repo environments endpoint and stops with the "not yet trusted" message when GitHub confirms it is absent; a read it cannot make is left to the run. It dispatches `gapp-register.yml` with `operation`, `name`, `repo`, `project`, `changes` (the domain to set or remove, as JSON) and `source`, waits for the run (`--no-watch` returns after the dispatch) and reads its structured result: `added`, `updated` or `unchanged` with the name, id and project; `source-owner-not-allowed` as the "not yet allowed" message; any other refusal verbatim with the run link.

Output: "<owner>/<repo> is set up to deploy to <project> as <name>." then "Deploy it: gapp ci deploy <name>". With `--copy-of`: "<new> is set up as a copy of <existing>: <repo> deploys to <project>.", the env names copied, the secret names to set.

#### `gapp ci rename <old> <new>`

Dispatches the register run with `operation=rename`; the id and every GCP resource are untouched.

#### `gapp ci deploy <name>`

Dispatches `gapp-deploy.yml` with `deployment` and `ref` and streams the run. An unknown name is an outcome naming `gapp ci setup` and `gapp ci list`; a refused dispatch, a missing deploy workflow and a failed run (with its link) are outcomes too. `gapp ci trigger` is a hidden alias for one release that prints the new name.

#### `gapp ci status <name>` and `gapp ci list`

`status` is one deployment, GitHub only and fast: "<name>: <repo> deploys to <project>[, domain <d>].", its last deploy (the newest run named `Deploy <name> (...)`), and either "Deploy it: gapp ci deploy <name>" or, for an entry with keys gapp.yaml does not have, the problem and "Next: rewrite the entry: gapp ci setup <name> --repo <repo> --project <project>"; it does not read the project's Environment (the deploy run checks it); it ends with "For the live service state: gapp ci status <name> --live". `--verbose` adds the id and the gapp.yaml keys the fleet sets.

`status --live` dispatches `gapp-status.yml`, waits for the run (about a runner start; the command says so) and prints the root `gapp status` result the run reported: service up or not, URL, domain state, next step. The run has read-only permissions (`contents: read`, plus the OIDC token), uses the project's existing deploy identity and no new one, and stores nothing. Refusals and failures are plain outcomes with the run link. `list` is the fleet's deployments, `--repo` narrows to one repo's, and `--verbose` adds the fleet repo's three files (present, current, or not checked), its variables and the per-solution workflows left from gapp 3.x.

### Missing only when GitHub says so

A thing is reported missing only when GitHub's answer confirms it does not exist. A read that fails for any other reason is reported as "Couldn't check <thing>: GitHub returned <status>: <GitHub's own message>. Request: <the request>", passed through trimmed, with no assumed cause. Because GitHub returns 404 both for a missing object and for one the login cannot see, a 404 on a repo is one clause: "GitHub returned 404 Not Found: it doesn't exist, or your GitHub login (<user>) can't see it." A 404 on a file or Environment of a repo that itself answers is a confirmed absence. Where the proxy refuses the Actions variables API, the variables are "not checked", never missing.

### Why gapp's files are written through the API, not git

gapp is not a git client for the operator's repo. The three workflows and the deployments file are generated configuration: every value in them is already known to gapp, there is no free-form text and, by gapp's secrets invariant, no credential can appear in them. So they are written the way gapp does every other GitHub operation, with one API call that creates or updates the file by path. GitHub makes the commit server-side; nothing on the operator's machine is cloned, committed, or pushed, and the operator's local git hooks are neither triggered nor bypassed. Re-runs are idempotent for free: the API takes the file's current SHA and either updates it or reports it unchanged.

Two writers use that path. `gapp ci init` writes the workflows and creates the deployments file from the operator's machine, which therefore needs a `gh` login that can write to the fleet repo. The register run writes the deployments file's entries from the runner with the run's own token (`contents: write`), which is why adding a deployment needs no API write from the machine that asks for it: a session behind a proxy that refuses GitHub API writes, or that has no gcloud identity, still completes `gapp ci setup` by dispatching the run. There is no separate verify or pre-flight workflow: the register run validates what it writes, and the deploy run verifies the credential (its first step) and the project.

#### Migration from gapp 3.x

None needed. Existing apps are set up under their current names, so `id` = the name they were deployed as and the runner reuses their live service, bucket, domain, secrets and Terraform state. `ci init` removes an empty `gapp/solutions.yaml` left by 3.9.0 and reports a non-empty one. Old per-solution workflow files keep working from the Actions tab until deleted; `ci list --verbose` notes them.

## Documentation for Users

The operator pattern is simple enough to be copy/paste documentation in gapp's README:

1. `gapp ci init` finds your fleet repo, or `gapp ci init <owner>/<repo> --create` makes one (once per operator)
2. `gapp ci sources add <owner>` (once per GitHub owner whose repos you deploy)
3. `gapp ci project setup <project-id>` (once per project; gcloud as an Owner, `gh` as an admin of the fleet repo)
4. `gapp ci setup <name> --repo <owner>/<repo> --project <project-id> [--domain app.example.com]` (from any machine with `gh`). A private repo also needs the hosting app from "Private repos".
5. Done. `gapp ci deploy <name>` from anywhere, or GitHub UI, CLI, or API with the deployment's name. Check with `gapp ci status <name>` and `gapp ci list`.

For someone else using your public repo: clone nothing, fork nothing. Run the same steps against your own fleet repo and project, and you're running. Two public products, one private glue repo of your own.
