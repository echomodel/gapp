# Contributing to gapp

This document covers the design, architecture and principles behind gapp.
Read it before contributing code. What gapp does for someone using it is
in the [README](README.md), and every command, file and MCP tool is
listed in [docs/INTERFACE.md](docs/INTERFACE.md).

## What gapp does

gapp deploys apps that serve HTTP to Google Cloud Run with Terraform. A
deployment is defined once, in the operator's fleet, and every command
works from that definition:

```
gapp project setup <project>     # once per project: APIs, Cloud Build's permissions, the project's mark
gapp add <name> --project <p>    # define a deployment; touches no cloud
gapp set / gapp env set          # its settings
gapp secrets set <secret-name>   # the secrets it reads
gapp deploy                      # build the definition's ref from a clone, terraform apply
```

## Design goals

**One answer to "what do I run, and where".** The fleet is the one record
of every deployment: its repo, project, ref and settings. No command
searches the cloud for deployments, and no command needs a particular
folder to act on one.

**Minimal cognitive load.** A first deploy needs a gcloud login and
nothing else: no GitHub, no file to create, no path to type. The CLI
always says what comes next.

**An app's repo does not know about gapp.** Nothing in a repo names gapp,
a project or Google Cloud. gapp reads a repo's Dockerfile or Procfile, and
the `env` block of its `app.json` if it has one, and never writes to a repo, so anyone can deploy the same repo to their own
project without changing it.

**Deployments are independent.** Deployments in one project do not know
about each other. Each has its own Terraform state, secrets, service
account and data bucket.

**State is in the cloud, out of the app's reach.** Terraform state and
the record of each deploy live in the project's own gapp bucket, not on
a workstation, so any authorized machine or runner can deploy and pick
up the same state. No app's service account is granted anything on that
bucket.

**A change to the fleet is a git commit and nothing else.** A command
that defines or changes a deployment fetches the fleet, rewrites the
file, commits and pushes. It calls no Google Cloud API and no git host's
API, starts no workflow and needs no CI. A local fleet is a file write.
What that keeps true:

- The fleet works on any git host.
- Defining what should run needs no cloud login.
- The operator's own git process applies to it: hooks, review, branch
  rules.
- Changes can later go to a branch and be merged on the operator's
  schedule, without a break.

A convenience that looks something up in the cloud during `gapp add`, or
calls a host's API to help a push along, breaks it.

**After one-time setup, GitHub access is enough.** Once a project Owner
has set up a project (`gapp project setup`) and connected it to the fleet
repo (`gapp ci projects connect`), a machine with only GitHub access,
such as a cloud agent session, can run a deployment's whole life: `add`,
`set`, `env`, `upgrade`, `rename`, `remove`, `gapp ci deploy` and `gapp ci
status --live`. Those two Owner steps are the only ones that need a
Google Cloud login. Today's exceptions are listed under Accepted limits:
setting a secret's value, and `gapp deployments list` and `scan`, which
read Google Cloud directly (`gapp list` and `gapp ci status --live` serve
a GitHub-only machine instead). A change that makes a GitHub-only machine
need a Google Cloud login for anything else breaks this goal.

**Each added capability is opt-in.** A fleet in a git repo, CI and
profiles are each something an operator turns on. Someone who uses none
of them never meets their words.

## Architecture

### Where things live

```
GAPP REPO (this repo: how to deploy)
  gapp/terraform/                       static HCL
  gapp/modules/cloud-run-service/       the Terraform module every deployment uses
  gapp/templates/                       the Dockerfile and Cloud Build templates; the CI workflows
  gapp/admin/sdk/                       all the logic
  .github/workflows/deploy.yml          the reusable workflow a fleet repo's deploy workflow calls

AN APP'S REPO (what gets deployed)
  Dockerfile or Procfile                how it builds and starts
  app.json (optional)                   the variables it reads, in Heroku's format

THE FLEET (what is defined)
  gapp.yaml                             every definition: name, id, repo, project, ref, gapp version, settings
    on this machine:                    ~/.config/gapp/fleets/<profile>/gapp.yaml
    or in a git repo:                   gapp.yaml at its root

GOOGLE CLOUD (what is deployed)
  Project label                         gapp=hosted: `gapp project setup` has run
  Bucket gapp-<id>-<project>            one per deployment
    terraform/state/                    Terraform state; not visible to the container
    data/                               the app's data, mounted at /mnt/data
    labels                              gapp-deployment=<id>, gapp-version=<the gapp that last deployed it>
  Cloud Run service <id>                labelled the same, by Terraform
  Secret Manager <id>-<secret-name>     labelled gapp-solution=<id>
  Artifact Registry gapp/               images, tagged with the commit

THIS MACHINE
  ~/.config/gapp/config.yaml            profiles: each one's login and fleet repo
  ~/.cache/gapp/<id>/terraform/         staged Terraform files and the generated tfvars
  ~/.cache/gapp/fleets/<repo>/          gapp's own clone of a fleet repo
```

