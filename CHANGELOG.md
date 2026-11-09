# Changelog

Notable changes to gapp. Versions are git tags (`vX.Y.Z`); the version
source is `gapp/__init__.py`.

## 4.0.0rc5 (pre-release of 4.0.0)

Fixes found by the first real run of 4.0.0rc4:

- `gapp ci project setup` failed to mark the project `gapp=hosted`: GA
  `gcloud projects update` has no `--update-labels` flag. The mark now goes
  through the same Resource Manager read and write `gapp setup` uses,
  keeping the project's other labels.
- A gcloud failure that is not about sign-in (a command gcloud rejects, or
  an API error such as HTTP 400) no longer ends with `gcloud auth login`
  advice; it says what failed in gcloud's or the API's own words.

## 4.0.0rc4 (pre-release of 4.0.0)

A CI deploy of a 3.x deployment failed at `gapp setup` on the runner: the
v-3 to v-4 project label restamp needs a permission the deploy identity
lacks. rc4 moves every per-deployment label off the project, which is a
clean break from 3.x's project labels.

- **New label model.** A project carries at most two gapp labels:
  `gapp=hosted`, written only by an Owner (`gapp setup` from a workstation,
  once per project, or `gapp ci project setup`), and `gapp-env`. Every setup
  and deploy, in both modes, labels the deployment's own bucket (by gapp)
  and Cloud Run services (by Terraform) with `gapp-deployment=<id>` and
  `gapp-version=<major>`. A CI run never writes a project label.
- **Version guard** reads `gapp-version` from the deployment's services: a
  deployment a newer gapp deployed is refused. The 3.x floor
  (`MIN_SUPPORTED_MAJOR`) is gone.
- **Finding a deployment.** `--project` (and CI, always) skips the search.
  Otherwise gapp lists the projects marked `gapp=hosted` and asks each for
  the deployment's bucket, in parallel; `gapp status` stops at the first
  match. `gapp list` reads every hosted project's deployment buckets.
- **Clean break.** No 4.x code reads `gapp__<name>` or
  `gapp_<owner>_<name>` project labels. Move a 3.x fleet once with the
  "Migration 3.x → 4.x" steps in CONTRIBUTING.md: mark each project hosted,
  redeploy each deployment with its project, then set each old label to
  `v-4` as a tripwire for any leftover 3.x install.
- The fleet's deploy workflow passes the project from the fleet entry to
  gapp's reusable workflow, so `gapp setup` and `gapp deploy` run with
  `--project` on the runner. Re-run `gapp ci init <fleet-repo>`.
- **Owner namespaces removed.** `gapp config owner`, `gapp list --all`,
  `gapp setup --force` and setup's cross-owner check are gone, as are the
  `app_owner` argument of `gapp_user`, `all_owners` of `gapp_list` and
  `force` of `gapp_setup`. A deployment's name is unique within its project;
  teams separate by project and IAM, or by fleet. An `owner:` key in the
  user config is now an error that names the key and the file: delete it.
- **Unknown keys are refused, not ignored.** An unknown key in the user
  config (top level or in a profile) is an error naming the file, the key
  and the keys gapp knows. `gapp ci setup` (before dispatching) and the
  register run check an entry's gapp.yaml keys with the gapp.yaml
  validator, and `gapp ci status <name>` names an entry that holds an
  unknown key. `gapp status` still answers for a gapp.yaml with unknown
  fields, but returns a warning naming them (a new `warnings` list in its
  result, printed one line each).
- On a CI runner, a refused Google Cloud call no longer suggests
  `gcloud auth login`; it says signing in does not apply and names
  `gapp ci project setup <project>` as the fix.

## 4.0.0rc3 (pre-release of 4.0.0)

Fixes found by the first real run of 4.0.0rc2:

- The status workflow and gapp's reusable deploy workflow install gcloud's
  beta component. `ci status --live` for a deployment with a custom domain
  failed with "You do not currently have this command group installed":
  the domain mapping of a fully managed Cloud Run service is described with
  `gcloud beta run domain-mappings` (the GA group covers Cloud Run for
  Anthos only). Re-run `gapp ci init <fleet-repo>` to update the installed
  status workflow.
- A deploy refuses when it cannot read the Terraform state. The live-domain
  guard took an unreadable state for "no domain mapped", so a deploy could
  have removed or replaced a serving domain; it now stops before any build
  or apply and names the state bucket, even with `--replace-domain`.

## 4.0.0rc2 (pre-release of 4.0.0)

Fixes found by the first real run of 4.0.0rc1:

- Every `gapp ci` GitHub call is now a REST call through `gh api`.
  Dispatching a workflow from a Claude Code cloud session failed because
  `gh workflow run` resolves the default branch through GraphQL, which
  such sessions refuse; the dispatch now reads the default branch and
  posts to the workflow's `dispatches` endpoint. Run logs, run watching,
  the fleet repo search, repo creation and the topic go through REST too.
- A refused dispatch or write suggests `gh auth login` only when GitHub
  refused the login itself (401, not signed in); any other refusal names
  the access needed instead.
- The status workflow installs Terraform before `gapp status`, which
  reads the deployment's Terraform outputs (`ci status --live` failed with
  "No such file or directory: 'terraform'"). Re-run `gapp ci init
  <fleet-repo>` to update the installed workflow.
- `ci status --live` and `ci setup` read only the lines a step printed,
  never the step's script that GitHub echoes into the log, so a failure
  no longer quotes unexpanded templates such as `$DEPLOYMENT`.
