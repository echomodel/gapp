---
name: user-management
description: Manage users for deployed solutions that use the app-user auth framework. Use when asked to register a user, list users, revoke access, create tokens, configure the app-user client, or test a deployed service with authentication — "register a user", "add alice to food-agent", "list users", "revoke bob", "set up user management", "test the deployed service", etc.
disable-model-invocation: false
user-invocable: true
---

# User Management Skill

## Overview

This skill manages users for deployed solutions that use the
`app-user` auth framework. It covers configuring the local admin
client, registering users, issuing tokens, revoking access, and
verifying the deployed service works end-to-end.

**Prerequisites:**
- Solution is deployed and running (use **deploy** skill first)
- Solution uses `app-user` for auth (`create_app()` in server.py)
- `SIGNING_KEY` secret exists in the deployment

## Step 1: Configure the app-user Client

The app-user CLI needs to know the service URL and signing key
for the target solution. This is a one-time setup per solution.

### If deployed with gapp

Use gapp tools to retrieve the service URL and signing key, then
pipe to app-user configure. The signing key passes via stdin and
never appears in conversation or logs:

```bash
gapp secrets get SIGNING_KEY --solution <solution-name> --raw | \
  app-user configure --name <solution-name> \
    --url "$(gapp status --solution <solution-name> --url)" \
    --signing-key-stdin
```

### If deployed without gapp

Set values manually:

```bash
app-user configure --name <solution-name> \
  --url https://my-service.run.app \
  --signing-key-stdin
```

Then paste the signing key and press Ctrl+D.

### Verify configuration

```bash
app-user profiles list
```

Should show the configured profile with the service URL.

## Step 2: Generate an Admin Token

The admin needs a token with `scope: "admin"` to call the
`/admin` endpoints. Generate one locally:

```bash
app-user admin-token --profile <solution-name>
```

This uses the locally stored signing key to create a short-lived
admin JWT. The token is printed — use it for the steps below.

## Step 3: Register the First User

```bash
app-user register --profile <solution-name> --email alice@example.com
```

This calls `POST /admin/users` on the running service. Returns:
- `email`: the registered email
- `token`: a long-lived JWT for the user
- `duration_seconds`: token lifetime

**Save the user token** — this is what the user configures in
their MCP client (Claude.ai, Claude Code, Gemini CLI).

## Step 4: Test the Deployment

### Test with curl

```bash
curl -H "Authorization: Bearer <user-token>" \
  https://my-service.run.app/mcp
```

Should get a valid MCP response (not 401/403).

### Test with MCP client

Configure the MCP client with the service URL and user token:

**Claude.ai / Claude App (remote MCP):**
- URL: `https://my-service.run.app/mcp?token=<user-token>`

**Claude Code (settings.json):**
```json
{
  "mcpServers": {
    "my-solution": {
      "url": "https://my-service.run.app/mcp",
      "headers": {
        "Authorization": "Bearer <user-token>"
      }
    }
  }
}
```

Call one of the solution's MCP tools to verify it works
end-to-end.

## Ongoing Operations

### List users

```bash
app-user users --profile <solution-name>
```

### Revoke a user

```bash
app-user revoke --profile <solution-name> --email bob@example.com
```

Existing tokens for the revoked user immediately stop working
(server checks `revoke_after` against token `iat`).

### Issue a new token for an existing user

```bash
app-user token --profile <solution-name> --email alice@example.com
```

Useful when a user loses their token or after revoking and
reactivating (new token's `iat` is after `revoke_after`).

### Custom token duration

```bash
app-user register --profile <solution-name> --email alice@example.com \
  --duration-seconds 86400
```

Default is ~10 years. Override with `--duration-seconds` or by
setting `TOKEN_DURATION_SECONDS` env var on the deployed service.

## What This Skill Does NOT Cover

- Building the solution (→ develop skill)
- Deploying the solution (→ deploy skill)
- Credential mediation for API-proxy apps (that's gapp's domain,
  uses `gapp users register` instead)

## Important Notes

- The app-user framework is a separate project (`krisrowe/app-user`).
  gapp does not depend on it as a code dependency.
- This skill references app-user's CLI and endpoints. If app-user
  changes its interface, this skill should be updated.
- Admin tokens are generated locally using the signing key. They
  never pass through the deployed service for creation.
- User tokens are long-lived because MCP clients cannot refresh
  tokens automatically. Revocation is the primary access control.