### One fleet

Two records of what is deployed cannot be kept in step: one that a deploy
from a clone reads and one that CI reads will name things differently,
hold different settings, and each change the same deployment. So there is
one, the fleet, and every command reads it: a deploy from a clone, a
deploy on a runner, `status` and `secrets` all take the deployment's id,
project and settings from its definition.

The fleet is in exactly one place per profile: the profile's own file,
a file the operator named, or a git repo. There is never a second copy,
so nothing syncs. Why a git repo, why gapp makes the commits itself and
why that is separate from CI are in
[docs/FLEET-IN-GIT.md](docs/FLEET-IN-GIT.md).

There is one way to name a fleet (`fleetstore.target`), and both places
that name one use it: `gapp fleet use`, which records it in the profile,
and the `GAPP_FLEET` environment variable, which names it for one process
over the profile. A value means the same in both. `local` is the
profile's own file; a full path ending in `.yaml` or `.yml` is a fleet
file; anything else is a git repo, read as `fleetrepo` reads one. A
relative path is refused in both, because it would make the fleet depend
on the folder a command runs in, the per-folder behaviour one fleet
replaced; an MCP server's folder is whatever its client chose. A
`GAPP_FLEET` gapp cannot use fails every read and write and never falls
back to the profile, and `gapp fleet use` is refused while it is set,
since the profile's setting would not be what that process uses. Runners
set it to the fleet file in their checkout. Tests clear it for every test
(`tests/conftest.py`), so a value in a developer's shell never reaches one.

A fleet kept in a file keeps the copy each change replaces as a new
backup in gapp's state folder (`$XDG_STATE_HOME/gapp/backups`, one file
per change, named by the time), never beside the fleet file. A config
folder or a repo then holds only what was put there, so a dotfile
manager or `git status` never sees a stray file. A comment header names
the fleet it is a copy of, which keeps the backup a fleet file `gapp
fleet import` takes as it is. A fleet in a repo has no backups: git is
its history.

The entry file (`gapp/admin/sdk/entryfile.py`) starts with `format`.
A gapp reads `format` first and nothing else when it is not one it knows,
so a file written by a later gapp is never half-understood or written
over. Top-level keys a gapp does not know are kept when it writes.

### A deployment's identity

- **The id is fixed; the name is not.** Every cloud resource is keyed by
  the id: the service, its account, the data bucket, the secrets, the
  state. `gapp rename` changes the name only.
- **gapp picks the id**, when the deployment is added: the name, or the
  name with a number when that id is taken on the project. No command
  takes one. A flag for it would put a word in the operator's hands that
  they otherwise never need, and the one case that needs a chosen id
  (definitions for resources that already exist) arrives through `gapp
  fleet import`, where the file carries it.
- **A deployment is one app on one project.** Test and production are
  two deployments of one repo. There are no environments: a deployment
  and its project carry the same information.
- **A second deployment of a repo is made on purpose**, with `--copy-of`.
- **No owner namespaces.** Teams that must not touch each other's
  deployments use separate projects and IAM, or separate fleets.

### Which deployment a command means

A command names its deployment with `--deployment`, or runs in a clone
and means the deployment whose `repo` is the clone's remote
(`gapp/admin/sdk/lookup.py`).

| The fleet has | The command |
|---|---|
| one deployment for this repo | uses it |
| several | refuses, and prints the command for each |
| none | refuses, and names `gapp add` |

- **A folder's name is never used.** It is an accident of one machine;
  acting on it could land one app on another's service, data or secrets.
- **A remote names a repo, not a deployment.** One repo can have several
  deployments, and only the fleet knows how many. The remote finds
  candidates and the fleet decides.
- The remote read is `origin`; with no `origin` and exactly one remote,
  that one; with several, the command refuses and lists them.
- A fork has another owner, so it matches nothing.
- Nothing here needs GitHub: the key is the remote, whatever the host.

### What a deploy builds

