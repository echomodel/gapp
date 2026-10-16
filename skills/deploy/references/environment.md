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
| ci init / ci setup | yes | project admin: WIF pool, service account, IAM | no | yes, to the private CI repo | once per solution; pool and SA are shared per project |
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

### 2. Something is missing and this machine can get it: prepare it, with consent

Applies when the machine is one the user keeps, and interactive
login is possible. Tell the user exactly what is missing and for
which step, then ask once whether they want this machine prepared.
On yes, run each item with its own approval, because each changes
the machine:

- gapp CLI: `pipx install "gapp @ git+https://github.com/<gapp-repo>.git@<tag>"`, or the user's packaging convention. Pin a tag; the same version should be on every machine that touches a project.
- GCP identity: `gcloud auth login` (opens a browser the user completes), then `gcloud config set account <email>` or `gapp config account <email>` so gapp pins the account.
- terraform: the platform's package manager (`brew install terraform`, `apt`, or the HashiCorp installer).
- GitHub: `gh auth login`.

Never type, store, or echo a credential yourself. If the user
offers to paste a key into chat, decline and point them at the
login flow or at their environment's secret store.

A long-lived cloud environment the user controls (a Codespace, a
cloud agent environment with a setup script and secrets) counts as
"can get it" when the user says so: suggest they add gapp,
terraform, and a service-account credential to that environment's
setup, and note that a stored key is a tradeoff against gapp's
no-stored-credentials principle. It is their call.

### 3. Something is missing and this machine cannot get it: route elsewhere

Applies to ephemeral or shared machines, or whenever interactive
login is impossible. Before handing anything off, do everything that
does not need the missing capability:

- write and commit `gapp.yaml`, push the branch
- if GitHub access exists, check whether CI is already wired and
  draft the operator workflow for the CI repo
- if CI is wired and the step is deploy, dispatch the workflow and
  watch the run; that is a complete deploy with no handoff

Then hand off only the steps that truly need the other machine, in
this shape:

```
What this machine could not do: <setup / CI wiring / secret set> for <solution> on <project>.
Why: no <GCP identity with project admin rights / gapp admin tools / terraform> here, and an interactive login is not possible in this session.

On your workstation (where gapp and gcloud are logged in), in the <solution> repo on branch <branch>:

1. git pull
2. gapp setup <project-id>              # or the MCP call gapp_setup(project_id="<project-id>")
3. gapp ci init <owner>/<ci-repo>       # once per operator; or gapp_ci_init(repo=...)
4. gapp ci setup --solution <solution>  # creates WIF, service account, workflow; or gapp_ci_setup(...)

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
the default suggestion between outcome 2 and outcome 3. They are
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
