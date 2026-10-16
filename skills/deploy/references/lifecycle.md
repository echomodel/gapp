# Lifecycle phases

Read the phase `gapp status` points you at. Commands are shown in
CLI form; the matching MCP tool is in `references/tools.md`.

## Phase 1: Initialize

`gapp init` both creates and updates `gapp.yaml`; call it again
whenever the user changes entrypoint, secrets, or domain. When there
is no `gapp.yaml`:

1. Identify the entrypoint with the user by reading the code
   (`references/manifest.md` has the priority order).
2. Run init to write `gapp.yaml` and register the solution locally.
3. Declare any secrets the app needs under `env` with a `secret`
   entry, or under `prerequisites.secrets` for prerequisite-only
   declarations.

Init needs no cloud identity. If `gh` is logged in it also tags the
GitHub repo with the `gapp-solution` topic; otherwise it skips that
silently.

## Phase 2: GCP foundation

When status shows no project, the user needs one.

**Find an existing project first.** `gapp list --all` shows every
gapp solution across owner namespaces.

- The solution already appears: it is deployed somewhere. Offer to
  attach to that project.
- Other solutions share a project: offer to reuse it.
- Nothing: ask for a project id, or point the user at the Cloud
  Console to create one. gapp never creates projects.

**Env binding.** Env is a property of the project, not the
solution: a project carries one `gapp-env` label that every solution
on it inherits. Setup never writes it; only
`gapp projects set-env <id> <env>` does. An unbound project is
addressable by `--project` only and lists as `<undefined>`. For
dev/staging/prod, bind each project once, then `--env prod` resolves
through discovery. Order does not matter; bind before or after
setup. The name `default` is reserved.

**Run setup.** `gapp setup <project-id>`. The id is required the
first time on a project (there are no labels to discover from);
later runs find it. Setup enables APIs, creates the per-solution
bucket for Terraform state and data, grants Cloud Build its
permissions, and writes the solution label. If another owner has a
solution of the same name on that project, setup refuses with a
cross-owner warning; pass `--force` only when the user means to
share, because bucket and service names are owner-blind.

Setup needs a project-admin identity. When this machine lacks one,
`references/environment.md` says what to do.

## Phase 3: Secrets

`gapp secrets list` shows declared secrets and their state. For each
missing one, ask the user for the value and run
`gapp secrets set <name>` (prefer stdin or the tool argument over
a shell history line). Values go to Secret Manager; only names are
in the repo. Secrets gapp creates carry the label
`gapp-solution=<name>`; pre-existing unlabeled secrets with the same
id are never silently adopted, so a conflict is reported, not
papered over.

## Phase 4: Deploy

Two paths. Present both once; afterwards use the one the user
chose.

**Local.** Needs gapp, a GCP identity, terraform, and a clean git
tree. `gapp deploy` builds the image with Cloud Build and applies
Terraform in one blocking call, then returns the service URL. Use
`--env prod` when the solution is on several projects, or
`--project <id>` to be explicit. The build is skipped when an image
for the current commit already exists.

**CI, recommended for ongoing use.** Set up once, then deploy from
anywhere with GitHub access: the GitHub UI, a phone, a cloud agent.
No terraform or gcloud on the deploying machine.

1. `gapp_ci_init(repo="owner/ci-repo")` designates a private CI
   repo (an address book of solution to project, not a product).
2. `gapp_ci_setup(solution="name")` creates the Workload Identity
   pool and provider and the `gapp-deploy` service account in the
   project (shared by every solution there), binds the pool to the
   CI repo, and pushes a per-solution workflow into the CI repo.
3. `gapp_ci_trigger(solution="name")` dispatches that workflow; any
   GitHub tool that can dispatch a workflow and read the run does
   the same job.

The two setup calls need a project-admin identity and write access
to the CI repo, so they usually happen on the workstation once.
Everything after is credential-free for the operator.

Recommend CI whenever the user deploys from more than one machine,
wants agents to deploy, does not want terraform locally, or wants
deploys on push.

## Phase 5: Verify and hand over

1. `gapp status` confirms the service is up and returns the URL.
2. If the solution needs an admin credential: the user may hold it
   in a vault from when they created it; if gapp generated it
   (`generate: true`), the user never had it, so fetch it with
   `gapp secrets get <name> --plaintext` and hand it over out of
   band.
3. Users, tokens, and client registration belong to the solution's
   own framework. Point at its README or skill rather than guessing.
4. Share the URL.

## Custom domain

Subdomains only (`mcp.example.com`); apex domains need A records,
which Cloud Run domain mapping does not handle cleanly.

1. Set it: `domain:` in `gapp.yaml`, or init with `domain=`.
2. Deploy. The next deploy creates the domain mapping; the
   `.run.app` URL keeps working throughout.
3. DNS: the user adds `CNAME mcp.example.com -> ghs.googlehosted.com`
   at their registrar. Ask which registrar they use and walk them to
   its DNS page; this is the one step gapp cannot automate.
4. Watch `gapp status`: `pending_dns` (CNAME missing or not
   propagated), `pending_cert` (DNS right, certificate provisioning),
   `active`.

Remove a domain by clearing the field (or `domain=""`) and
redeploying; change one by editing the field, redeploying, and
updating the CNAME.

## Ongoing operations

**Redeploy.** Before redeploying, ask whether the change affects how
the app reads or writes its data. If the format or layout changed,
inspect the bucket (`gsutil ls gs://gapp-<name>-<project-id>/data/`)
and raise incompatibilities before deploying; the user may need a
migration or backup first. Then deploy by the chosen path. Remind
the user that uncommitted changes are not included.

**Status and discovery.** `gapp status` for health, URL, and next
step. `gapp list` for the active owner namespace, `gapp list --all`
for every namespace, `gapp projects list` for env bindings.

**Env bindings.** `gapp projects set-env <id> <env>` (idempotent for
the same value, `--force` to overwrite), `gapp projects clear-env
<id>` to return a project to `<undefined>`.