A definition's `ref` is a branch, a tag or a commit of the app's repo,
and a deploy takes no ref of its own. With one, whoever can start a
deploy could build code that was never merged, whatever the app repo's
branch rules say. The ref belongs with the rest of the definition, where
a fleet that takes changes by pull request can review it. A release, a
rollback and trying a branch are changes to the definition.

From a clone, the clone must be on that ref: a branch must be the one
checked out (its unpushed commits are built), and a tag or commit must be
checked out exactly.

### The app's `app.json`

An app may list the variables it reads in `app.json`, Heroku's published
format, which gapp does not extend. The rules, and why:

- **The definition wins, and `app.json` fills in what it leaves out.**
  One rule covers every case: a variable the definition sets is used
  whatever `app.json` says, so a variable that was required and now has
  a default, or the reverse, needs no special handling.
- **It is read by a deploy only, from the commit the deploy builds**
  (`gapp/admin/sdk/appjson.py`). What a command reports must not change
  with the folder it is run in or the commit that happens to be checked
  out. `gapp show` and `gapp secrets list` report the definition; `gapp
  deploy --dry-run` is the preview that includes `app.json`, because it
  knows the commit.
- **Nothing from it is copied into the definition.** A copied default
  looks the same as a value the operator chose, so when the app's next
  version changed its default, gapp could not tell whether to follow.
  Keeping track of where each value came from would put a shadow of
  `app.json` in the fleet. With no copy there is nothing to go stale and
  nothing to resync.
- **`generator: "secret"` is a hint, not an instruction.** A secret is
  lasting state in Google Cloud, and the definition is the full account
  of a deployment's secrets, so a secret is made only when the definition
  asks for one. A variable `app.json` wants generated is treated as any
  other variable the definition must supply: the deploy refuses, and the
  refusal suggests `--secret --generate`. A deploy that created a
  credential because a file in a repo asked for one would also be the
  more surprising behaviour for a repo someone else wrote.
- **A variable `app.json` does not list is warned about, never
  refused,** and only when the file has an `env` block. It catches a
  misspelt name and a setting the app has dropped. An `app.json` need not
  list everything an app reads, so it cannot be an error; an app with no
  `app.json` has said nothing, so nothing is checked.

### gapp versions

A deployment carries two versions: its definition's (`gapp` in the fleet,
the gapp that deploys it and the one a runner installs) and its
infrastructure's (the gapp of its last successful deploy, in the deploy
record in the project). Each release sets two numbers, in
`gapp/__init__.py`, and `gapp/admin/sdk/versions.py` is the only code that
compares versions:

- **`COMPATIBLE_FROM`**: this gapp works as it is with definitions,
  infrastructure and workflows from this version on. It stays where it is
  while releases add without breaking, and a release that breaks
  anything listed below raises it to its own version. A raise is listed
  under Breaking in the CHANGELOG.
- **`UPGRADES_FROM`**: `gapp upgrade` and a deploy with `--upgrade` can
  carry anything from this version on up to this gapp. It matters only
  once `COMPATIBLE_FROM` has moved: it is how far back the new release's
  upgrade steps reach. Never above `COMPATIBLE_FROM`.

The rules:

- **A version move is two deliberate steps.** `gapp upgrade` moves the
  definition to this gapp's version, and the next deploy brings the
  infrastructure up too and must be told to (`--upgrade`). It knows from
  the deploy record: an upgrade is pending when the definition is newer
  than the infrastructure. `--upgrade` is refused when nothing is
  pending. A deploy never upgrades a deployment as a side effect. The
  first deploy by a newer gapp can change infrastructure, not just the
  app, so it happens only when someone asks for it, and never rides along
  with a routine deploy.
- **A deploy builds only with the definition's version.** A runner
  installs exactly that version; a deploy from a machine with another
  gapp installed is refused. So the infrastructure is only ever produced
  by the version the definition names.
- **Within `COMPATIBLE_FROM`, a newer gapp works with an older
  deployment.** It reads and changes the definition and reads the
  infrastructure without an upgrade, except for a setting added after the
  definition's version (`versions.SETTINGS_SINCE`, filled in by the
  release that adds one), which a runner installing the older gapp would
  not know.
- **Both versions are checked, against the same number.** A definition
  that was upgraded and never deployed has infrastructure on the older
  version, and a later gapp refuses it if that is below its
  `COMPATIBLE_FROM`, whatever the definition says.
- **There is no fleet-wide version.** Deployments in one fleet can be on
  different gapp versions, so a new gapp can be tried on one of them.
- **Only the newer gapp knows how to get to itself.** Upgrade steps
  belong to the gapp being upgraded to, and anything below its
  `UPGRADES_FROM` is refused with the gapp to install first.

