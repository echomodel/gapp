# Changelog

Notable changes to gapp. Versions are git tags (`vX.Y.Z`); the version
source is `gapp/__init__.py`.

## 4.0.0rc1 (pre-release of 4.0.0)

gapp now has two fleet systems with a rigid line between them. The root
commands (`gapp setup`, `deploy`, `status`, `secrets`, ...) are the direct
system: they need a Google Cloud identity and their record is the GCP
project label. The `gapp ci` commands are the CI system: they need GitHub
and their record is `gapp/solutions.yaml` in the CI repo plus the project's
GitHub Environment. No command consults the other system's state, and
every command's requirements are fixed by its name.

Breaking changes:

- `gapp ci setup` never uses gcloud and no longer bootstraps a project.
  The one-time per-project work (Workload Identity pool, provider,
  `gapp-deploy` service account, binding, the project's Environment) moved
  to the new `gapp ci project setup <project>`, the only `ci` command that
  touches GCP. `--project` is required on `ci setup`; a project the CI
  repo is not yet trusted for stops with the one-time message.
- `gapp ci trigger` is renamed `gapp ci deploy` (the MCP tool
  `gapp_ci_trigger` likewise becomes `gapp_ci_deploy`). `ci trigger` and
  `gapp_ci_trigger` remain for this one release as hidden aliases that
  name the new command. A solution with no entry in `gapp/solutions.yaml`
  is no longer deployed through a legacy per-solution workflow; `ci
  status` lists such solutions to register.
- The CI repo variable `GAPP_ALLOWED_OWNERS` is renamed
  `GAPP_ALLOWED_SOURCES`, with no alias, and is managed by the new
  `gapp ci sources add|remove|list <owner>`. The register workflow reads
  the new name; re-run `gapp ci init` to update the workflows.

Also:

- Every `gapp ci` command returns a plain outcome with a next step instead
  of failing: `ok` and `next_step` in every result (CLI exits 1 and prints
  the step; MCP tools return the same dict). No stack traces, raw HTTP
  errors or raw gcloud errors reach the user. The register workflow
  reports every outcome as a structured result (`result=<code>`) that
  `ci setup` prints verbatim; the deploy workflow names a missing
  Environment, Environment variable or App credential in words on its
  first step.
- `gapp status` reports the direct system only and never crashes: with no
  usable Google Cloud identity it says so and names the login command;
  without a `gapp.yaml` it says what to run.
- `gapp ci status` is the CI system's complete report: the three gapp
  files (present and current), the three variables, every registered
  solution with project, domain, gapp ref and Environment, legacy
  per-solution workflows, and the next step.

## 3.9.0

- The CI repo holds one generic deploy workflow
  (`.github/workflows/gapp-deploy.yml`), one register workflow
  (`.github/workflows/gapp-register.yml`) and the data file
  `gapp/solutions.yaml`, all gapp-owned. `gapp ci init` installs them
  through the GitHub API and updates the workflows when a new gapp release
  changes them; the data file is created once and never overwritten. It
  also reports whether the repo holds `GAPP_ALLOWED_OWNERS`,
  `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY`, with the
  `gh` commands to set them; it never sets them.
- `gapp ci setup --project <id> [--domain <d>]` registers a solution by
  dispatching the register workflow and reporting the run's result
  (`--no-watch` / `watch=False` returns after the dispatch). The run checks
  the project's Environment, the owner allowlist, the repo's reachability
  and that no other project holds the solution, then writes the entry
  with its own token, SHA-checked. The machine running `ci setup` needs
  neither gcloud nor a GitHub API write. Without `--project` the project
  is found by label when gcloud is present; otherwise the command stops
  and names the flag. `--out` is gone: nothing is rendered per solution
  any more.
- The first `gapp ci setup` on a project still needs gcloud: it creates
  the Workload Identity pool, provider, deploy service account and binding
  and records them in the CI repo as a GitHub Environment named after the
  project id (`GAPP_WIF_PROVIDER`, `GAPP_DEPLOY_SERVICE_ACCOUNT`). The
  deploy job runs in that Environment; the reusable deploy workflow gains
  an `environment` input and reads the two variables from it. This never
  runs from a machine without gcloud.
- `gapp ci trigger` dispatches the generic deploy workflow with the
  solution's name; a solution that still has its own per-solution
  workflow is dispatched through that file, with a note to register it.
- `gapp ci status` lists the registered solutions with project, domain
  and whether each project's Environment exists, the three variables, the
  gapp files, and solutions still on their own workflow; it names the gap.
- The hosting GitHub App's credentials have fixed names in the CI repo.
  The `--app-id-var` and `--app-key-secret` options of `gapp ci setup`
  (and the matching MCP parameters) are removed.
- Every gcloud call honours `gapp config account`: the provider, `gapp ci`
  and the secrets commands all run gcloud as the configured account, and
  the gcloud path of `gapp ci setup` stops by name when that account has
  no login instead of falling back to gcloud's active account.
- Checks a machine cannot perform (the Actions variables and secrets API
  behind a restrictive proxy) are reported as not checked rather than
  failed; the register and deploy runs perform them.

## 3.8.0

- CI deploys of private solution repos: the reusable workflow clones with a
  one-hour, read-only token minted from a GitHub App the operator installs;
  `gapp ci setup` detects a private repo and wires the app inputs.
- `gapp ci setup --domain` writes the custom domain into the workflow and
  keeps it on re-runs.
- `gapp deploy` refuses, before any build, to replace or remove a live
  domain mapping without `--replace-domain`, and to map a domain the
  deploying identity has not verified.
- `gapp status` reports the custom domain's state.
- Project label reads, project listing and token fetches raise with the
  cloud's own message instead of returning empty.
