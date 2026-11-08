# Changelog

Notable changes to gapp. Versions are git tags (`vX.Y.Z`); the version
source is `gapp/__init__.py`.

## Unreleased

- `gapp ci setup` records each project's deploy identity in the CI repo as
  a GitHub Environment named after the project id (`GAPP_WIF_PROVIDER`,
  `GAPP_DEPLOY_SERVICE_ACCOUNT`), and every generated workflow deploys
  through that Environment instead of carrying the two values as literals.
  New `--project <id>` (`project=` on the MCP tool): with the Environment
  already recorded, setup makes no GCP call, so a new solution on an
  already-set-up project needs `gh` only. The reusable deploy workflow
  gains an `environment` input and reads the two variables from it.
- `gapp ci status` reports the Environment a workflow deploys through and
  whether its two variables exist; `gapp ci trigger` refuses when one is
  missing.
- The hosting GitHub App's credentials have fixed names in the CI repo:
  Actions variable `HOSTING_GITHUB_APP_ID` and secret
  `HOSTING_GITHUB_APP_PRIVATE_KEY`. The `--app-id-var` and
  `--app-key-secret` options of `gapp ci setup` (and the matching MCP
  parameters) are removed. `gapp ci init` reports whether the CI repo
  holds them and prints the two commands to store them; it never sets
  them.
- Every gcloud call honours `gapp config account`: the provider, `gapp ci`
  and the secrets commands all run gcloud as the configured account, and
  the gcloud path of `gapp ci setup` stops by name when that account has
  no login instead of falling back to gcloud's active account.

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
