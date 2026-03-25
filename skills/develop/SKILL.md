---
name: develop
description: Build, structure, migrate, or review Python MCP servers and web APIs for deployment. Use when asked to create a new MCP server, structure a solution repo, add multi-user auth, set up a data store, migrate an existing app to gapp conventions, review an app against standards, or any question about building a deployable Python service — "create an MCP server", "add auth to my app", "how should I structure this", "set up user management", "make this multi-user", "review my solution", "is this ready to deploy", "port this to gapp", etc.
disable-model-invocation: false
user-invocable: true
---

# Develop Skill

## Overview

This skill guides users through building Python MCP servers and
web APIs that are self-contained, deployable apps. Solutions built
with this guidance work locally (stdio, single user) and deployed
(HTTP, multi-user) without code changes.

When the solution is ready to deploy, hand off to the **deploy**
skill. When the user needs to manage users after deployment, hand
off to the **user-management** skill.

## Modes

This skill operates in three modes depending on what the user
needs. Determine the mode from the user's request and the state
of the current working directory.

### Mode 1: Greenfield — Build a New Solution

User wants to create a new MCP server or web API from scratch.
Follow the full guide below from Repository Structure onward.

### Mode 2: Migration — Port an Existing App

User has an existing app (possibly with custom auth, custom
deployment, non-standard structure) and wants to port it to
follow gapp conventions. Steps:

1. Read the existing codebase to understand current structure
2. Walk through the Compliance Checklist below, noting what
   already conforms and what needs to change
3. Propose a migration plan — what to move, what to delete,
   what to add — in priority order
4. Execute the migration with the user's approval
5. Run the checklist again to verify compliance

### Mode 3: Review — Evaluate Against Standards

User wants a compliance check of their existing solution.
Maybe they've refactored, maybe gapp has a new version with
new conventions, maybe they just want to know where they stand.

Run the **Compliance Checklist** below and present results as
a table:

| Item | Status | Notes |
|------|--------|-------|
| SDK layer contains all business logic | ✅ | |
| MCP tools are thin wrappers | ✅ | |
| APP_NAME constant in __init__.py | ❌ | Missing |
| ... | ... | ... |

For each ❌, explain what's wrong and what the fix would be.
Ask the user if they want to fix the issues.

## Compliance Checklist

Use this for Mode 2 (migration) and Mode 3 (review):

### Structure
- [ ] Three-layer architecture: `sdk/`, `mcp/`, optional `cli/`
- [ ] All business logic in `sdk/` — no logic in MCP tools or CLI commands
- [ ] MCP tools are async one-liners calling SDK methods
- [ ] `APP_NAME` constant in `__init__.py`, used everywhere
- [ ] `pyproject.toml` or `setup.py` with correct dependencies

### MCP Server
- [ ] Uses `mcp` package (FastMCP) with `stateless_http=True`
- [ ] DNS rebinding protection disabled (`enable_dns_rebinding_protection = False`)
- [ ] `mcp.run()` for stdio, `app` variable for HTTP (uvicorn)
- [ ] All tools have clear, user-centric docstrings

### Multi-User Auth (if applicable)
- [ ] `app-user` in dependencies
- [ ] `FileSystemUserDataStore` (or custom `UserDataStore`) instantiated
- [ ] `DataStoreAuthAdapter` bridges auth store to data store
- [ ] `create_app()` wires auth + admin + inner app
- [ ] `app` variable assigned from `create_app()` for uvicorn
- [ ] SDK reads `current_user_id` from `app_user.context`
- [ ] Solution's `context.py` re-exports from `app_user.context`

### Environment Variables
- [ ] `SIGNING_KEY` — read by app-user (not hardcoded)
- [ ] `JWT_AUD` — optional, for audience validation
- [ ] `APP_USERS_PATH` — data directory (with XDG fallback)
- [ ] No hardcoded paths in code
- [ ] Tests use the same env vars for isolation

### Testing
- [ ] Sociable unit tests in `tests/unit/`
- [ ] No mocks unless explicitly justified
- [ ] Tests use temp dirs and env vars for isolation
- [ ] Test names describe scenario + outcome

### Deployment Readiness
- [ ] `app` variable in `server.py` for uvicorn HTTP mode
- [ ] `gapp.yaml` present (if deploying with gapp)
- [ ] No Terraform, no custom Dockerfiles (if using gapp to generate)
- [ ] All secrets declared in `gapp.yaml` env section

