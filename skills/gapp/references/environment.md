# Environment: can this machine do the step?

Read this when a probe from SKILL.md Step 0 came back empty, or
before any step that touches GCP or GitHub. The point is to never
surprise the user with a failed command, never ask them to do
something this machine could have done, and never try to do
something only their other machine can do.

## What each step needs

| Step | gapp admin tools | GCP identity | terraform | GitHub write | Notes |
|---|---|---|---|---|---|
| init (write `gapp.yaml`) | optional | no | no | optional | `gapp init` adds a repo topic when `gh` is logged in; otherwise it just writes the file. Writing `gapp.yaml` by hand from the schema is fine too. |
| projects set-env / clear-env | yes | project admin | no | no | one label write |
| setup | yes | project admin: enable services, create bucket, edit IAM, write labels | no | no | once per solution per project, idempotent |
| ci init | yes | no | no | yes, API writes to the private CI repo | once per operator; installs gapp's two workflows and `gapp/solutions.yaml` |
| first ci setup on a project | yes | project admin: WIF pool, service account, IAM | no | dispatch, plus an API write for the Environment | pool and SA are shared per project; setup records them as the CI repo's GitHub Environment `<project-id>`; never runs from a machine without gcloud |
| ci setup on an already-set-up project | yes | **no** (`--project <id>`) | no | **workflow dispatch only** | runs from any machine, including a cloud session whose GitHub proxy allows only reads and dispatch: it dispatches the register workflow, which writes the entry with its own token |
| ci setup, private solution repo | yes | as above | no | as above, plus the hosting GitHub App's id and key stored in the CI repo | the app is created and installed by the user in a browser, once per operator; see below |
| custom domain | yes | the deploying identity must have verified the domain or a parent | no | no | verification happens in Search Console, a browser step for the user; see below |
| secrets list / get / set | yes | Secret Manager | no | no | |
| status / list | yes | read-only | no | no | |
| deploy, local | yes | deploy roles | yes | no | clean tree |
| deploy, via CI | no | no | no | dispatch the workflow | the runner authenticates with WIF |

"Project admin" means an identity that can enable APIs, create
service accounts, edit project IAM, and create Workload Identity
pools. The `gapp-deploy` service account CI uses deliberately
cannot do those, which is why the one-time steps need a human's
identity or a purpose-built bootstrap identity.

## The three outcomes

Compare the row for the next step with what Step 0 found.

### 1. Everything needed is here: proceed

Run the step. Do not ask whether this is the user's workstation;
the probes already answered the only question that matters. If the
identity exists but is the wrong kind (for example a deploy-only
service account when setup needs admin), the gcloud error will say
permission denied; report which role is missing rather than
retrying.

### 2. Tooling is missing: install it, on any machine, without asking

A missing tool is never a reason to hand off. Install what is missing,
pinned to the current gapp release, and move on:

- gapp CLI, on a machine the user keeps: `pipx install "gapp @ git+https://github.com/<gapp-repo>.git@<tag>"`
  (or `pip install --user`). On an ephemeral or shared machine, a
  temporary prefix instead: `pip install --target <tmp> "gapp @ git+...@<tag>"`
  with that prefix on `PYTHONPATH` and `PATH`. Either way it changes
  nothing the user cares about and unlocks every read-only command,
  `gapp init`, and `gapp manifest schema` immediately.
- terraform, when a local deploy is the chosen path: the platform's
  package manager (`brew install terraform`, `apt install terraform`,
  or the HashiCorp installer).
- gh, when CI wiring or triggering is the chosen path and it is absent.

Pin the same tag on every machine that touches a project; mixed
versions are how label and bucket contracts drift.

Then configure gapp from what is discoverable, asking only when it is
ambiguous: if exactly one gcloud account is active, pin it with
`gapp config account <email>`; set `gapp config owner` only when the
user has named an owner namespace; leave discovery on.

What installation cannot supply is an identity. A browser login
(`gcloud auth login`, `gh auth login`) is the one step that needs the
user at the keyboard. Run it where a browser is reachable and tell the
user it is coming; where no browser is reachable, go to outcome 3.
Never type, store, or echo a credential yourself, and if the user
offers to paste a key into chat, decline and point them at the login
flow or at their environment's secret store.

A long-lived cloud environment the user controls (a Codespace, a cloud
agent environment with a setup script and secrets) can carry gapp,
terraform, and a service-account credential in its setup; suggest it
when the user keeps hitting outcome 3, and note that a stored key is a
tradeoff against gapp's no-stored-credentials principle. It is their
call.

Two more steps are the user's, in a browser, and no tool replaces them:

