# Secrets

Read this for anything about a solution's secrets: declaring them,
checking readiness before a deploy, setting or reading values.

## How secrets work

Secrets are values in GCP Secret Manager that gapp injects into the
container as environment variables at runtime: API tokens, database
credentials, shared auth keys. They are declared in `gapp.yaml` under
`env` with a `secret` mapping:

```yaml
env:
  - name: DB_PASSWORD          # env var the app reads at runtime
    secret:
      name: db-password        # Secret Manager id: <solution>-db-password
      generate: true
  - name: UPSTREAM_API_KEY
    secret:
      name: api-key
```

Every secret has a short name (`secret.name`) and a Secret Manager id
of the form `<solution>-<short-name>`, so two solutions in one project
can both declare `db-password` without colliding. Every gapp command
takes the short name and adds the prefix itself. The `name` field is
required; a bare `secret: true` is rejected by validation.

Two kinds:

- **Generated** (`generate: true`): gapp creates a strong random value
  on the first deploy if the secret does not exist. Use it for any
  value that only needs to be random and stable: shared secrets,
  signing keys, internal auth keys.
- **User-provided** (no `generate`): the value must exist in Secret
  Manager before the deploy. Use it for upstream API keys and
  third-party credentials.

## Before a deploy

Check readiness first; deploying with a missing user-provided secret
fails partway through.

```bash
gapp secrets list        # or the gapp_secret_list tool
```

- `generate: true`, any status: fine, deploy creates it.
- user-provided, status `ready`: fine.
- user-provided, missing or empty: not ready. Set it, then deploy.

Set a value with the short name; the CLI prompts for the value with
hidden input, which keeps it out of shell history and transcripts:

```bash
gapp secrets set api-key              # prompts; or --from-stdin
```

The MCP form is `gapp_secret_set(name="api-key", value=...)`. Never
ask the user to paste a secret into chat; point them at the prompt.

The list output also reports `unattached`, `conflict`, and `orphan`
states with remediation hints. Those come from gapp's label model:
secrets gapp creates carry `gapp-solution=<name>`, and an existing
unlabeled secret at the same id is never silently adopted. The repo's
`docs/SECRETS.md` explains each state and its fix.

## After a deploy

Read a secret when a post-deploy step needs it, for example to hand a
generated admin key to the user or configure a client:

```bash
gapp secrets get db-password               # hash and length only
gapp secrets get db-password --plaintext   # the value
```

The MCP form is `gapp_secret_get(name=, plaintext=)`. The default
returns a hash and length so the value does not land in the
conversation log unless it has to; ask for plaintext only when the
operation needs the value, and hand it over out of band when you can.