- `ci deploy`, `ci status --live` and `ci setup` wait for the run their
  own dispatch created. They reported the result of the workflow's
  previous run when the new one was not listed yet. Each dispatch now
  passes a `request_id` input that the workflows put at the end of the
  run name; gapp accepts only a run carrying that id and created after the
  dispatch, and says plainly when none appears in time. Re-run `gapp ci
  init <fleet-repo>` to update the installed workflows; until then a
  dispatch is refused with "Unexpected inputs" and that hint.

## 4.0.0rc1 (pre-release of 4.0.0)

gapp now has two fleet systems, kept apart. The root commands (`gapp
setup`, `deploy`, `status`, `list`, `secrets`) deploy the local checkout
to GCP with gcloud, as before. The `gapp ci` commands manage a fleet
through GitHub only: they never use gcloud (except `ci project setup`)
and never read the working directory. The fleet is kept in a private
**fleet repo**: `gapp/deployments.yaml`, one entry per deployment, plus
gapp's two workflows. The `ci` group mirrors the root verbs (`setup`,
`deploy`, `status`, `list`).

Breaking changes:

- The unit of the CI system is a **deployment**: one repo deployed to one
  project under a name you choose. `gapp ci setup [<name>] --repo
  <owner>/<repo> --project <project> [--domain <d>] [--config <file>]`
  adds or updates one (the name defaults to the repo's name); `gapp ci
  deploy <name>`, `gapp ci status <name>` and `gapp ci list [--repo
  <owner>/<repo>]` take names. No `ci` command reads the working
  directory, so `--solution` and `--solution-repo` are gone.
- The fleet file is `gapp/deployments.yaml` (a mapping of names to
  entries) instead of `gapp/solutions.yaml` (a list). An entry's own
  fields are `id`, `repo` and `project`; every other key is a gapp.yaml
  key the deploy run merges over the repo's `gapp.yaml`, or writes as
  the whole file when the repo has none. `ci init` removes an empty
  `gapp/solutions.yaml` left by 3.9.0 and reports a non-empty one;
  existing apps are set up again under their current names, which keeps
  their live resources.
- `gapp ci init` is the only command that sets the fleet repo, in gapp's
  local config; no other command looks it up by topic any more. Without
  a repo it shows the saved one or finds the one tagged `gapp-ci` under
  your account (`--owner <org>` for an organization); `gapp ci init
  <owner>/<repo>` sets an existing repo and `--create` makes a new
  private one. `--local-only` is gone.
- `gapp ci setup` never uses gcloud and no longer bootstraps a project.
  The one-time per-project work (Workload Identity pool, provider,
  `gapp-deploy` service account, binding, the project's Environment)
  moved to `gapp ci project setup <project>`, the only `ci` command that
  touches GCP. A project the fleet is not yet trusted for stops with the
  one-time message.
- `gapp ci trigger` is renamed `gapp ci deploy` (the MCP tool
  `gapp_ci_trigger` likewise becomes `gapp_ci_deploy`); both old names
  remain for this one release as hidden aliases. A deployment with no
  entry is not deployed; the outcome names `gapp ci setup`.
- The fleet repo variable `GAPP_ALLOWED_OWNERS` is renamed
  `GAPP_ALLOWED_SOURCES`, with no alias, and is managed by `gapp ci
  sources add|remove|list <owner>`.
- The reusable deploy workflow takes an `overlay` input (the entry's
  gapp.yaml keys as JSON) applied by `gapp ci runner overlay`; the
  one-key `domain` input is kept for older callers. The installed deploy
  workflow's input is `deployment` (was `solution`); its runs are named
  `Deploy <name> (<ref>)`.
- Result keys: `fleet_repo` replaces `ci_repo` and `repo` in every
  `gapp ci` result and MCP tool.

Also:

- `gapp ci setup <new> --copy-of <existing> [--project <p>]` starts a new
  deployment from an existing one (same repo and gapp.yaml keys; its own
  id; no domain, history or secret values), which is how environments
  and multiple instances work. `gapp ci rename <old> <new>` changes the
  name only. `id` (what the GCP resources are keyed by) defaults to the
  name, takes the next free variant on a project, is at most 27
  characters, and shows only with `--verbose`.
- Output leads with the deployment and what happened, then
  `Next: <exact command>`; the fleet repo, files, topic, variables and
  id appear only with `--verbose` or when they are the problem. `gapp ci
  --help` and the README tell the story from nothing to first deploy.
  `--json` on every `ci` command prints the result dict (the same one
  the MCP tool returns) and nothing else.
- `gapp ci status <name>` is GitHub only and fast; `--live` dispatches a
  new read-only status workflow (`gapp-status.yml`, installed by `ci
  init`) that runs `gapp status` for the deployment on a runner with the
  project's deploy identity and returns the service state. Nothing is
  stored in the fleet repo. `gapp status` and `gapp_status` take an
  explicit `--project`.
- Every failure is a plain sentence with the exact fix. Something is
  reported missing only when GitHub confirms it; otherwise "Couldn't
  check <thing>: GitHub returned <status>: <message>" with the request
  that failed, and a 404 names both readings (it doesn't exist, or your
  login can't see it).
- `gapp status` reports the direct system only and never crashes: with
  no usable Google Cloud identity it says so and names the login command;
  without a `gapp.yaml` it says what to run.
- `scripts/release.py` accepts release candidates (`X.Y.ZrcN`, PEP 440),
  which tag as `vX.Y.ZrcN` so `gapp ci init`'s pin resolves.

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
