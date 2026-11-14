# Secrets

A deployment's definition names the secrets its app reads. The values
live in Secret Manager, in the deployment's project, and nowhere
else: the fleet holds a secret's name, never its value.

For each variable that comes from a secret, gapp:

1. Computes the Secret Manager id as `<deployment id>-<secret-name>`.
2. Labels every secret it creates `gapp-solution=<deployment id>`, so
   ownership can be read from the secret itself.
3. Wires the secret into the Cloud Run service as that variable.

The label is how gapp finds its secrets. Every operation uses one
label-filtered query to list what the deployment owns. A secret in the
same project without the label, or labelled for another deployment, is
not the deployment's.

## Declaring secrets

```bash
gapp env set API_TOKEN --secret
gapp env set APP_KEY --secret --generate
gapp env set DB_URL --secret database-url
```

| Command | The variable reads | Its value |
|---|---|---|
| `--secret` | The secret named after the variable: `API_TOKEN` reads `api-token` | You set it: `gapp secrets set api-token` |
| `--secret <secret-name>` | That secret | You set it |
| `--secret --generate` | The secret named after the variable | gapp makes a 32-character value at the first deploy, and never again |

In the fleet file that is:

```yaml
settings:
  env:
    API_TOKEN: {secret: api-token}
    APP_KEY: {secret: app-key, generate: true}
```

The id in Secret Manager is always `<deployment id>-<secret-name>`. You
choose the name; gapp owns the prefix.

## Commands

| Command | Does |
|---|---|
| `gapp secrets list` | The secrets the definition names, the state of each, and what to do about any that is not ready |
| `gapp secrets get <secret-name>` | Whether it is set, with its length and a hash. `--plaintext` prints the value |
| `gapp secrets set <secret-name>` | Stores a value, typed at a prompt, or read from stdin with `--from-stdin` |

Each takes `--deployment <name>`, or inside a clone works on the clone's
deployment. They need gcloud. The value goes from your machine to Secret
Manager; no runner is involved and nothing is written to the fleet.

A secret the definition does not name is refused, with the ones it does
name listed.

The MCP tools `gapp_secrets_list` and `gapp_secrets_get` do the same. No
tool takes a secret's value: set one from a terminal.

## Status values

`gapp secrets list` classifies each declared secret into one of:

| Status | Meaning |
|---|---|
| `ready` | Declared, present in GCP, labeled for this solution. |
| `missing` | Declared, not present in GCP. Operator must run `gapp secrets set`. |
| `missing-generate` | Declared with `generate: true`, not yet present. `gapp deploy` will create it. |
| `unattached` | Declared, a secret with the expected ID exists in GCP, but it has no `gapp-solution` label. |
| `conflict` | Declared, a secret with the expected ID exists in GCP labeled for a different solution. |

Plus a separate `orphans` list: secrets in GCP labeled for this solution
that the definition does not name.

## Worked example: declared vs. labeled in GCP

A combined walkthrough that exercises every status in one shot. The
deployment `my-app` names three secrets in its definition. The same GCP
project hosts three secrets, two of them labeled for this solution.

### The definition's settings

```yaml
settings:
  env:
    APP_KEY: {secret: app-key}
    API_TOKEN: {secret: api-token}
    DB_PASSWORD: {secret: db-password}
```

### State of GCP Secret Manager

| Secret ID | `gapp-solution` label |
|---|---|
| `my-app-app-key` | `my-app` |
| `my-app-api-token` | _(none)_ |
| `my-app-stale-key` | `my-app` |

So: 3 named in the definition, 3 in GCP. Of the GCP set, 2 carry the
gapp label. One labeled secret matches a named one (`app-key`); the
other labeled secret is named nowhere (`stale-key`). The unlabeled GCP
secret happens to share the conventional ID for `api-token`. The third
(`db-password`) has no GCP counterpart at all.

### What `gapp secrets list` produces