## Repository Structure

Every solution follows a three-layer architecture:

```
my-solution/
  my_solution/
    __init__.py       # APP_NAME constant
    sdk/              # Business logic — ALL behavior lives here
      core.py         # Main SDK class
      config.py       # Configuration, data paths
    mcp/
      server.py       # MCP tool definitions — thin, calls SDK
    cli/              # Optional — Click commands, calls SDK
      main.py
  tests/
    unit/             # Sociable unit tests, no mocks
  pyproject.toml      # Or setup.py
  gapp.yaml           # Optional — only if deploying with gapp
```

### Rules

- **SDK first.** All behavior lives in the SDK. MCP and CLI are
  thin wrappers that call SDK methods and format output.
- **No business logic in MCP tools.** Tools are async one-liners
  that call SDK methods.
- **No business logic in CLI commands.** Commands call SDK methods.
- **If you're writing logic in a tool or command, stop and move it
  to SDK.**

### APP_NAME constant

Define once, use everywhere:

```python
# my_solution/__init__.py
APP_NAME = "my-solution"
```

Used for FastMCP server name, data store paths, and local XDG
directory naming.

## MCP Server Setup

### Dependencies

Use the `mcp` package (which includes FastMCP):

```toml
[project]
dependencies = [
    "mcp[cli]",
    "pyyaml",
]
```

### Basic server

```python
# my_solution/mcp/server.py
import os
from mcp.server.fastmcp import FastMCP
from my_solution import APP_NAME
from my_solution.sdk.core import MySDK

mcp = FastMCP(APP_NAME, stateless_http=True, json_response=True)

# DNS rebinding — disable for Cloud Run deployments
mcp.settings.transport_security.enable_dns_rebinding_protection = False

sdk = MySDK()

@mcp.tool()
async def my_tool(param: str) -> dict:
    return sdk.do_thing(param)

def run_server():
    mcp.run()

if __name__ == "__main__":
    run_server()
```

### stdio vs HTTP

- **stdio** (local): `python -m my_solution.mcp.server` or
  the CLI entry point calls `mcp.run()`
- **HTTP** (deployed): `uvicorn my_solution.mcp.server:app`
  where `app` is an ASGI object (see Multi-User Auth below)

Both use the same MCP tools, same SDK. The only difference is
how the server is started and whether auth is present.

## CLI (Optional)

Use Click for CLI commands:

```toml
[project]
dependencies = [
    "click",
]

[project.scripts]
my-solution = "my_solution.cli.main:cli"
```

```python
# my_solution/cli/main.py
import click
from my_solution.sdk.core import MySDK

sdk = MySDK()

@click.group()
def cli():
    pass

@cli.command()
def do_thing():
    result = sdk.do_thing()
    click.echo(result)
```

## Multi-User Auth (app-user)

For solutions that need multi-user support — user identity,
registration, revocation, data scoping — use the `app-user`
library. This is optional. Single-user solutions skip this.

**app-user repo:** `krisrowe/app-user`

### Add dependency

```toml
[project]
dependencies = [
    "mcp[cli]",
    "app-user",
]
```

### Wire it up

```python
# my_solution/mcp/server.py
import os
from mcp.server.fastmcp import FastMCP
from app_user import create_app, FileSystemUserDataStore, DataStoreAuthAdapter
from app_user.context import current_user_id
from my_solution import APP_NAME
from my_solution.sdk.core import MySDK

mcp = FastMCP(APP_NAME, stateless_http=True, json_response=True)
mcp.settings.transport_security.enable_dns_rebinding_protection = False

# Data store — reads APP_USERS_PATH env var, falls back to XDG
store = FileSystemUserDataStore(app_name=APP_NAME)
auth_store = DataStoreAuthAdapter(store)
sdk = MySDK(store)

@mcp.tool()
async def my_tool(param: str) -> dict:
    # current_user_id is set by app-user's middleware automatically
    return sdk.do_thing(param)

# HTTP mode — ASGI app with auth + admin endpoints
app = create_app(store=auth_store, inner_app=mcp.asgi_app())

# stdio mode
def run_server():
    mcp.run()

if __name__ == "__main__":
    run_server()
```

### What this gives you

- `app` — ASGI object for uvicorn. JWT auth on all requests.
  `/admin` REST endpoints for user management. `current_user_id`
  ContextVar set automatically per request.