### Commands that touch a deployment's infrastructure

A command checks the version of what it touches, and gets its deployment
in one of two ways (`gapp/admin/sdk/loading.py`), never any other:

- **`load_definition`**: commands that touch only the definition: `show`,
  `set`, `unset`, `env`, `apis`, `apply`, `add --copy-of`. Checks the
  definition's version. Never reads a cloud.
- **`load_deployment`** (or `load_definition` then `with_infrastructure`,
  for a deploy, which checks its clone first): commands that touch the
  infrastructure: `deploy`, `status`, `secrets list`, `secrets get`,
  `secrets set`, and `gapp ci runner deploy` and `status` on a runner.
  Checks the definition's version and the infrastructure's, from the
  deploy record. These commands already read the record, so the check
  costs a comparison, not a read.

`gapp upgrade` is the one command that reads a definition below
`COMPATIBLE_FROM` on purpose, and applies `UPGRADES_FROM` itself.
`INFRASTRUCTURE_COMMANDS` in `tests/unit/test_fleet_deploy.py` lists the
second group, and a test runs each against infrastructure this gapp does
not work with; a new command that touches infrastructure goes in this
list, in that one, and through `load_deployment`.

What counts as a breaking change between gapp and what it has deployed:
the labels and the project mark, the bucket's name and layout, how
secrets are named, the Terraform state's path, and the fleet file's
format.

### What gapp keeps in a project

`gapp project setup` makes one bucket per project, `gapp-<project>`:

```
deployments/<id>/terraform/     the deployment's Terraform state
deployments/<id>/deployed.yaml  the last deploy that succeeded
deployments/<id>/history/       one file per deploy, failed ones included
```

- **Why not the deployment's own bucket.** The app's service account can
  write to its data bucket. With state there, an app could rewrite the
  state its own next deploy trusts, and a record there could only ever
  be a claim to review.
- **Why a record at all.** Nothing else in Google Cloud says which repo,
  ref and settings a running service came from. The record holds the
  definition as deployed, the full commit, the image, when, by whom,
  and from where (a machine, or a runner with its run). It is what a
  later search for deployments the fleet does not hold can rebuild a
  definition from, and it is what says which gapp last deployed a
  deployment.
- **The record is a snapshot, never an authority.** No command reads a
  definition from it. The fleet stays the one record of what is meant to
  run; two authorities is the design this one replaced.
- **Latest and history are separate**, so the folder a person opens
  holds three things, and "what is running" never has to be worked out
  from a list. A failed deploy is written to the history only, so
  `deployed.yaml` always describes a deploy that succeeded.
- **The data bucket is Terraform's.** Everything a deployment has is in
  its state, so nothing is made outside it. A data bucket that exists
  before Terraform has it (a deploy that failed part way, a lost state)
  is taken into state as it is, never created over.
- **No secret's value** is in a definition, so none is in the record.

### Removing a deployment

`gapp remove` changes the fleet and nothing else.

- **It is not an archive.** A definition kept but marked removed keeps
  its name and id taken, gives `gapp add` a second meaning (bring it
  back), and makes every command learn to skip it, for something that is
  only "still there but ignored".
- **It writes no file and offers no recovery of its own.** The settings
  it removed are in the fleet repo's history, or in the backup a fleet
  kept in a file takes on every change, and its output gives the command
  that shows them.
  Finding and rebuilding what is deployed belongs to a lost or stale
  fleet, which is a different moment from a removal, and is designed for
  that case (see Open questions).
- **It prints no teardown commands.** A list that misses one resource
  reads as complete.
- **It says what a later `gapp add` means.** The id comes from the name,
  so a deployment of the same name on the same project uses the
  resources that were left, data included. The output says so.

## Static Terraform and generated tfvars

Apps never own Terraform files:

- **Static HCL** lives in this repo at `gapp/terraform/`.
- **One module**, `gapp/modules/cloud-run-service/`, makes the Cloud Run
  service, its service account, its data bucket, its access, its
  variables and secret references, its volume and its domain mapping.
- **At deploy time** gapp stages those files under `~/.cache/gapp/`,
  generates only `terraform.tfvars.json` from the definition, and runs
  `terraform init` and `apply`.

Every variable the module needs is declared with no default, so a deploy
that drops one fails at plan instead of deploying without, say, its data
volume.

## Container build

- **A Procfile app** is built from gapp's template
  (`gapp/templates/Dockerfile`), which installs the app's own declared
  dependencies and appends `CMD exec <web command>`. The shell runs the
  command, so `$PORT` expands when the container starts. The template
  names no framework and supplies no server.
