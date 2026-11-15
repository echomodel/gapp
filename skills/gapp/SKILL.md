---
name: gapp
description: "Deploy, redeploy, set up and operate apps that serve HTTP (web servers, APIs, MCP servers) on Google Cloud Run with gapp, in any language that runs in a container, from any machine: a workstation, a CI runner or a cloud agent session. Use when asked to deploy, host, ship, publish or redeploy an app to Google Cloud or Cloud Run, push changes to production, set up CI/CD or GitHub Actions deploys, deploy the same app to several projects, or check a deployed app's status or URL, add a custom domain, change its settings, or manage its secrets when the app runs on Cloud Run or is a deployment in a gapp fleet. Also use when the machine lacks the gapp CLI, a Google Cloud login or terraform, and for any question about hosting an app on Google Cloud, even if gapp is not named."
compatibility: Needs the gapp CLI (or its MCP tools). gcloud for project setup, secrets, status and a deploy from this machine; terraform for that deploy; the GitHub CLI for deploys on GitHub Actions. The skill detects what is missing and routes around it.
---

# gapp

gapp deploys apps that serve HTTP to Cloud Run with Terraform. A
deployment is defined once, in the user's **fleet**, and every command
works from that definition. The app's repo stays cloud-agnostic: it
needs a Procfile or a Dockerfile, optionally an `app.json` listing its
variables, and nothing that names gapp. gapp
handles the container build, Secret Manager, a data volume, the domain
and access.

| Word | Meaning |
|---|---|
| Fleet | The record of the user's deployments: one file, on their machine or in a git repo |
| Definition | One deployment's entry: its name, repo, project, the ref a deploy builds, and its settings |
| Deployment | The running service with its data and secrets |

## Invariants

- **Everything comes from the definition.** A deploy, a status and a
  secret all take the project, the id and the settings from it. Nothing
  searches the cloud, and `gapp.yaml` in an app's repo is not read.
  The app's `app.json` only fills in defaults, at deploy; the definition
  always wins (`references/settings.md`).
- **Name the deployment with `deployment`** (`--deployment <name>`), or
  run inside a clone of its repo, where gapp finds it by the clone's
  remote. A folder's name is never used.
- **A refusal changes nothing.** The CLI exits 2 and a tool returns
  `{"refused": true, "message": ...}`. The message names the command
  that fixes it; relay it, do not work around it.
- **Never hand-edit the fleet file.** Change a definition with `gapp
  set`, `gapp env set`, `gapp apis` or `gapp apply`.
- **What a deploy builds is the definition's ref.** No deploy takes a
  ref. To release, roll back or try a branch: `gapp set ref <ref>`,
  then deploy. From a clone, the clone must be on that ref.
- **The build ships `git archive HEAD`**, so uncommitted changes are
  not deployed. Images are tagged by commit; an image that exists is
  not built again.
- **Secret values live in Secret Manager only.** The fleet holds names.
  No tool takes a secret's value: the user sets one from a terminal
  with `gapp secrets set <secret-name>`. Never put a credential in
  chat, a repo, the fleet or a plain variable.
- **gapp upgrades are explicit.** A definition records its gapp
  version. `gapp upgrade` then a deploy with `--upgrade`; never pass
  `--upgrade` unless `gapp show` or a dry run says one is pending.
- **gapp does not do auth, users or tokens.** That is the app's own
  framework's job.
- **One deployment is one Cloud Run service.**
- Never run an interactive login the user cannot complete from this
  machine. Guide step by step: find where the user is, do the next
  step, stop.

## Where to read next

| When you need | Read |
|---|---|
| To decide whether this machine can do the step, install what is missing, or hand off a login | `references/environment.md` |
| To judge whether a repo fits, make it startable, choose its settings, make an MCP server cloud-ready | `references/settings.md` |
| The steps in detail: define, prepare the project, secrets, deploy, verify, domain, CI, upgrades | `references/lifecycle.md` |
| Secrets: naming them, readiness before a deploy, setting and reading values | `references/secrets.md` |
| The CLI command and MCP tool for each operation | `references/tools.md` |

The repo's `docs/INTERFACE.md` is the full reference for every command
and for the fleet file.

## Two tool surfaces, one SDK

- the `gapp` CLI, wherever gapp is installed; every command takes
  `--json`
- the MCP tools named `gapp_*`, when `gapp-mcp` is registered

They return the same result. Prefer the tools when both are present.
Use only commands and tools that exist: `references/tools.md` is the
complete list.

## Step 0: probe before you plan

Decide by what this machine has, not by guessing where you are.

| Capability | How to find out | Needed by |
|---|---|---|
| gapp | `gapp_*` tools present, or `gapp --version`. Missing: install it, pinned to the release, without asking | everything |
| The fleet | `gapp fleet` (where it lives, how many deployments) and `gapp list` | everything |
| This deployment | `gapp show` in the clone, or `gapp show --deployment <name>` | which step is next |
| Google Cloud login | an active account in `gcloud auth list`, or application default credentials | project setup, secrets, status, a deploy from here |
| terraform | `terraform version` | a deploy from here, status |
| GitHub | `gh auth status` | `gapp ci` |
| A browser login is possible | a hint, not a verdict: `CLAUDE_CODE_REMOTE`, `CI` or `CODESPACES` set usually means no; when unsure, ask | preparing this machine or handing off |

Then match what the next step needs against what you found
(`references/environment.md`). Only a login ever needs the user; tools
never do.

## Which step is next

| What you find | Next |
|---|---|
| The repo has no Dockerfile and no Procfile | Make it startable (`references/settings.md`) |
| `gapp show` says the repo has no deployment | Define it: `gapp add` |
| The project is not set up (a deploy or dry run says so) | `gapp project setup <project>`, as an Owner |
| `gapp secrets list` shows a secret that is not ready | Have the user set it |
| Defined, never deployed (`gapp status`) | Deploy |
| Deployed | Ongoing operations: redeploy, settings, domain, upgrade |

`gapp deploy --dry-run` says what a deploy would do and whether an
upgrade is pending, and changes nothing. Use it before a first deploy.

## Two ways to deploy

| | From this machine | On a runner |
|---|---|---|
| Command | `gapp deploy` | `gapp ci deploy` |
| Needs here | gcloud, terraform, a clone on the definition's ref | `gh` only |
| One-time setup | `gapp project setup <project>` | that, plus the fleet in a GitHub repo, `gapp ci init`, `gapp ci projects connect <project>` |

Both run the same deploy from the same definition. Recommend runners
to anyone who wants to deploy from more than one machine or from an
agent session.

## Handing off when this machine cannot do a step

Do everything that does not need the missing thing first, then give
the user a handoff they can act on without working anything out: which
machine, the exact commands in order, and what to report back. The
template is in `references/environment.md`. The one-time Owner steps
(`gapp project setup`, `gapp ci projects connect`) and setting a
secret's value are the usual reasons.