- `mcp.run()` — stdio, single user, no auth. `current_user_id`
  defaults to `"default"`.
- Same tools, same SDK, both modes.

### Reading user identity in the SDK

```python
# my_solution/sdk/core.py
from app_user.context import current_user_id

class MySDK:
    def __init__(self, store=None):
        self.store = store

    def do_thing(self, param):
        user = current_user_id.get()  # "default" or "alice@example.com"
        # Use user to scope data, files, etc.
```

The SDK never imports FastMCP. It reads `current_user_id` which
is set by app-user's middleware (HTTP) or defaults to `"default"`
(stdio). Framework-agnostic.

### Data storage

`FileSystemUserDataStore` provides per-user JSON storage:

```python
# Save
store.save("alice@example.com", "daily/2026-03-25", entries)

# Load
data = store.load("alice@example.com", "daily/2026-03-25")

# List users
users = store.list_users()  # ["alice@example.com", "bob@example.com"]
```

Directory layout:
```
~/.local/share/my-solution/users/    (local)
/mnt/solution-data/users/             (Cloud Run)

  alice~example.com/
    auth.json              # managed by app-user
    daily/
      2026-03-25.json      # managed by your SDK
```

Email `@` is replaced with `~` for directory names. Reversible,
no collisions.

### Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `SIGNING_KEY` | For HTTP | `"dev-key"` | JWT signing |
| `JWT_AUD` | No | None (skip check) | Token audience |
| `APP_USERS_PATH` | No | `~/.local/share/{app_name}/users/` | Data directory |
| `TOKEN_DURATION_SECONDS` | No | 315360000 (~10yr) | Default token lifetime |

For gapp deployment, these are set in `gapp.yaml` under `env:`.

## Testing

### Sociable unit tests

- No mocks unless explicitly needed
- Isolate via temp dirs and env vars
- Use the same env vars the solution reads in production

```python
# tests/unit/test_something.py
import os
import pytest

def test_stores_data_in_configured_path(tmp_path):
    os.environ["APP_USERS_PATH"] = str(tmp_path / "users")
    try:
        # Test that SDK reads/writes to the configured path
        sdk = MySDK(FileSystemUserDataStore(app_name="my-solution"))
        sdk.do_thing("test")
        assert (tmp_path / "users" / "default" / "some-file.json").exists()
    finally:
        del os.environ["APP_USERS_PATH"]
```

### Test names

Describe scenario + outcome, not implementation:
- Good: `test_logs_food_to_current_date_directory`
- Bad: `test_returns_true_when_file_exists`

### Test location

- Unit tests: `tests/unit/` — fast, no network, no credentials
- Integration tests: `tests/integration/` — only when explicitly
  requested, excluded from default pytest run

## Final Step: Compliance Dashboard

**Always conclude with this** — whether greenfield, migration, or
review. Run the Compliance Checklist and present results:

```
## Solution Compliance Dashboard: {APP_NAME}

| Category | Item | Status |
|----------|------|--------|
| Structure | SDK layer contains all business logic | ✅ |
| Structure | MCP tools are thin wrappers | ✅ |
| Structure | APP_NAME constant in __init__.py | ✅ |
| MCP | Uses FastMCP with stateless_http=True | ✅ |
| MCP | DNS rebinding protection disabled | ✅ |
| MCP | app variable for uvicorn HTTP mode | ❌ |
| Auth | app-user in dependencies | ✅ |
| Auth | create_app() wires auth + admin | ❌ |
| Testing | Sociable unit tests exist | ✅ |
| Testing | Tests use env vars for isolation | ⚠️ |
| Deploy | gapp.yaml present | ❌ |
| ... | ... | ... |

✅ = conforms  ❌ = missing/wrong  ⚠️ = partial
```

After presenting the dashboard:

1. If there are ❌ or ⚠️ items: "Want me to fix these?"
2. If all ✅: "This solution is ready. Next steps:"
   - **Deploy** → hand off to the **deploy** skill
   - **User management** → hand off to the **user-management**
     skill (if using app-user)
   - **Stay here** → if the user wants to add features or
     refactor further

## What This Skill Does NOT Cover

- Deployment to Cloud Run (→ deploy skill)
- CI/CD setup (→ deploy skill)
- User registration and management after deploy (→ user-management skill)
- gapp infrastructure (terraform, secrets, GCS) (→ deploy skill)
