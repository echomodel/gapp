---
name: user-management
description: Manage users for deployed MCP solutions. Use when asked to register a user, list users, revoke access, create tokens, configure the mcp-app admin client, or test a deployed service with authentication — "register a user", "add alice to my-app", "list users", "revoke bob", "set up user management", "test the deployed service", etc.
disable-model-invocation: false
user-invocable: true
---

# User Management Skill

## Overview

This skill manages users for deployed MCP solutions. It covers
configuring the admin client, registering users, issuing tokens,
revoking access, and verifying the deployed service works
end-to-end.

User management is provided by mcp-app's admin endpoints and CLI.
The admin REST endpoints (`/admin/users`, `/admin/tokens`) are
mounted automatically in HTTP mode. The `mcp-app` CLI provides
local management commands.

**Prerequisites:**
- Solution is deployed and running (use **deploy** skill first)
- Solution uses mcp-app with middleware configured, or has admin
  endpoints wired manually
- `SIGNING_KEY` secret exists in the deployment

## Step 1: Configure the mcp-app Admin Client

The mcp-app CLI needs to know the service URL and signing key
for the target solution. This is a one-time setup per solution.

### Get the signing key

Use `gapp_secret_get` with `plaintext=True` to retrieve the
signing key value:

```
gapp_secret_get(env_var_name="SIGNING_KEY", plaintext=True)
```

Or via CLI:
```bash
gapp secrets get SIGNING_KEY --raw | \
  my-solution-admin connect \
    "$(gapp status --url)" \
    --signing-key-stdin
```

### Verify configuration

```bash
my-solution-admin health
```

Should show `healthy (200)`.

## Step 2: Register the First User

```bash
mcp-app users add alice@example.com
```

This calls `POST /admin/users` on the running service. Returns:
- `email`: the registered email
- `token`: a long-lived JWT for the user

**Save the user token** — this is what the user configures in
their MCP client (Claude.ai, Claude Code, Gemini CLI).

## Step 3: Test the Deployment

### Test with curl

```bash
curl -H "Authorization: Bearer <user-token>" \
  https://my-service.run.app/
```

Should get a valid MCP response (not 401/403).

### Test with MCP client

**Claude Code (CLI):**
```bash
claude mcp add --transport http my-solution \
  https://my-service.run.app/ \
  --header "Authorization: Bearer <user-token>"
```

**Claude.ai / Claude mobile / Claude Code (remote via URL):**
```
https://my-service.run.app/?token=<user-token>
```

Remote MCP servers added through Claude.ai are available across
all Claude clients — web, mobile app, and Claude Code.

**Gemini CLI (manual config):**
Add to `~/.gemini/settings.json`:
```json
{
  "mcpServers": {
    "my-solution": {
      "url": "https://my-service.run.app/",
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
mcp-app users list
```

### Revoke a user

```bash
mcp-app users revoke bob@example.com
```

Existing tokens for the revoked user immediately stop working
(server checks `revoke_after` against token `iat`).

### Issue a new token for an existing user

```bash
mcp-app tokens create alice@example.com
```

Useful when a user loses their token or after revoking and
reactivating (new token's `iat` is after `revoke_after`).

## What This Skill Does NOT Cover

- Building the solution (→ author-mcp-app skill in echoskill)
- Deploying the solution (→ deploy skill)
- Credential mediation for API-proxy apps (→ echomodel/mcp-app#8)

## Important Notes

- User management is an mcp-app concern, not a gapp concern. The
  admin endpoints and CLI are part of the mcp-app package.
- Admin tokens are generated locally using the signing key. They
  never pass through the deployed service for creation.
- User tokens are long-lived because MCP clients cannot refresh
  tokens automatically. Revocation is the primary access control.