```
App:     my-app
Project: my-project

  Secret               Env Var                   Status             Generate
  ----------------------------------------------------------------------
  app-key              APP_KEY                   ready              no
  api-token            API_TOKEN                 unattached         no
  db-password          DB_PASSWORD               missing            no

  Orphans (labeled in GCP but not declared in the definition):
    - my-app-stale-key

========================================================================
Resolution options
========================================================================

[1] my-app-api-token — unattached
    Secret 'my-app-api-token' exists in project 'my-project' but has
    no `gapp-solution` label. gapp will not modify it until ownership
    is established.

    Option: Adopt for solution 'my-app' (gapp manages it going forward)
      $ gcloud secrets update my-app-api-token \
          --update-labels=gapp-solution=my-app --project=my-project

    Option: Delete and let gapp recreate it on next deploy or `gapp secrets set`
      $ gcloud secrets delete my-app-api-token --project=my-project

[2] my-app-stale-key — orphan
    Secret 'my-app-stale-key' is labeled for solution 'my-app' but
    'my-app' no longer declares it. No deployed env var reads it.

    Option: Delete it (recommended if no longer needed)
      $ gcloud secrets delete my-app-stale-key --project=my-project

    Option: Declare it again, as an env var read from it
      $ gapp env set <ENV_VAR> --secret stale-key
```

### Reading the output

| What `secrets list` saw | What it printed |
|---|---|
| `app-key` declared, labeled match in GCP | row in `secrets[]`, `status: ready` |
| `api-token` declared, unlabeled GCP secret at the conventional ID | row in `secrets[]`, `status: unattached`, plus a hint with adopt-or-delete options |
| `db-password` declared, no GCP secret at all | row in `secrets[]`, `status: missing`, no hint (the resolution is `gapp secrets set`) |
| `my-app-stale-key` labeled for solution but not in the definition | listed in `orphans[]`, plus a hint with delete-or-re-declare options |

The unlabeled `my-app-api-token` is **not** listed as its own line in
`orphans[]` — by design, it is considered untracked by gapp. It only
surfaces because the definition happens to name a secret whose
conventional ID matches it.

The integrated case shows two important properties:

1. **`secrets[]` is definition-driven; `orphans[]` is GCP-driven.** Each
   row in `secrets[]` corresponds 1:1 to a variable the definition names. Each
   ID in `orphans[]` corresponds to a labeled secret with no definition
   match. The two compartments never duplicate.
2. **Hints fire only for non-trivial states.** `ready` and `missing`
   produce no hints (the action is obvious or the action is a single
   `gapp secrets set`). `unattached`, `conflict`, and `orphan` each
   emit one hint with concrete `gcloud` commands.

The per-state recovery sections below walk each scenario in
isolation, with the exact commands to resolve it.

## Exception Scenario Recovery and Conflict Resolution

These are the cases where a deploy can fail or `gapp secrets list`
shows something other than `ready`. They are not expected in a
solution that has been managed exclusively by gapp from the
start. They typically arise from one of:

- A solution last deployed under an older gapp version that did not
  apply the `gapp-solution` label.
- A secret created manually with `gcloud secrets create` outside gapp.
- A solution rename without a corresponding cleanup of secrets in the
  old name's namespace.
- A declaration that was deleted without removing the
  underlying GCP secret.

In each scenario below, the placeholder solution name is `my-app`,
the declared secret short name is `api-token`, and the project is
`my-project`. Substitute your own values.

### 1. `missing` — secret is genuinely absent

`gapp secrets list` output:

```
App:     my-app
Project: my-project

  Secret               Env Var                   Status             Generate
  ----------------------------------------------------------------------
  api-token            API_TOKEN                 missing            no
```

This is the expected state for a freshly cloned solution before any
secret has been provisioned. Resolution:

```
gapp secrets set api-token
```

Or, if the variable was set with `--generate`, run `gapp deploy`: the
deploy makes generated secrets itself.

### 2. `unattached` — secret exists but lacks the gapp-solution label

```
  Secret               Env Var                   Status             Generate
  ----------------------------------------------------------------------
  api-token            API_TOKEN                 unattached         no

Resolution options

[1] my-app-api-token — unattached
    Secret 'my-app-api-token' exists in project 'my-project' but has
    no `gapp-solution` label. gapp will not modify it until ownership
    is established.

    Option: Adopt for solution 'my-app' (gapp manages it going forward)
      $ gcloud secrets update my-app-api-token \
          --update-labels=gapp-solution=my-app --project=my-project

    Option: Delete and let gapp recreate it on next deploy or `gapp secrets set`
      $ gcloud secrets delete my-app-api-token --project=my-project
```

This means a Secret Manager entry already exists at the exact ID gapp
would compute, but the `gapp-solution` label is absent.

By design, gapp **never silently adopts a pre-existing unlabeled secret**.
The deploy and `gapp secrets set` paths refuse with an error rather
than write a new version into something gapp didn't create. The
operator decides explicitly.

