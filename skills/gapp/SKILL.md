---
name: gapp
description: Deploy, redeploy, set up, and operate Python web servers and MCP servers on Google Cloud Run with gapp, from any machine - a workstation, a CI runner, or a cloud agent session. Use when asked to deploy, host, ship, publish, or redeploy a Python service, get something running on GCP or Cloud Run, push changes to production, set up CI/CD or GitHub Actions deploys, create or configure gapp.yaml, bind projects to environments, check deployment status or a service URL, add a custom domain, manage a deployed service's secrets in Secret Manager (list, check readiness, set, get), or when the current machine lacks the gapp CLI, a Google Cloud login, or terraform and the user needs to know what to run where. Also use for any question about hosting a Python service on Google Cloud, even if gapp is not named.
compatibility: Needs the gapp admin tools (the gapp CLI, or the gapp plugin's MCP tools) plus gcloud for most steps; terraform for local deploys; GitHub access for CI deploys. The skill detects what is missing and routes around it.
---

# gapp

gapp deploys containerized Python services to Cloud Run with
Terraform. The solution stays cloud-agnostic: no GCP imports, no
framework dependencies. A small `gapp.yaml` is the only touchpoint.
gapp handles the container build, Secret Manager, data volumes,
domain mapping, and IAM.

## Invariants

These hold in every phase, so keep them in mind before reading on.

- Every gapp operation is idempotent and reports a `next_step`.
  Trust it; it is derived from live cloud state, not local memory.
- The build ships `git archive HEAD`, so uncommitted changes are
  never deployed and a dirty tree blocks the build. Images are
  tagged by commit SHA; an existing image skips the build.
- Secret values live in Secret Manager, never in the repo. Only
  names go in `gapp.yaml`.
- gapp does not do auth, users, or tokens. That is the solution's
  own framework's job; point the user at that framework's docs.
- One repo is one solution is one Cloud Run service.
- Never put a credential into chat, a file in the repo, or a Cloud
  Run env var as a workaround. Never run an interactive login the
  user cannot complete from this machine.
- Guide step by step. Assess where the user is, do the next phase,
  stop.

## Where to read next

| When you need | Read |
|---|---|
| To decide whether this machine can do the step, install what is missing, or hand off a login | `references/environment.md` |
| To write or change `gapp.yaml`, judge candidacy, make an MCP server cloud-ready | `references/manifest.md` |
| The phases in detail: init, GCP foundation, secrets, deploy, verify, custom domain, ongoing ops | `references/lifecycle.md` |
| Secrets: declaring them, readiness before a deploy, setting and reading values | `references/secrets.md` |
| The CLI command and MCP tool for each operation | `references/tools.md` |

## Two tool surfaces, one SDK

gapp admin operations come two ways and return the same structured
result, because both call the same SDK:

- the `gapp` CLI, available wherever gapp is installed
- the gapp plugin's MCP tools, named `gapp_*`, available when the
  plugin is loaded

When both are loaded, prefer the MCP tools: they return structured
results instead of text to parse. Hand off only commands that exist:
the CLI and MCP columns in `references/tools.md` are the complete
list. Triggering a CI deploy is the one operation with a third
route: any GitHub tool that can dispatch a workflow and read the run
does the same job as `gapp ci trigger`, which matters in sessions
where `gh` is not logged in but GitHub tools are.

## Step 0: probe before you plan

Decide by capability, not by guessing where you are. A cloud agent
container that someone provisioned with a service-account key and
terraform is a perfectly good deploy machine; a laptop with nothing
installed is not. Check what is actually installed, logged in, and
configured. The probes below are hints, not a script: adapt them to
the shell and tools in front of you, and skip any whose answer you
already know.

| Capability | How to find out (examples) | Needed by |
|---|---|---|
| gapp admin tools | MCP tools named `gapp_*` are present; or the `gapp` command is on PATH (`gapp --version`, `which gapp`, `pipx list`). Missing: install it, pinned to the release, without asking | every phase after init |
| GCP identity | an active account in `gcloud auth list`; or application default credentials, for example `GOOGLE_APPLICATION_CREDENTIALS` set or `gcloud auth application-default print-access-token` succeeding | setup, secrets, status, local deploy |
| terraform | `terraform version` or `which terraform` | local deploy only |
| GitHub write access | `gh auth status`; or GitHub tools in this agent that can read repos and dispatch workflows | CI wiring, CI deploys |
| Interactive login possible | a hint, not a verdict: `CLAUDE_CODE_REMOTE`, `CI`, or `CODESPACES` set usually means no browser; when unsure, ask the user | preparing this machine versus handing off |
| Solution state | `gapp.yaml` exists; then `gapp status` when an identity exists, else read the file | which phase is next |
| CI wired | `gapp ci status`, or the solution's workflow file present in the CI repo | whether deploys can go through Actions |

Then match what the next step needs against what you found. The
needs table and the three outcomes (proceed; install missing tooling
and configure, on any machine, without asking; or route an identity
step elsewhere) are in `references/environment.md`. Read it whenever
anything is missing. Only a login ever needs the user; tools never do.

## Lifecycle router

`gapp status` tells you where a solution is. Read `next_step.action`
and go to that phase; do not walk the user through phases they have
already completed.

| State | `initialized` | project | `pending` | Next |
|---|---|---|---|---|
| Not initialized | false | none | n/a | Phase 1 init |
| Initialized, no project | true | none | true | Phase 2 setup |
| Has project, not deployed | true | set | true | Phase 4 deploy (after Phase 3 if secrets are declared) |
| Deployed | true | set | false | Ongoing operations |

Phases, each detailed in `references/lifecycle.md`:

1. **Init**: confirm the repo is a fit, write a minimal `gapp.yaml`
   (`references/manifest.md`), register the solution. Needs no
   cloud identity, so it is always doable here.
2. **GCP foundation**: pick or reuse a project, optionally bind it
   to an env, run setup. Needs a project-admin identity.
3. **Secrets**: list declared secrets, set the missing ones
   (`references/secrets.md`). Needs an identity with Secret Manager
   access.
4. **Deploy**: locally (gapp, identity, terraform, clean tree) or
   through GitHub Actions once CI is wired (GitHub access only).
   Recommend CI for anyone who wants to deploy from more than one
   machine or from agents.
5. **Verify and hand over**: status, service URL, admin credential
   retrieval, custom domain if wanted.

## Handing off when this machine cannot do a step

When a step needs something this machine does not have and cannot
get, do everything that does not need it first (write and commit
`gapp.yaml`, push, draft the CI workflow), then give the user a
handoff they can act on without re-deriving anything: which machine,
the exact commands or tool calls in order, and what to report back.
The template is in `references/environment.md`. One-time steps
(setup, CI wiring) are the usual reason; after CI is wired, deploys
never need the workstation again.
