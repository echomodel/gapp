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

**Find an existing project first.** `gapp list` shows every
deployment on the projects marked `gapp=hosted`.

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
first time; later runs find the project among those marked
`gapp=hosted`. Setup enables APIs, creates the per-solution bucket
for Terraform state and data and labels it (`gapp-deployment`,
`gapp-version`), grants Cloud Build its permissions, and marks the
project `gapp=hosted` once (it needs an Owner for that; a CI run
never writes project labels). A deployment's name is unique within
its project.

Setup needs a project-admin identity. When this machine lacks one,
`references/environment.md` says what to do.

## Phase 3: Secrets

Full detail in `references/secrets.md`; the short version:

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

**CI, recommended for ongoing use.** The CI system is the other
fleet system: it needs GitHub, never a Google Cloud identity (one
command excepted), never reads the working directory, and its record
is the fleet repo, not the GCP labels. Its unit is a **deployment**:
a repo deployed to a project under a name the user chooses; the same
repo deployed twice (staging and prod, two instances) is two
deployments. A solution is deployed by one system or the other; never
mix them for one solution, and never expect `gapp status` to know
about the fleet or `gapp ci status` to know about GCP. Set up once,
then deploy from anywhere with GitHub access: the GitHub UI, a phone, a
cloud agent. No terraform or gcloud on the deploying machine.

1. `gapp ci init` shows the fleet repo set on this machine or finds
   the one tagged `gapp-ci` under the user's account (`--owner <org>`
   for an organization). None: `gapp ci init <owner>/<fleet-repo>
   --create` makes a private one (an address book of deployments, not
   a product) with gapp's three workflows and an empty
   `gapp/deployments.yaml`; `gapp ci init <owner>/<fleet-repo>` sets
   an existing one and installs or updates gapp's files in it (needs a
   `gh` that can write). Once per operator, and once per machine to
   set it; re-run after a gapp upgrade to update the workflows. Never
   hand-edit gapp's files. The output ends with the next command.
2. `gapp ci sources add <owner>` allows repos owned by a GitHub owner
   to be deployed from the fleet (the variable `GAPP_ALLOWED_SOURCES`).
   Once per owner; needs `gh` with write access to the fleet repo.
3. `gapp ci project setup <project-id>` makes the fleet trusted to
   deploy to the project: it creates or confirms the Workload
   Identity pool and provider and the `gapp-deploy` service account
   (shared by every deployment on the project), binds the pool to the
   fleet repo, and records them in the fleet repo as a GitHub
   Environment named after the project (`GAPP_WIF_PROVIDER`,
   `GAPP_DEPLOY_SERVICE_ACCOUNT`), which the deploy job runs in. Once
   per project, idempotent. **This is the one `ci` command that
   touches GCP**: it needs gcloud signed in as an Owner of the project
   and `gh` signed in as an admin of the fleet repo, and it is the one
   human step to hand off when this machine lacks them.