- **A Dockerfile app** is built from its Dockerfile as it is.
- **The source is `git archive HEAD`**, so the image holds exactly the
  committed contents, and the commit is the image's tag.
- **An image that exists for the commit is not built again**, unless
  `--rebuild` is given.
- **One identity** is used for the whole deploy: gapp hands gcloud's
  access token to Terraform.

## Secrets

Secret values live in Secret Manager in the project that uses them, and
nowhere else. See [docs/SECRETS.md](docs/SECRETS.md).

- **The fleet holds a secret's name, never its value.** No command writes
  a value anywhere but Secret Manager, no MCP tool takes one, and the CLI
  reads one only from a prompt or stdin.
- **Secrets are scoped to the deployment.** The id is
  `<deployment id>-<secret-name>`, and each deployment's service account
  may read only the secrets its definition names.
- **Ownership is a label.** gapp will not adopt, overwrite or wire in a
  secret it did not label; it says what it found and what to run.
- **A generated secret is made once.** A redeploy never rotates one.

## Code architecture

### SDK first

All logic lives in `gapp/admin/sdk/`. The CLI and the MCP server are thin
wrappers that call it and format the result.

```
gapp/admin/
  sdk/
    entryfile.py     the fleet file's format: parse, dump, versions
    fleetstore.py    where the fleet lives; reading, writing, moving, export and import
    fleetrepo.py     a fleet in a git repo: gapp's clone, one commit per change
    fleet.py         what a definition's own fields may hold; the id
    lookup.py        which deployment a command means
    definitions.py   add, rename, show, upgrade and the settings commands
    settings.py      the settings: validation and the shape the deploy code reads
    core.py          project setup, deploy, status, list
    secrets.py       Secret Manager
    start.py         how an app starts
    appjson.py       what an app's app.json says about its variables
    ci.py            gapp ci: GitHub only, except `projects connect`
    ci_runner.py     what a workflow runs on a runner
    config.py        profiles
    cloud/           the provider: gcp.py, and dummy.py for tests
  cli/main.py        Click commands
  mcp/server.py      the MCP tools
```

If you are writing logic in a CLI command or a tool, move it to the SDK.

Results are dicts. A refusal is an exception in the SDK
(`definitions.Refused`, `fleet.FleetRefusal`, `config.ConfigRefusal`): the
CLI prints its message and exits 2, and a tool returns
`{"refused": true, "message": ...}`. The `gapp ci` functions return
`ok`, `refused`, `lines` and `next_step` instead, because a run can fail
part way.

### The interface is a contract

`docs/interface.yaml` lists every command, argument, option and MCP tool,
and `tests/unit/test_interface.py` compares the real CLI and MCP server
to it. Nothing is added, removed or renamed in code without changing that
file and [docs/INTERFACE.md](docs/INTERFACE.md) first.

- Exit codes: 0 done, 2 refused and nothing changed, 1 failed.
- Every command takes `--json` and prints what its tool returns.
- A message says what was found and the exact command that fixes it.

### Testing

**Sociable unit tests.** No mocks unless the test is of network I/O.
Isolate with temp dirs and environment variables.

- **Unit tests (`tests/unit/`)** are fast and local: no network, no
  credentials. They run real git in throwaway repos, and
  `DummyCloudProvider` stands in for Google Cloud.
- **`gapp ci` tests** run against an in-memory GitHub
  (`tests/unit/conftest.py`) that answers the REST shapes the SDK uses
  and fails on anything else. The lookup step of each workflow is run
  for real with yq.
- **Integration tests (`tests/integration/`)** only when asked for.

Test names say the scenario and the outcome:
`test_replacing_a_live_domain_is_refused_before_anything_is_built`.

Run them: `python -m pytest tests/unit`.

## What gapp owns

gapp sets up and wires the Google Cloud resources a deployment needs, in
its project, using Google Cloud access. It hands the app what it needs to
use them: environment variables, secrets and permissions.

The boundary is the platform and its credentials, not a category of
feature:

- gapp works with one kind of access, Google Cloud: the operator's own
  login, or the project's deploy account on a runner.
- A Google Cloud resource in the deployment's project is something gapp
  can own.
- Anything that needs another company's account and credentials is
  outside. gapp has no access to it, and holding that access would be a
  second set of credentials to protect, with its own ways to go wrong.
  For those, gapp only delivers their values to the app, as environment
  variables and secrets.

