# CI: deploying on GitHub Actions

`gapp ci` runs the deploy `gapp deploy` does on a GitHub Actions runner.
The machine that asks needs only the GitHub CLI, `gh`: no gcloud, no
Terraform, no clone of the app. It is optional, and nothing else in gapp
needs it.

It works on a fleet kept in a GitHub repo, called the fleet repo here.
The workflows run in that repo and read its `gapp.yaml`.

## From a fleet to a deploy on a runner

```bash
gapp fleet use owner/my-fleet --create
gapp ci init
gapp project setup my-project
gapp ci projects connect my-project
gapp ci deploy --deployment my-app
gapp ci status --deployment my-app --live
```

| Step | Does | How often | Needs |
|---|---|---|---|
| `gapp fleet use` | Keeps the fleet in the repo | Once | git access to it |
| `gapp ci init` | Installs the deploy and status workflows in the fleet repo | Once, and again after installing another gapp | git push access to the fleet repo that can change workflows (for a token, the `workflow` scope); `gh` |
| `gapp project setup <project>` | Prepares the project for deployments | Once per project | gcloud as an Owner |
| `gapp ci projects connect <project>` | Lets the fleet repo's runners sign in to the project | Once per project | gcloud as an Owner; `gh` as an admin |
| `gapp ci deploy` | Deploys on a runner | Each deploy | `gh` |
| `gapp ci status` | The last deploy run; `--live` asks the project what is running | Any time | `gh` |

Each command's output names the next one. Every command ends in one of
three ways: it did the thing; it was refused and nothing changed (exit
code 2); or something failed part way (exit code 1). The message says
which, and the command that fixes it.

`gapp ci` never changes the fleet. A definition is changed with `gapp
add`, `gapp set` and the other fleet commands, and a runner reads the
result.

## From a machine with only GitHub access

Once a project Owner has run `gapp project setup` and `gapp ci projects
connect` for a project, a machine with only GitHub access (a teammate's
laptop, a CI job, a cloud agent session) can run a deployment's whole life
in that project. It needs `gh` and git access to the fleet repo, and no
Google Cloud login.

| Works with GitHub access only | Needs a Google Cloud login |
|---|---|
| `gapp add`, `set`, `env`, `upgrade`, `rename`, `remove` (each a commit to the fleet repo) | `gapp project setup`, `gapp ci projects connect` (once per project, as an Owner) |
| `gapp list`, `gapp show` | `gapp secrets set` (a value someone supplies; generated secrets need nothing) |
| `gapp ci deploy`, `gapp ci status --live` | `gapp deployments list`, `gapp deployments scan` |
| `gapp ci init` | `gapp deploy`, `gapp status` (they act from this machine) |

## What a run does

`gapp ci deploy` starts the fleet repo's deploy workflow with the
deployment's name and nothing else. The run:

1. Reads the deployment's definition from `gapp.yaml` in the fleet repo.
2. Refuses a project the runners are not connected to.
3. Checks out the definition's ref of the app's repo (its default branch
   when it names none).
4. Installs the gapp version the definition names.
5. Signs in to the project as its deploy identity.
6. Runs `gapp ci runner deploy <name>`: the deploy `gapp deploy` runs.

Nothing about a deploy is passed to the run but the name. To build
something else, change the definition: `gapp set ref <ref>`.

`gapp ci deploy` checks the same things before it starts a run, so a
missing workflow, an unconnected project or a definition with no GitHub
repo is refused at once, with the command that fixes it.

A gapp upgrade is handled as on your machine: a run refuses to apply
one unless it was started with `--upgrade`, and refuses `--upgrade` when
none is pending.

## Which gapp runs

A runner installs the gapp version the deployment's definition names, as
the release tag `v<version>`. So two deployments in one fleet can be on
different gapp versions, and installing a new gapp on your machine
changes no deploy until you run `gapp upgrade` for a deployment.