- **The hosting GitHub App** that lets CI clone a private solution repo.
  The user creates it on the GitHub organization that holds their
  deployable repos (Contents read-only, no webhook, installable only on
  that account), installs it there, and generates a private key. Then,
  from a terminal with `gh`, they store the id and the key in the CI
  repo under the fixed names (`gh variable set HOSTING_GITHUB_APP_ID ...`,
  `gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY < key.pem`) and delete
  the key file. Never ask for the key in chat and never store it
  anywhere else. `gapp ci init` and `gapp ci status` tell you when
  this is still missing and print those two commands, along with the
  one for `GAPP_ALLOWED_OWNERS`, the CI repo variable listing the
  GitHub owners whose repos may be registered; that one every
  registration needs.
  Say explicitly: install the app on an org of deployable repos, never
  on all repositories of a personal account.
- **Domain ownership verification** for a custom domain. The user runs
  `gcloud domains verify <parent-domain>` and finishes in Search
  Console, and for CI deploys adds the `gapp-deploy` service account as
  a verified owner there. `references/lifecycle.md`, "Custom domain",
  has the order of steps.

### 3. Something is missing and this machine cannot get it: route elsewhere

Applies only to identity: the machine cannot complete a browser login
and holds no GCP credential. Tooling never lands here; outcome 2 covers
it. Before handing anything off, do everything that
does not need the missing capability:

- install the gapp CLI if it is missing (outcome 2), then use it for
  everything that needs no identity: `gapp init`, `gapp manifest
  schema`, `gapp ci status`
- write and commit `gapp.yaml`, push the branch
- if GitHub access exists, check whether CI is already wired
  (`gapp ci status`) and, when the project already has its
  Environment, register the solution from here with
  `gapp ci setup --solution <s> --solution-repo <owner>/<repo>
  --project <id>`; that needs only workflow dispatch
- if CI is wired and the step is deploy, dispatch the workflow and
  watch the run; that is a complete deploy with no handoff

When this machine reaches GitHub through a proxy that refuses API
writes (cloud agent sessions often do), `ci setup` and `ci trigger`
still work: both only dispatch a workflow, and the register run does
the write with its own token. A check the machine cannot perform (the
Actions variables and secrets API) is reported as not checked, which
is not a failure: the run performs it. What such a machine cannot do
is `ci init` (API writes) and the first `ci setup` on a project
(gcloud); hand those off. Do not create a verify or pre-flight
workflow in the CI repo; the deploy run is the verification.

Then hand off only the steps that truly need the other machine, in
this shape:

```
What this machine could not do: <setup / CI wiring / secret set> for <solution> on <project>.
Why: no <GCP identity with project admin rights / gapp admin tools / terraform> here, and an interactive login is not possible in this session.

On your workstation (where gapp and gcloud are logged in), in the <solution> repo on branch <branch>:

1. git pull
2. gapp setup <project-id>              # or the MCP call gapp_setup(project_id="<project-id>")
3. gapp ci init <owner>/<ci-repo>       # once per operator; installs gapp's workflows; or gapp_ci_init(repo=...)
4. gapp ci setup --solution <solution> --project <project-id>  # first solution on the project: creates WIF, service account, records the Environment, registers the solution; or gapp_ci_setup(...)
   # (another solution on an already-set-up project needs no gcloud and no handoff: run
   #  gapp ci setup --solution <solution> --solution-repo <owner>/<repo> --project <project-id> here instead)

Then tell me "setup done" and I will trigger the deploy from here.
```

Keep the handoff literal and ordered, and use only commands that
exist (`references/tools.md`). Omit steps the user has already done;
`gapp status` output or the CI repo contents tell you which.

After the user reports back, re-probe rather than trusting the
report: `gapp status` or the workflow file's presence confirms it.

## Hints about the surface, used lightly

Environment variables such as `CLAUDE_CODE_REMOTE`, `CI`,
`CODESPACES`, or `GITHUB_ACTIONS` suggest an ephemeral or headless
machine where a browser login is unlikely. Use them only to choose
between a persistent install and a temporary prefix, and between
running a login here and outcome 3. They are
not evidence of what is installed, and they say nothing about a
user who deliberately provisioned their cloud environment with
credentials. The probes decide; the hints only break ties, and
when they cannot, ask the user one direct question: "Can you
complete a browser login from this machine, or should I hand setup
off to your workstation?"

## Never do these to get unblocked

- Put a backend or GCP credential in `gapp.yaml`, a Cloud Run env
  var, the repo, or chat.
- Run `gcloud auth login` in a session that cannot show the user a
  browser.
- Pretend a deploy happened. If nothing ran, say what ran and what
  did not.