| Thing | A Google Cloud resource in the project? | gapp's? |
|---|---|---|
| The service, the data bucket, secrets | yes | yes |
| The domain mapping; a DNS record in Cloud DNS | yes | yes |
| A document or key-value store on Google Cloud | yes | yes, once gapp supports one |
| A DNS record at an outside registrar | no | no |
| An identity provider run by another company | no | no |
| A payment processor, a messaging service, an outside API | no | no |

So the list of what gapp sets up is not fixed. It is whichever
Google Cloud resources gapp has learned to set up, it grows as apps need
more, and it is bounded by the platform. How the app talks to a resource (a
mounted folder, an environment variable, the provider's own library) does
not decide whose it is; where the resource lives does.

Considered and set aside as the line: "what every app needs" (a custom
domain is optional and still gapp's); "what gapp can do knowing nothing
about the app" (gapp knows how it starts, what it reads and where its
data goes); "what the app uses without a vendor's library" and "what the
app makes no network request to" (both put a store on Google Cloud
outside, and neither is why an outside identity provider is).

## Google Cloud access

gapp never signs anyone in, and never stores or refreshes a Google Cloud
credential. gcloud owns the login, and on runners the runner's own
identity does. When gapp calls an API directly, it borrows a short-lived
token from gcloud for that call and keeps it only in memory. A second
login path, such as application-default credentials or gapp's own OAuth,
would give the operator two identities that can disagree and give gapp a
secret to protect.

When Google Cloud cannot be asked or refuses, one place says what to do:
the provider's `identity_hint` (`gapp/admin/sdk/cloud/`). Keep it that
way:

- **It names the account gapp acted as**, the `gapp config account` one
  or else gcloud's active one, and which case applies: no account signed
  in, an account gapp is set to with no login on this machine, a login
  that can no longer get a token (expired or revoked), or a signed-in
  account that cannot reach the project. Without the account, the
  operator has to guess which of those it is.
- **A missing gcloud is a message, never a traceback.** Every gcloud call
  goes through `GCPProvider._run_gcloud` or `util.run_gcloud`, which turn
  "not found" into `GcloudNotInstalled`, a `CloudAccessError`, so every
  caller that already handles a refused call handles it too.
- **Who is signed in is asked only after something failed,** and once
  per provider, so a working command never pays for it.
- **The cloud's own answer stays** in the error, on its own line, before
  the hint.
- **On a runner the hint is about the deploy identity's roles**
  (`ci_identity_hint`): signing in does not apply there.
- **Unit tests never run the real gcloud.** They stand in for it
  (`_run_gcloud`, `_gcloud_installed`, or a provider whose login is
  already known). `tests/unit/conftest.py` puts a stand-in gcloud first
  on the PATH that fails the test naming the call, so a stub that does
  not reach far enough is caught instead of quietly asking whatever
  account the machine running the tests is signed in to.

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

## Design principles

### 1. Separate the tool, the app, the definition and the deployment

This repo is the tool. An app's repo is the app. The fleet is what is
defined. Google Cloud is what is deployed. They must not bleed: no
project ids in an app's repo, no secret values in any repo or in the
fleet, nothing on a machine that cannot be had again from the fleet and
the cloud.

### 2. Terraform earns its keep

Even a simple deployment is five or six resources that depend on each
other. Terraform manages that graph; the shared module holds what is
common, and the definition's settings hold what differs.

### 3. Prove patterns before abstracting

Do the thing by hand two or three times before building an abstraction
for it. Three similar lines are better than a premature one.

### 4. One place to look

Every question has one command: `gapp list` for what is defined, `gapp
show` for one definition, `gapp status` for what is live.

### 5. Derive, don't configure

If a value follows from a convention, do not ask for it. A deployment's
bucket, service, state path and image tag all follow from its id, its
project and the commit.

### 6. The CLI always knows what's next

Every result, error and refusal ends with the next command to run.

### 7. Refuse before changing, and say so

A command checks everything it can before it changes anything, and a
refusal changes nothing. A deploy that would take a live domain down, or
apply an upgrade nobody asked for, stops before the build.

### 8. Missing only when the source says so

A read that failed is never reported as "not there". Unreadable Terraform
state is not "no domain", an unreadable fleet is not an empty fleet, and
a GitHub call that was refused is "could not check", with GitHub's own
answer.

### 9. Public repos must not depend on private repos

Someone must be able to deploy an app to their own project using only
public repos. Reusable logic belongs here; an operator's fleet holds only
their own definitions.

