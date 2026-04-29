# Secrets

gapp manages secrets for a deployed solution by:

1. Reading `env: [{secret: ...}]` declarations from `gapp.yaml`.
2. For each declaration, computing the GCP Secret Manager ID as
   `<solution>-<short-name>`.
3. Stamping every secret it creates with the label
   `gapp-solution=<solution>` so ownership is machine-readable.
4. Wiring the secret into the Cloud Run service as an env var.

The label is gapp's discovery mechanism. Every operation — list, set,
validate, materialize — uses a single label-filtered query
(`gcloud secrets list --filter labels.gapp-solution=<solution>`) to
enumerate what gapp owns. Secrets in the same project that lack the
label, or carry a label for a different solution, are not gapp-managed.

## Declaring secrets

```yaml
name: my-app
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true             # gapp creates + sets a 32-char value on deploy

  - name: API_TOKEN
    secret:
      name: api-token            # operator must `gapp secrets set api-token <v>`
```

Secret IDs are not configurable — they are always `<solution>-<short-name>`.
Operators set the short name; gapp owns the prefix.

## Commands

| Surface | Command | Behavior |
|---|---|---|
| CLI | `gapp secrets list` | Show declared secrets, status, and remediation hints. |
| CLI | `gapp secrets get NAME` | Fetch a secret. Default: hash + length. `--plaintext` prints the value. |
| CLI | `gapp secrets set NAME [VALUE]` | Set a secret. Without VALUE, prompts (or use `--from-stdin`). |
| MCP | `gapp_secret_list` | Same as CLI list, returns structured JSON. |
| MCP | `gapp_secret_get` | Same as CLI get. |
| MCP | `gapp_secret_set` | Same as CLI set. |

CLI and MCP are thin wrappers over a single SDK module
(`gapp.admin.sdk.secrets`). All logic — discovery, classification,
validation, materialization — lives in the SDK so behavior is
identical regardless of surface.

## Status values

`gapp secrets list` classifies each declared secret into one of:

| Status | Meaning |
|---|---|
| `ready` | Declared, present in GCP, labeled for this solution. |
| `missing` | Declared, not present in GCP. Operator must run `gapp secrets set`. |
| `missing-generate` | Declared with `generate: true`, not yet present. `gapp deploy` will create it. |
| `unattached` | Declared, a secret with the expected ID exists in GCP, but it has no `gapp-solution` label. |
| `conflict` | Declared, a secret with the expected ID exists in GCP labeled for a different solution. |
| `no-project` | Solution exists locally but no GCP project is resolved yet. Run `gapp setup`. |

Plus a separate `orphans` list: secrets in GCP labeled for this solution
that have no matching declaration in `gapp.yaml`.

## Exception Scenario Recovery and Conflict Resolution

These are the cases where a deploy can fail or `gapp secrets list`
shows something other than `ready`. They are not expected in a
solution that has been managed exclusively by gapp v3+ from the
start. They typically arise from one of:

- A solution last deployed under an older gapp version that did not
  apply the `gapp-solution` label.
- A secret created manually with `gcloud secrets create` outside gapp.
- A solution rename without a corresponding cleanup of secrets in the
  old name's namespace.
- A `gapp.yaml` declaration that was deleted without removing the
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
gapp secrets set api-token <value>
```

Or, if the manifest declares `generate: true`, simply run `gapp deploy`
— the deploy pipeline materializes generated secrets automatically.

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

    Option: Use a different secret name in this solution's gapp.yaml (rename 'api-token')
      $ (edit gapp.yaml; gapp constructs the secret_id as <solution>-<name>)

    Option: Re-label for 'my-app' if 'other-app' is gone (manual takeover)
      $ gcloud secrets update my-app-api-token \
          --update-labels=gapp-solution=my-app --project=my-project
```

The expected secret ID exists, but its `gapp-solution` label points
at a different solution. This is a name collision at the
`<solution>-<short-name>` level, typically caused by two solutions in
the same project happening to land on the same computed ID.

Two paths forward:

**Rename in `gapp.yaml`.** Cleanest answer if both solutions are
active. Pick a different short name; gapp will compute a different
secret ID and the collision goes away:

```yaml
env:
  - name: API_TOKEN
    secret:
      name: my-app-api-token   # was just `api-token`; now namespaced
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
  Orphans (labeled in GCP but not declared in gapp.yaml):
    - my-app-old-key

Resolution options

[1] my-app-old-key — orphan
    Secret 'my-app-old-key' is labeled for solution 'my-app' but no
    matching declaration exists in gapp.yaml. It is not consumed by
    any deployed env var.

    Option: Delete it (recommended if no longer needed)
      $ gcloud secrets delete my-app-old-key --project=my-project

    Option: Re-add the declaration to gapp.yaml under env: with a matching name
      $ (edit gapp.yaml)
```

This is a leftover. The yaml declaration that produced this secret
was deleted at some point but the secret itself wasn't cleaned up.
It's labeled gapp-managed, so gapp tracks it; it just has nothing to
do.

Two paths forward:

**Delete.** Almost always the right answer. Confirms the value is
genuinely no longer needed:

```
gcloud secrets delete my-app-old-key --project=my-project
```

**Re-add.** If the declaration was deleted by mistake, re-add it
under `env:` with a `secret.name` of `old-key`. The next deploy will
re-wire the existing value into the Cloud Run service.

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