The two workflows in the fleet repo belong to the gapp that installed
them. `gapp ci init` writes them, and running it again brings them up to
the installed gapp's. They are gapp's files: a change made by hand is
replaced by the next `gapp ci init`. To add your own trigger (a schedule,
a push), write a workflow of your own that starts `gapp-deploy.yml` with
a deployment's name.

## How a runner signs in

No key is stored anywhere. `gapp ci projects connect <project>` sets up
Workload Identity Federation:

| In the project | In the fleet repo |
|---|---|
| A Workload Identity pool and provider that trust GitHub's tokens for the fleet repo's owner | A GitHub Environment named after the project |
| The `gapp-deploy` service account, with the roles a deploy needs | Two variables in it: the provider and the service account |
| A binding that lets runs of the fleet repo, and only that repo, act as it | |

At run time GitHub issues the run a short-lived token saying which repo
it is. Google Cloud exchanges it for an hour-long credential for
`gapp-deploy`. A fork of the fleet repo cannot sign in: the binding names
the one repo.

`gapp ci projects list` shows the projects runners can deploy to, read
from the fleet repo's Environments.

### What the deploy identity may do

`gapp-deploy` can build, deploy the service, manage the deployment's
secrets and data bucket, read and write the project's gapp bucket, and
create the deployment's service account. It cannot enable
APIs, change project labels or reach another project. So:

- An extra API in a definition's `apis` must be on already. Run one
  deploy from your machine, or enable it as an Owner.
- A new custom domain needs `gapp-deploy` added as a verified owner of
  the domain in Search Console, because Cloud Run maps a domain only for
  an identity that has verified it.

## Private app repos

The run's own token can read only the fleet repo, which is enough to
clone a public app. For a private one, give the fleet repo a GitHub App
that can read it:

1. On the organization that holds the private repos, create a GitHub App:
   Repository permissions, Contents: Read-only. Webhook off. Install it
   on that organization only.
2. Note its App ID and generate a private key.
3. Store both in the fleet repo, then delete the key file:

   ```bash
   gh variable set HOSTING_GITHUB_APP_ID --repo owner/my-fleet --body <app-id>
   gh secret set HOSTING_GITHUB_APP_PRIVATE_KEY --repo owner/my-fleet < <key>.pem
   ```

For each deploy of a private repo, the run mints a token limited to that
one repo, read-only, valid for an hour. The App's install list is the
limit of what the key can ever read, so install it only where every repo
is one you would let a runner read.

## Security model

Who can do what follows from GitHub and Google Cloud permissions:

| To | You need |
|---|---|
| Start a deploy | Write access to the fleet repo |
| Change what a deployment is, including the repo it builds | To be able to commit to the fleet repo's `gapp.yaml` |
| Connect a project | To be an Owner of the project and an admin of the fleet repo |

What that means in practice:

- **Anyone who can write to the fleet repo can deploy** any repo they
  can name, at any ref, to any connected project. Google Cloud trusts
  every run from the fleet repo, on any branch, so a changed workflow
  deploys as well as gapp's. Treat write access to the fleet repo as
  deploy access to every connected project.
- **Inputs never reach a shell unquoted.** The workflows pass the
  deployment's name through the environment and read the file with it as
  data, never as part of a command.
- **A runner writes nothing to GitHub.** Its token reads the fleet repo.

Two settings on the fleet repo tighten this, and they are yours to set:

- **A required reviewer on a project's Environment** makes every deploy
  to that project wait for a named person's approval.
- **Branch protection on the default branch** makes every change to the
  fleet a pull request. gapp commits to the fleet directly, so with this
  on, fleet changes are made by pull request instead of by `gapp set`.
  Review alone protects little unless Google Cloud also trusts only the
  default branch and deploys build only merged refs; see
  [the fleet in git](FLEET-IN-GIT.md).

## Limits

- The fleet repo and the app repos are on github.com.
- A run deploys one deployment. There is no "deploy everything".
- `gapp ci status --live` starts a runner, so it takes about as long as
  one takes to start. `gapp status` is quicker where gcloud is signed in.