### 10. Bounded queries over project-wide scans

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

### 11. gapp commits only to the fleet repo

gapp never clones, commits to or pushes an app's repo. The fleet repo is
different: it is gapp's store for one file, gapp keeps its own clone of
it away from the operator's working folders, and each change is one
commit to that file. The two workflows `gapp ci init` installs are
written through the GitHub API, as files gapp owns.

## `gapp ci`

`gapp ci` is everything gapp does with GitHub Actions, and nothing else
in gapp needs it. How it works for an operator, and its security model,
are in [docs/CI.md](docs/CI.md). The rules to keep:

- **A fleet in a git repo does not imply CI.** `gapp fleet use` installs
  nothing but the fleet file. The repo can be on any git host and be
  deployed from by another CI system or none.
- **`gapp ci` never changes the fleet.** There is one way to change a
  definition, and it is not a workflow run.
- **A runner runs the same deploy.** The workflow checks out the fleet
  and the app, installs the gapp the definition names, and runs `gapp ci
  runner deploy <name>`. No deploy logic lives in a workflow.
- **`gapp ci` uses GitHub's REST API only**, through `gh api`.
- **Only `gapp ci projects connect` touches Google Cloud**, because it
  creates the trust a runner needs to sign in at all. Preparing a project
  for deployments is `gapp project setup`; the two do not overlap.
- **The allowed sources are a repository variable**, so a commit to the
  fleet file cannot widen whose code a runner deploys.
- **A run is tied to the request that started it** by an id at the end
  of its name. gapp never reports another run's result as its own.
- **Workflows pass inputs through the environment**, never into a
  script.

### Listing and scanning deployments

`gapp deployments list` and `gapp deployments scan`
(`gapp/admin/sdk/deployments.py`) are the two collection commands over
what is deployed. They only report: neither refuses a deployment for its
version, and neither changes anything. Their cost is bounded before
anything is read, and these rules keep it that way:

- **`list` starts from the fleet.** The fleet gives every name for
  nothing, so every filter that can (name, repo, project) runs on it
  first, and only the deployments left cost a read: one small deploy
  record each, read in parallel. `--limit` caps those reads.
- **`list` orders by name and nothing else.** Ordering by anything that
  is only known once deployed (its version, when it was deployed) means
  reading every record before the first row, which makes `--limit`
  pointless and the command O(n) whatever is asked. Do not add it.
- **`scan` starts from the cloud,** because it is for what the fleet does
  not hold. It finds gapp's projects with one search for the mark `gapp
  project setup` puts on a project, which Google Cloud filters on its side: the
  cost follows the number of gapp projects, not every project the login
  can see, which in a large organization is thousands. Nobody using it
  needs to know about the mark. Above `SCAN_MAX_PROJECTS` it stops and
  asks for `--project` rather than running long.
- **A read that fails is reported on its row** ("couldn't read"), never
  taken for "not deployed", and the other rows still come back.
- **Recovery lives here, not under `gapp fleet`.** It is about what runs,
  and `gapp fleet` is about the fleet as a whole. `scan` prints the same
  commands `gapp remove` prints for adding a deployment back
  (`definitions.add_again`).

### The runner hand-off

A runner deploy involves two gapp versions on purpose: the workflows come
from the gapp that ran `gapp ci init`, and the gapp they install is the
one the definition names. What the workflows give that gapp is the
runner hand-off: the commands `gapp ci runner deploy <name> [--upgrade]`
and `gapp ci runner status <name>`, the `GAPP_FLEET` variable naming the
checked-out fleet file, `GAPP_WORKFLOWS_VERSION` naming the workflows'
gapp, and the app checkout as the working folder for a deploy. It is part
of what `COMPATIBLE_FROM` covers, recorded per value of it in `HANDOFF` in
`tests/unit/test_ci_workflows.py`.

Each side checks the other against the same number. The workflows carry
their gapp's `COMPATIBLE_FROM` and refuse an older definition before
installing anything; the gapp on the runner refuses workflows older than
its own `COMPATIBLE_FROM`; and `gapp ci deploy` and `gapp ci status
--live` make both checks before starting a run ("upgrade it first", or
"gapp ci init"). A release that changes the hand-off either keeps the old
form as well (adding a variable an older gapp ignores is fine) or raises
`COMPATIBLE_FROM` and records the new hand-off in `HANDOFF`; the test
fails when it does neither. Without these checks, a runner deploy the
workflows cannot run starts anyway and fails halfway with an error about
something else (a fleet that seems empty), instead of being refused with
what to do.

## The MCP server

`gapp-mcp` serves one tool per command, named after it, with the same
arguments, calling the same SDK function. Register it:

```bash
claude mcp add --scope user gapp-admin gapp-mcp
agy mcp add gapp-admin -- gapp-mcp
```

A tool never takes a path on the machine it runs on (it takes the
settings object or the fleet document) and never takes a secret's value.

## Version management

**Single source of truth:** `gapp/__init__.py` holds `__version__`. Git
tags are the version with a `v` in front.

### Where the version actually lives

`gapp/__init__.py` is the single source of truth for the version
string. Two derived locations track it:

| File | Mechanism |
|---|---|
| `gapp/__init__.py` | **Source.** `__version__ = "X.Y.Z"`. |
| `pyproject.toml` | **Auto-derived.** Declares `dynamic = ["version"]` and reads `gapp.__version__` via `[tool.setuptools.dynamic]`. Never edit the version directly here — there isn't one to edit. |

### Release workflow

A release is one local prep command, a merge, and a reinstall. The local
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

#### 2. Get the commit onto main; the tag follows

Merge the release's pull request (or push the commit to `main`). The
`Tag release` workflow (`.github/workflows/tag-release.yml`) runs when
`gapp/__init__.py` changes on `main`: it tags that commit `v<version>`
and publishes a GitHub release for it, marked a pre-release for an rc. It
does nothing when the tag already exists. Nobody types or pushes a tag,
so a release can be finished from a phone or from a session that cannot
push tags. When the release goes through a pull request, delete the
local tag `scripts/release.py` made (`git tag -d vX.Y.Z`): the workflow
tags the merge commit.

To tag by hand instead, push the tag before the commit reaches `main`:

```bash
git push origin vX.Y.Z && git push origin main
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