4. `gapp ci setup <name> --repo <owner>/<repo> --project <project-id>
   [--domain app.example.com] [--config keys.yaml]` adds the
   deployment (the name defaults to the repo's name): it dispatches
   the register run, which checks the project's Environment, the
   source allowlist and the repo's reachability, then writes the entry
   in `gapp/deployments.yaml` with its own token. So **this needs only
   a `gh` that can dispatch workflows, and never gcloud**: from any
   machine, including this one. `--project` is required; the CI system
   never looks a project up in GCP. When the project is not yet
   trusted, or the owner not yet allowed, the command stops with a
   verbatim one-time message naming step 3 or step 2 and repeating the
   command to run again; relay it as is. The same name again updates
   the entry's keys; a repo already deployed under another name is
   refused, with the two ways on (update that one, or `--copy-of`).
   Deployment values go into the entry, never into the repo: `repo`
   and `project` are the entry's own fields and every other key is a
   gapp.yaml key merged over the repo's `gapp.yaml` at deploy time
   (`domain`, `env`, `service`, ...), or the whole file when the repo
   has none. `--domain` sets one key, a re-run without it keeps it,
   `--domain ""` removes it.
5. **Environments and second instances.** `gapp ci setup <new>
   --copy-of <existing> [--project <other-project>] [--domain <d>]`
   makes another deployment of the same repo: same gapp.yaml keys, its
   own id (so its own service, account, bucket and secrets), no domain,
   no history, no secret values; the project defaults to the source's.
   The output lists the env names copied ("check them for this
   environment") and the secret names to set on the new project before
   the first deploy; relay both. `gapp ci rename <old> <new>` changes a
   name only.
6. **Private repo.** The runner's built-in token can read only the
   fleet repo, so a private repo is cloned with a one-hour token from a
   GitHub App the user installs on the repo's owner (a browser step,
   `references/environment.md`). The deploy workflow passes the app's
   id and key from the fleet repo's Actions variable
   `HOSTING_GITHUB_APP_ID` and secret `HOSTING_GITHUB_APP_PRIVATE_KEY`
   (fixed names, one pair per fleet repo). `gapp ci init --verbose` and
   `gapp ci list --verbose` report them; the register and deploy runs
   name them in words when missing. A public repo needs none of this.
7. `gapp ci deploy <name>` dispatches the deploy workflow with the
   deployment's name and streams the run; it needs `gh` logged in. Any
   GitHub tool that can dispatch `gapp-deploy.yml` with `deployment`
   and `ref` and read the run does the same job. An unknown name is not
   deployed; the outcome names step 4 and `gapp ci list`.
8. `gapp ci status <name>` is one deployment (repo, project, domain,
   last deploy, and the next step if a deploy cannot run), GitHub only
   and fast; `gapp ci status <name> --live` asks the project for the
   live service state (service up, URL, domain) through the fleet's
   read-only status workflow, which runs `gapp status` on a runner with
   the project's deploy identity, so it takes a runner start and needs
   only `gh`; `gapp ci list` is the fleet. `--verbose` adds the fleet
   repo, its files and variables, and the id; `--json` on any `ci`
   command returns the result dict only.

When you explain this to the user, say explicitly which parts are
per operator, per project, per source owner and per deployment,
because it decides how much work a second deployment is: `ci init` is
once per operator (and sets the fleet repo on each machine); `ci
project setup` (pool, provider, `gapp-deploy` service account,
Environment) is once per project and shared by every deployment on it;
`ci sources add` is once per GitHub owner; only `ci setup` is per
deployment, and it runs from any machine. Default output leads with the
deployment and what happened, then `Next: <command>`; relay the next
command.

Every `gapp ci` command ends in a plain outcome: it did the thing, or
`ok` is false and `next_step` says what stands in the way and the
exact command. There are no stack traces or raw errors to interpret;
relay the step. Something is called missing only when GitHub confirmed
it; "Couldn't check <thing>: GitHub returned <status>: ..." is a read
that failed, in GitHub's words, and says which request; do not read it
as missing. Every gcloud call `ci project setup` makes runs as the
account from `gapp config account` when one is set; if that account
has no login, it stops and names it rather than fall back to whatever
account gcloud has active. A solution that still has its own
per-solution workflow in the fleet repo from gapp 3.x runs from the
Actions tab; `gapp ci list --verbose` notes such workflows, and setting
the deployment up under its old name keeps its live resources and lets
the user delete the old file.

Recommend the fleet whenever the user deploys from more than one
machine, wants agents to deploy, wants the same repo on several
projects, does not want terraform locally, or wants deploys on push.

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

1. **Verify ownership first.** Cloud Run maps a domain only for an
   identity that has verified it or a parent, and a verification
   belongs to the account that did it, not to the project. Check
   with `gcloud domains list-user-verified`; if the domain or a parent
   is not listed, the user runs `gcloud domains verify <parent>`,
   which opens Search Console, and finishes there. Verify at the
   narrowest parent that is useful: one verification of
   `example.com` covers every `*.example.com` the user will map, and
   it keeps the verification record off the mapped name itself,
   because a TXT record and a CNAME cannot share a name. If the user
   must verify the exact host that will carry the CNAME, point them
   at Search Console's CNAME verification method instead of TXT.
   For CI deploys, the user also adds the deploy service account
   (`gapp-deploy@<project-id>.iam.gserviceaccount.com`) as a verified
   owner of that Search Console property (Settings, Verified owners,
   Add an owner); otherwise the runner cannot create or recreate the
   mapping. `gapp deploy` checks all this before terraform and stops
   with these steps when the identity has not verified the domain.
2. **Set it.** Local deploys: `domain:` in `gapp.yaml`, or init with
   `domain=`. CI deploys: `gapp ci setup <name> --repo <owner>/<repo>
   --project <id> --domain mcp.example.com`; the entry's domain wins
   over `gapp.yaml`, so a public repo can carry a placeholder such as
   `app.example.com` or no domain at all.
3. **Deploy.** The next deploy creates the domain mapping; the
   `.run.app` URL keeps working throughout.
4. **DNS.** The user adds `CNAME mcp.example.com -> ghs.googlehosted.com`
   at their registrar. Ask which registrar they use and walk them to
   its DNS page; this is the one step gapp cannot automate.
5. **Watch `gapp status`.** Its `domain` block reports `pending_dns`
   (CNAME missing or not propagated; the CNAME target is included),
   `pending_cert` (DNS right, certificate provisioning, usually
   minutes), `active`, or `error` with Cloud Run's message.

**Changing or removing a domain destroys the old mapping first.**
Terraform replaces a changed mapping by deleting the live one before
creating the new one, so a typo, a placeholder, or an omitted field
takes the domain down. `gapp deploy` reads the live mapping from
Terraform state before building and refuses, naming the live domain,
when the deploy would map a different domain or none. If the change
is intended, re-run with `--replace-domain` (`replace_domain=True`
on the SDK and MCP tool), then update the CNAME. If it is not, fix
`domain:` in `gapp.yaml` or re-run `gapp ci setup <name> ... --domain`
with the right value. A redeploy that keeps the same domain is never blocked,
even by an identity that did not verify the domain.
If the Terraform state cannot be read, the deploy refuses before building,
even with `--replace-domain`: fix the identity's access to the state bucket.

## Ongoing operations

**Redeploy.** Before redeploying, ask whether the change affects how
the app reads or writes its data. If the format or layout changed,
inspect the bucket (`gsutil ls gs://gapp-<name>-<project-id>/data/`)
and raise incompatibilities before deploying; the user may need a
migration or backup first. Then deploy by the chosen path. Remind
the user that uncommitted changes are not included.

**Operator data on the volume.** A solution that reads its data from
the path it maps to `{{SOLUTION_DATA_PATH}}` (a property definition,
a user list, seed files) gets that data by copying it to the same
bucket path from a logged-in machine, for example
`gsutil -m rsync -r <local folder> gs://gapp-<name>-<project-id>/data/<subdir>`;
the running service sees it on its next read. The solution's own
skill or README says what belongs there and how it finds out.

**Status and discovery.** `gapp status` for health, URL, custom
domain state, and next step. `gapp list` for every deployment on the
hosted projects, `gapp projects list` for env bindings. An expired or wrong gcloud login surfaces from these
as an authorization error naming the account; it is never reported as
"not deployed", so take such an error at face value and fix the login.

**Env bindings.** `gapp projects set-env <id> <env>` (idempotent for
the same value, `--force` to overwrite), `gapp projects clear-env
<id>` to return a project to `<undefined>`.
