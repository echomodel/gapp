# Environment: can this machine do the step?

Read this when a probe from SKILL.md Step 0 came back empty, or before
any step that touches Google Cloud or GitHub. The point is to never
surprise the user with a failed command, never ask them to do something
this machine could have done, and never try something only their other
machine can do.

## What each step needs

| Step | gapp | Google Cloud login | terraform | GitHub | How often |
|---|---|---|---|---|---|
| Fleet commands: `add`, `set`, `env`, `apis`, `apply`, `show`, `list`, `fleet`, `upgrade` | yes | no | no | git access to the fleet repo, when the fleet is in one | any time |
| `project setup` | yes | an Owner of the project | no | no | once per project |
| `secrets list`, `get`, `set` | yes | Secret Manager access | no | no | any time |
| `status` | yes | read access | yes | no | any time |
| `deploy` | yes | deploy rights | yes | a clone of the app's repo | any time |
| `ci init` | yes | no | no | `gh` as an admin of the fleet repo | once, and after installing another gapp |
| `ci projects connect` | yes | an Owner of the project | no | `gh` as an admin of the fleet repo | once per project |
| `ci deploy`, `ci status --live` | yes | no | no | `gh` that can start workflows | any time |
| `ci status`, `ci projects list` | yes | no | no | `gh` | any time |

An Owner is needed for the one-time steps because they enable APIs,
create service accounts and change access. The `gapp-deploy` identity a
runner uses deliberately cannot do those.

Every `gapp ci` result says `ok` and, when false, carries `next_step`
with a plain sentence and the exact command; relay it. A line starting
"Couldn't check ..." is a read that failed, in GitHub's words, never a
verdict that something is missing.

## The three outcomes

Compare the row for the next step with what Step 0 found.

### 1. Everything needed is here: proceed

Run the step. Do not ask whether this is the user's workstation; the
probes already answered the only question that matters. If the login
is the wrong kind (a deploy-only account when a step needs an Owner),
the error says permission denied; report which step needs which login
rather than retrying.

### 2. Tooling is missing: install it, on any machine, without asking

A missing tool is never a reason to hand off. Install what is missing,
pinned to the current gapp release, and move on:

- gapp, on a machine the user keeps: `pipx install "gapp @ git+https://github.com/<gapp-repo>.git@<tag>"`.
  On a throwaway machine, a temporary prefix instead: `pip install
  --target <tmp> "gapp @ git+...@<tag>"` with that prefix on
  `PYTHONPATH` and `PATH`.
- terraform, when a deploy from this machine is the chosen path: the
  platform's package manager or the HashiCorp installer.
- gh, when `gapp ci` is the chosen path and it is absent.

Then point gapp at the user's fleet. If they keep it in a git repo,
`gapp fleet use <owner>/<repo>`; ask which repo when you do not know.
When `GAPP_FLEET` is set, it already names the fleet (`gapp fleet` says
so) and `gapp fleet use` is refused; leave it as it is. A fleet that
cannot be read then means this machine has no access to that repo:
get the access, never work around it with a local fleet.
A new machine with no fleet set has an empty local one, which is not
the user's: `gapp list` showing nothing on a machine they have not
used before means "not connected to the fleet yet", not "nothing is
deployed".

If exactly one gcloud account is active, nothing more is needed. With
several, ask which, then `gapp config account <email>`.

What installing cannot supply is a login. A browser login (`gcloud auth
login`, `gh auth login`) is the one step that needs the user at the
keyboard. Run it where a browser is reachable and tell the user it is
coming; where none is, go to outcome 3. Never type, store or echo a
credential yourself, and if the user offers to paste a key into chat,
decline and point them at the login or at their environment's secret
store.

Two more steps are the user's, in a browser, and no tool replaces them:

- **The GitHub App** that lets a runner clone a private app repo. The
  repo's `docs/CI.md`, "Private app repos". Say explicitly: install it
  on an organization of deployable repos, never on all repositories of
  a personal account.
- **Domain ownership** for a custom domain. `references/lifecycle.md`,
  "Custom domain".

### 3. A login is missing and this machine cannot get one: route elsewhere

Applies only to logins. Before handing anything off, do everything that
does not need the missing one:

- install gapp (outcome 2) and connect it to the fleet
- make the repo startable, commit and push
- define the deployment and give it its settings: fleet commands need
  no cloud login
- if `gh` works and the one-time CI steps are done, `gapp ci deploy` is
  a complete deploy from here with no handoff, and `gapp ci status
  --live` reports what is running

What such a machine cannot do: `gapp project setup` and `gapp ci
projects connect` (an Owner's gcloud login), setting a secret's value
(gcloud), and a deploy from the machine itself.

Hand off only those steps, in this shape:

```
What this machine could not do: <project setup / connecting the project / setting a secret> for <deployment> on <project>.
Why: no Google Cloud login here, and a browser login is not possible in this session.

On a machine where gcloud is signed in as an Owner of <project>:

1. gapp project setup <project>
2. gapp ci projects connect <project>
3. gapp secrets set <secret-name> --deployment <deployment>

Then tell me "done" and I will deploy from here.
```

Keep it literal and ordered, with no comments inside the commands, and
use only commands that exist (`references/tools.md`). Leave out steps
already done: `gapp ci projects list` and `gapp secrets list` (where it
can run) say which.

After the user reports back, check rather than trust the report: `gapp
ci projects list`, then a dry run or the deploy itself.

## Hints about the surface, used lightly

Variables such as `CLAUDE_CODE_REMOTE`, `CI`, `CODESPACES` or
`GITHUB_ACTIONS` suggest a throwaway or headless machine where a
browser login is unlikely. Use them only to choose between a lasting
install and a temporary prefix, and between running a login here and
outcome 3. They are not evidence of what is installed, and they say
nothing about a user who deliberately gave their cloud environment
credentials. The probes decide; the hints only break ties, and when
they cannot, ask one direct question: "Can you complete a browser login
from this machine, or should I hand that step off?"

## Never do these to get unblocked

- Put a credential in a repo, the fleet, a plain variable or chat.
- Run `gcloud auth login` in a session that cannot show the user a
  browser.
- Edit the fleet file by hand, or copy it between machines.
- Change a deployment's ref, project or settings to make a refused
  command go through.
- Pretend a deploy happened. If nothing ran, say what ran and what did
  not.