For development, use an editable install: `pipx install -e .`

## Considered and rejected

- **Two records of what is deployed** (one for deploys from a clone, one
  for CI), each patched towards the other. Every patch adds a path
  between them while the two authorities remain.
- **Finding deployments by searching the cloud for labels.** A list of
  what one login can see is not a list of what is meant to run, and it
  cannot say what a deployment's settings should be.
- **Settings in the app's repo** (a `gapp.yaml` there). It puts one
  operator's choices in a repo others deploy too, and makes a second
  place a setting can come from.
- **A local and a remote copy of the fleet, synced.** Any two-copy design
  has lost updates and stale reads, with or without git.
- **The fleet in GitHub variables.** No history, last write wins, small
  size limits.
- **Changing the fleet through workflow runs.** It ties the fleet to one
  CI system and makes every change wait for a runner.
- **A `--ref` on deploy.** See "What a deploy builds".
- **A fleet-wide gapp version.** One deployment could not be upgraded
  ahead of the rest.
- **A hosted service for the fleet.** gapp has no server and should not
  need one.
- **An `--id` on `gapp add`.** See "A deployment's identity".
- **Archiving instead of removing, and `gapp remove` saving what it
  removed to a file.** See "Removing a deployment".
- **The deploy record in the deployment's own bucket, or as labels and
  annotations.** The app can write the first, and the second is too
  small for a definition. See "What gapp keeps in a project".

## Accepted limits

- Backups of a fleet kept in a file are never pruned or capped.
- With the fleet in a repo, every fleet command needs git access to it.
- `gapp ci` works with a fleet repo and app repos on github.com.
- Moving the fleet to another repo means `gapp ci projects connect`
  again for each project, because the trust names the repo.
- A session with no gcloud login cannot set a secret's value.
- `gapp deployments list` and `scan` need a Google Cloud login.
- Nothing takes a deployment's resources down.
- After `gapp remove`, a different app added under the same name on the
  same project deploys onto what the removed one left, data included.
  `gapp remove` says so; nothing refuses it yet.
- A project set up before the project bucket existed needs `gapp project
  setup` again; a deploy to it is refused with that command.
- Deployments made before the deploy record existed have none until
  their next deploy.

## Open questions

- A deploy that refuses when the record under its id names another
  repo. It would close the reused-name case above, but a repo that has
  moved owners looks the same, so it needs a deliberate way through.
- A deeper `status` that asks Google Cloud about every resource in the
  state.
- `status` calls a fixed `/health` path without credentials, so an app
  with no such route, or one that is not public, reads as unhealthy.
- Taking a deployment's resources down.
- Finding deployments the fleet does not hold and rebuilding their
  definitions from the deploy record: one command to search, one to
  rebuild, shown for review before anything is written.
