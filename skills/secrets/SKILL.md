---
name: secrets
description: Manage secrets for gapp-deployed solutions. Use when asked to check, read, set, or troubleshoot secrets in Secret Manager — "what secrets does this need", "get the signing key", "set the API key", "are my secrets ready for deploy", "why can't I connect to the admin CLI", etc.
disable-model-invocation: false
user-invocable: true
---

# Secrets Skill

## Overview

This skill manages secrets for gapp-deployed solutions. Secrets
are values stored in GCP Secret Manager and injected as environment
variables into the running container. Common examples: signing
keys, upstream API tokens, database credentials.

## How Secrets Work in gapp

Secrets are declared in `gapp.yaml`'s `env` section with a
`secret` block:

```yaml
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key
      generate: true

  - name: THIRD_PARTY_API_KEY
    secret:
      name: api-key
```

### Naming

Every secret has two names:

| Name | Where it appears | Example |
|------|-----------------|---------|
| **Env var name** | gapp.yaml `name` field, app code | `SIGNING_KEY` |
| **Secret Manager ID** | GCP Secret Manager | `my-solution-signing-key` |

The Secret Manager ID is always `{solution}-{secret.name}`. This
prefixing ensures secrets from different solutions in the same GCP
project never collide.

All gapp commands accept the **env var name** — gapp resolves the
Secret Manager ID automatically. You never need to know or type
the full Secret Manager ID.

### Two kinds of secrets

**Auto-generated** (`generate: true`): gapp creates a strong
random value during deploy if the secret doesn't exist yet. Use
this for signing keys and any secret where the value just needs
to be random and consistent.

**User-provided** (no `generate`): the secret must exist in
Secret Manager before deploy. The user populates it with
`gapp_secret_set`. Use this for upstream API keys, third-party
credentials, or any value the user provides.

### The `name` field

The `name` field under `secret` is required. It specifies the
short name used in Secret Manager (before the solution prefix).

```yaml
env:
  - name: SIGNING_KEY
    secret:
      name: signing-key       # Secret Manager ID: {solution}-signing-key
      generate: true
```

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
gapp_secret_set(env_var_name="THIRD_PARTY_API_KEY", value="the-value")
```

Or via CLI:
```bash
gapp secrets set THIRD_PARTY_API_KEY
# prompts for value (hidden input)
```

## Workflow: After Deploy

### Retrieving a secret value

Use `gapp_secret_get` to confirm a secret exists or to retrieve
its value for admin operations (e.g., connecting the mcp-app
admin CLI).

**Default (safe)** — returns hash and length, no plaintext:
```
gapp_secret_get(env_var_name="SIGNING_KEY")
# {"name": "SIGNING_KEY", "secret_id": "my-solution-signing-key", "hash": "a1b2c3d4...", "length": 43}
```

**With plaintext** — returns the actual value:
```
gapp_secret_get(env_var_name="SIGNING_KEY", plaintext=True)
# {"name": "SIGNING_KEY", "secret_id": "my-solution-signing-key", "value": "the-actual-value"}
```

Use plaintext when you need the value for something — e.g., to
pass to `mcp-app set-base-url --signing-key` for admin client
setup. The hash-only default avoids leaking secrets into agent
conversation logs unnecessarily.

CLI equivalent:
```bash
gapp secrets get SIGNING_KEY              # hash + length
gapp secrets get SIGNING_KEY --plaintext  # shows value
gapp secrets get SIGNING_KEY --raw        # just the value, for piping
```

### Connecting the mcp-app admin CLI

For mcp-app solutions, the signing key is needed to configure the
admin client for user management. This is the bridge between
deployment and user management:

```bash
gapp secrets get SIGNING_KEY --raw | \
  my-solution-admin connect \
    "$(gapp status --url)" \
    --signing-key-stdin
```

Or via MCP tools:
1. `gapp_secret_get(env_var_name="SIGNING_KEY", plaintext=True)`
2. Use the returned value with the admin CLI or MCP admin tools

## MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `gapp_secret_list` | Check all secrets and their deploy-readiness |
| `gapp_secret_get` | Get a secret (hash by default, plaintext opt-in) |
| `gapp_secret_set` | Store a secret value before deploy |

## Important Notes

- Secret names are scoped per-solution. Two solutions can both
  declare `name: signing-key` without collision.
- `gapp_secret_get` returns hash + length by default. Use
  `plaintext=True` only when you need the actual value.
- Always call `gapp_secret_list` before `gapp_deploy` to confirm
  all non-generated secrets are populated.
- Secrets with `generate: true` are created automatically during
  deploy — you never need to set them manually.