Two paths forward:

**Adopt.** If the existing secret value is correct and you just want
gapp to manage it going forward, attach the label:

```
gcloud secrets update my-app-api-token \
    --update-labels=gapp-solution=my-app --project=my-project
```

After this, `gapp secrets list` will report `ready` and `gapp deploy`
will treat the secret as fully managed. The existing value is preserved.

**Delete and recreate.** If the existing value is wrong, stale, or you
no longer trust it:

```
gcloud secrets delete my-app-api-token --project=my-project
gapp secrets set api-token <new-value>
```

The recreated secret carries the `gapp-solution` label automatically.

### 3. `conflict` — secret labeled for a different solution

```
  Secret               Env Var                   Status             Generate
  ----------------------------------------------------------------------
  api-token            API_TOKEN                 conflict           no

Resolution options

[1] my-app-api-token — conflict
    Secret 'my-app-api-token' is labeled for solution 'other-app',
    not 'my-app'. gapp will not modify another solution's secret.

    Option: Give this secret another name than 'api-token' (gapp stores it as my-app-<name>)
      $ gapp env set API_TOKEN --secret <new-name>

    Option: Re-label for 'my-app' if 'other-app' is gone (manual takeover)
      $ gcloud secrets update my-app-api-token \
          --update-labels=gapp-solution=my-app --project=my-project
```

The expected secret ID exists, but its `gapp-solution` label points
at a different solution. This is a name collision at the
`<solution>-<short-name>` level, typically caused by two solutions in
the same project happening to land on the same computed ID.

Two paths forward:

**Give the secret another name.** Cleanest answer if both are active.
gapp then computes a different secret ID and the collision goes away:

```
gapp env set API_TOKEN --secret my-app-api-token
```

The secret ID becomes `my-app-my-app-api-token`. Ugly but unambiguous.
A better long-term answer is usually to put the two solutions in
different projects.

**Re-label.** Only valid if the other solution is genuinely gone and
its labeled secrets are dangling. Verify first:

```
gapp list --all                                # is `other-app` still deployed?
gcloud secrets list --filter=labels.gapp-solution=other-app --project=my-project
```

If `other-app` no longer exists and you want to take over its
namespace, re-label:

```
gcloud secrets update my-app-api-token \
    --update-labels=gapp-solution=my-app --project=my-project
```

### 4. `orphan` — labeled for this solution but not declared

```
  Orphans (labeled in GCP but not declared in the definition):
    - my-app-old-key

Resolution options

[1] my-app-old-key — orphan
    Secret 'my-app-old-key' is labeled for solution 'my-app' but
    'my-app' no longer declares it. No deployed env var reads it.

    Option: Delete it (recommended if no longer needed)
      $ gcloud secrets delete my-app-old-key --project=my-project

    Option: Declare it again, as an env var read from it
      $ gapp env set <ENV_VAR> --secret old-key
```

This is a leftover. The declaration that produced this secret
was deleted at some point but the secret itself wasn't cleaned up.
It's labeled gapp-managed, so gapp tracks it; it just has nothing to
do.

Two paths forward:

**Delete.** Almost always the right answer. Confirms the value is
genuinely no longer needed:

```
gcloud secrets delete my-app-old-key --project=my-project
```

**Re-add.** If the variable was removed by mistake, set it again:
`gapp env set <ENV_VAR> --secret old-key`. The next deploy wires the
existing value into the Cloud Run service.

## Why the strict label model

gapp could in principle adopt any unlabeled secret at the conventional
ID, or fall back to name-only matching when a label is absent. It
deliberately does not, for two reasons:

1. **No silent takeover.** A secret that exists outside gapp's
   management may have been put there by a different process, a
   colleague, or an older deploy under different conventions. Reading
   it, writing a new version, or wiring it into a Cloud Run service
   should be an explicit operator action — not a side effect of running
   `gapp deploy` against a fresh checkout on a new workstation.

2. **Diff-able state.** With a single label-filtered query, gapp can
   answer in O(1) round-trips: "what does this solution own?" Without
   the label as the source of truth, the answer would require either
   an N×describe scan of every conventionally-named secret, or
   convention-only matching that can't distinguish "gapp-created" from
   "happens to share the naming scheme."

The `unattached` and `conflict` statuses exist to surface the cases
where the label model and the real world have drifted, with concrete
remediation rather than an opaque `missing`.
