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
command excepted), and its record is the CI repo, not the project
label. A solution is deployed by one system or the other; never mix
them for one solution, and never expect `gapp status` to know about
CI or `gapp ci status` to know about GCP. Set up once, then deploy
from anywhere with GitHub access: the GitHub UI, a phone, a cloud
agent. No terraform or gcloud on the deploying machine.

1. `gapp ci init owner/ci-repo` designates a private CI repo (an
   address book of solution to project, not a product) and installs
   gapp's three files in it through the GitHub API: the generic
   deploy workflow `gapp-deploy.yml`, the register workflow
   `gapp-register.yml`, and the data file `gapp/solutions.yaml`. Once
   per operator, from a machine whose `gh` can write to the repo;
   re-run after a gapp upgrade to update the workflows. Never
   hand-edit gapp's files.
2. `gapp ci project setup <project-id>` makes the CI repo trusted to
   deploy to the project: it creates or confirms the Workload
   Identity pool and provider and the `gapp-deploy` service account
   (shared by every solution on the project), binds the pool to the
   CI repo, and records them in the CI repo as a GitHub Environment
   named after the project (`GAPP_WIF_PROVIDER`,
   `GAPP_DEPLOY_SERVICE_ACCOUNT`), which the deploy job runs in. Once
   per project, idempotent. **This is the one `ci` command that
   touches GCP**: it needs gcloud signed in as an Owner of the project
   and `gh` signed in as an admin of the CI repo, and it is the one
   human step to hand off when this machine lacks them.
3. `gapp ci sources add <owner>` allows repos owned by a GitHub owner
   to deploy through the CI repo (the variable `GAPP_ALLOWED_SOURCES`).
   Once per owner; needs `gh` with write access to the CI repo.
4. `gapp ci setup --solution <s> --solution-repo <owner>/<repo>
   --project <project-id> [--domain mcp.example.com]` registers the
   solution: it dispatches the register workflow, which checks the
   project's Environment, the source allowlist, the repo's
   reachability and that no other project holds the solution, then
   writes the entry in `gapp/solutions.yaml` with its own token. So
   **this needs only a `gh` that can dispatch workflows, and never
   gcloud**: from any machine, including this one. `--project` is
   required; the CI system never looks a project up in GCP. When the
   project is not yet trusted, or the owner not yet allowed, the
   command stops with a verbatim one-time message naming step 2 or
   step 3 and repeating the command to run again; relay it as is.
   Deployment values go into the entry, never into the solution repo:
   `--domain` overrides `gapp.yaml` at deploy time, a re-run without
   `--domain` keeps it, `--domain ""` removes it.
5. **Private solution repo.** The runner's built-in token can read
   only the CI repo, so a private solution repo is cloned with a
   one-hour token from a GitHub App the user installs on the repo's
   owner (a browser step, `references/environment.md`). The deploy
   workflow passes the app's id and key from the CI repo's Actions
   variable `HOSTING_GITHUB_APP_ID` and secret
   `HOSTING_GITHUB_APP_PRIVATE_KEY` (fixed names, one pair per CI
   repo, never renamed per solution). `gapp ci init` and `gapp ci
   status` report them and print the commands to store them; the
   register and deploy runs name them in words when missing. A public
   solution repo needs none of this.
6. `gapp ci deploy` dispatches the deploy workflow with the
   solution's name and streams the run; it needs `gh` logged in. Any
   GitHub tool that can dispatch `gapp-deploy.yml` with `solution`
   and `ref` and read the run does the same job. An unregistered
   solution is not deployed; the outcome names step 4.

When you explain this to the user, say explicitly which parts are
per operator, per project, per source owner and per solution,
because it decides how much work a second solution is: `ci init` is
once per operator; `ci project setup` (pool, provider, `gapp-deploy`
service account, Environment) is once per project and shared by
every solution on it; `ci sources add` is once per GitHub owner;
only `ci setup` is per solution, and it runs from any machine.

Every `gapp ci` command ends in a plain outcome: it did the thing, or
`ok` is false and `next_step` says what stands in the way and the
exact command. There are no stack traces or raw errors to interpret;
relay the step. Every gcloud call `ci project setup` makes runs as
the account from `gapp config account` when one is set; if that
account has no login, it stops and names it rather than fall back to
whatever account gcloud has active. A solution that still has its
own per-solution workflow in the CI repo runs from the Actions tab;
`gapp ci status` lists such solutions, and registering one lets the
user delete the old file.

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
   `domain=`. CI deploys: `gapp ci setup --project <id> --domain
   mcp.example.com`; the entry's domain wins over `gapp.yaml`, so a
   public solution repo can carry a placeholder such as
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
`domain:` in `gapp.yaml` or re-run `gapp ci setup --domain` with the
right value. A redeploy that keeps the same domain is never blocked,
even by an identity that did not verify the domain.

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
domain state, and next step. `gapp list` for the active owner
namespace, `gapp list --all` for every namespace, `gapp projects list`
for env bindings. An expired or wrong gcloud login surfaces from these
as an authorization error naming the account; it is never reported as
"not deployed", so take such an error at face value and fix the login.

**Env bindings.** `gapp projects set-env <id> <env>` (idempotent for
the same value, `--force` to overwrite), `gapp projects clear-env
<id>` to return a project to `<undefined>`.
