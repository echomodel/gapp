---
name: secrets
description: Manage secrets for gapp-deployed solutions. Use when asked to check, get, set, or troubleshoot secrets in Secret Manager — "what secrets does this need", "get the db password", "set the API key", "are my secrets ready for deploy", "check secret status", etc.
disable-model-invocation: false
user-invocable: true
---

# Secrets Skill

## Overview

This skill manages secrets for gapp-deployed solutions. Secrets
are values stored in GCP Secret Manager and injected as environment
variables into the running container. Common examples: API tokens,
database credentials, shared secrets used for authentication.

## How Secrets Work in gapp

Secrets are declared in `gapp.yaml`'s `env` section with a
`secret` block:

```yaml
env:
  - name: DB_PASSWORD
    secret:
      name: db-password
      generate: true

  - name: UPSTREAM_API_KEY
    secret:
      name: api-key
```

### Naming

Every secret has a short name (`secret.name` in gapp.yaml) and
a full Secret Manager ID:

| | Example |
|---|---|
| **Short name** (what you type) | `db-password` |
| **Secret Manager ID** (what gapp creates) | `my-solution-db-password` |

The Secret Manager ID is always `{solution}-{short-name}`. This
prefixing ensures secrets from different solutions in the same
GCP project never collide.

All gapp commands accept the **short name** — gapp adds the
solution prefix automatically.

### The `name` field

The `name` field under `secret` is required. It is the short name
for the secret in Secret Manager.

```yaml
env:
  - name: DB_PASSWORD          # env var the app reads at runtime
    secret:
      name: db-password        # Secret Manager: {solution}-db-password
      generate: true
```

### Two kinds of secrets

**Auto-generated** (`generate: true`): gapp creates a strong
random value during deploy if the secret doesn't exist yet. Use
this for any secret where the value just needs to be random and
consistent — shared secrets, internal auth keys, encryption keys.

**User-provided** (no `generate`): the secret must exist in
Secret Manager before deploy. Populate it with `gapp_secret_set`
or the CLI. Use this for upstream API keys, third-party
credentials, or any externally-provided value.

## Workflow: Before Deploy

**Always check secrets before deploying.** Call `gapp_secret_list`
to see which secrets are declared and their status:

- `generate: true` + any status = OK. gapp handles it on deploy.
- `generate: false` + status `"set"` = OK. Ready to deploy.
- `generate: false` + status `"not created"` or `"empty"` = NOT
  READY. Must populate with `gapp_secret_set` before deploy.

Do NOT call `gapp_deploy` until all non-generated secrets show
status `"set"`. Deploying with missing secrets will fail.

### Populating a secret

```
gapp_secret_set(name="api-key", value="the-value")
```

Or via CLI:
```bash
gapp secrets set api-key
# prompts for value (hidden input)
```

## Workflow: After Deploy

### Retrieving a secret

Use `gapp_secret_get` to confirm a secret exists or to retrieve
its value for post-deploy operations (e.g., configuring an admin
client, verifying a credential).

**Default (safe)** — returns hash and length, no plaintext:
```
gapp_secret_get(name="db-password")
# {"name": "db-password", "secret_id": "my-solution-db-password", "hash": "a1b2c3d4...", "length": 43}
```

**With plaintext** — returns the actual value:
```
gapp_secret_get(name="db-password", plaintext=True)
# {"name": "db-password", "secret_id": "my-solution-db-password", "value": "the-actual-value"}
```

The hash-only default avoids leaking secrets into agent
conversation logs unnecessarily. Use plaintext only when you
need the actual value for an operation.

CLI equivalent:
```bash
gapp secrets get db-password              # hash + length
gapp secrets get db-password --plaintext  # shows value
gapp secrets get db-password --raw        # just the value, for piping
```

## MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `gapp_secret_list` | Check all secrets and their deploy-readiness |
| `gapp_secret_get` | Get a secret (hash by default, plaintext opt-in) |
| `gapp_secret_set` | Store a secret value before deploy |

## Important Notes

- Secret names are scoped per-solution. Two solutions can both
  declare `name: db-password` without collision.
- `gapp_secret_get` returns hash + length by default. Use
  `plaintext=True` only when you need the actual value.
- Always call `gapp_secret_list` before `gapp_deploy` to confirm
  all non-generated secrets are populated.
- Secrets with `generate: true` are created automatically during
  deploy — you never need to set them manually.
