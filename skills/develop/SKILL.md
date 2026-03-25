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

**First, help them decide: local or remote?**

**Local MCP server (stdio)** makes sense when:
- Works with local files, code, git repos, or system config
- Manages the local workstation or development environment
- Needs fast, low-latency interaction
- Single user, single machine

**Remote MCP server (HTTP)** makes sense when:
- Accesses cloud data or external APIs
- Needs to be available from multiple devices (phone, laptop, desktop)
- Needs multi-user support
- Benefits from always-on availability (logging, tracking, etc.)

Based on this, the solution targets stdio only (simpler — no auth,
no deployment) or stdio + HTTP (needs `app` variable, auth setup,
deployment planning). Both follow the same repo structure.

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

### Multi-User Auth (only if solution uses app-user)
- [ ] `app-user` in dependencies
- [ ] `FileSystemUserDataStore` (or custom `UserDataStore`) instantiated
- [ ] `DataStoreAuthAdapter` bridges auth store to data store
- [ ] `create_app()` wires auth + admin + inner app
- [ ] `app` variable assigned from `create_app()` for uvicorn
- [ ] SDK reads `current_user_id` from `app_user.context`
- [ ] Solution's `context.py` re-exports from `app_user.context`

### Paths and Environment Variables
- [ ] XDG path resolver functions in SDK (data, config, cache)
- [ ] Each resolver checks env var override first, then XDG fallback with APP_NAME
- [ ] No hardcoded absolute paths in code
- [ ] Tests use the same env var overrides for isolation
- [ ] (If using app-user) `SIGNING_KEY`, `JWT_AUD`, `APP_USERS_PATH` env vars handled

### Testing
- [ ] Sociable unit tests in `tests/unit/`
- [ ] No mocks unless explicitly justified
- [ ] Tests use temp dirs and env vars for isolation
- [ ] Test names describe scenario + outcome

### Documentation
- [ ] README.md: why the repo exists, quick start, deployment, CLI overview, config, dev guide
- [ ] CONTRIBUTING.md: architecture, testing standards, conventions, how to add features
- [ ] CLAUDE.md: thin, `@import README.md` and `@import CONTRIBUTING.md`, no other content
- [ ] `.gemini/settings.json`: `context.fileName` pointing to README.md and CONTRIBUTING.md, committed, no secrets/tokens
- [ ] `.gitignore` includes `.gemini/*` and `!.gemini/settings.json`
- [ ] No stale references to removed features or old architecture in any docs

### Deployment Readiness
- [ ] `app` variable in `server.py` for uvicorn HTTP mode
- [ ] `gapp.yaml` present (if deploying with gapp)
- [ ] No Terraform, no custom Dockerfiles (if using gapp to generate)
- [ ] All secrets declared in `gapp.yaml` env section

## Gitignore

Start with this baseline for Python MCP repos:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
build/
dist/
.venv/
venv/
.eggs/

# Environment
.env

# Testing
.pytest_cache/

# Gemini (except settings.json)
.gemini/*
!.gemini/settings.json

# OS
.DS_Store

# Logs
*.log
```

Add solution-specific entries as needed (data files, temp dirs,
etc.). Avoid bloated templates with entries for tools you don't
use (`develop-eggs/`, `lib64/`, `MANIFEST`, etc.).

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

## XDG Path Convention

Solutions use XDG Base Directory Specification for local paths.
Each major XDG directory has a global resolver function in the SDK
that checks an env var first, then falls back to XDG with the
app name:

```python
# my_solution/sdk/paths.py
import os
from pathlib import Path
from my_solution import APP_NAME

def get_data_dir() -> Path:
    """Data directory (logs, user data, catalogs)."""
    env = os.environ.get("MY_SOLUTION_DATA")
    if env:
        return Path(env).expanduser().resolve()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME

def get_config_dir() -> Path:
    """Config directory (settings, app.yaml)."""
    env = os.environ.get("MY_SOLUTION_CONFIG")
    if env:
        return Path(env).expanduser().resolve()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME

def get_cache_dir() -> Path:
    """Cache directory (temporary data, can be deleted)."""
    env = os.environ.get("MY_SOLUTION_CACHE")
    if env:
        return Path(env).expanduser().resolve()
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / APP_NAME
```

The env var overrides (`MY_SOLUTION_DATA`, etc.) serve two purposes:
1. **Deployment** — gapp.yaml maps them to GCS FUSE mount paths
2. **Testing** — unit tests set them to temp dirs for isolation

## CLI Layer

Use Click for CLI commands. The CLI is a thin wrapper that calls
SDK methods — same rule as MCP tools.

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

### CLI scope — discuss with the user

The CLI's role varies by solution. Discuss with the user:

**Minimal CLI (management only):**
- Configuration management (set data paths, change settings)
- Security-sensitive operations the user doesn't want an agent
  doing (rotating keys, changing auth settings, wiping data)
- Operations that require confirmation or are destructive
- The MCP tools handle all day-to-day functionality

**Full parity CLI:**
- Every MCP tool has a CLI equivalent
- Useful for scripting, cron jobs, pipelines
- More work to maintain — every new feature needs two interfaces

**Recommended starting point:** minimal CLI for management and
security-sensitive operations. Add CLI equivalents of MCP tools
only when the user has a concrete need (scripting, automation,
non-agent workflows). Don't build full parity speculatively.

### SDK returns JSON, always

Regardless of CLI scope, all logic lives in the SDK. SDK methods
return dicts (JSON-serializable). Both MCP tools and CLI commands
call the same SDK methods and get the same data:

```python
# SDK returns a dict
def log_food(self, entries) -> dict:
    return {"success": True, "date": "2026-03-25", "entries_added": 2}

# MCP tool returns it directly
@mcp.tool()
async def log_meal(food_entries: list) -> dict:
    return sdk.log_food(food_entries)

# CLI formats it for humans, or passes through as JSON
@cli.command()
@click.option("--json", "as_json", is_flag=True)
def log(food, as_json):
    result = sdk.log_food(food)
    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(f"Logged {result['entries_added']} entries for {result['date']}")
```

One SDK method. Two interfaces. Same data. `--json` gives the
raw SDK output. Human-readable formatting is CLI-only.

## Multi-User Auth (app-user) — Optional

If the solution needs multi-user support — user identity,
registration, revocation, per-user data scoping — the `app-user`
library (`krisrowe/app-user`) handles JWT auth, admin endpoints,
and per-user data storage with minimal wiring.

**This is entirely optional.** Solutions can implement their own
auth however they want, or skip auth entirely for single-user
tools. app-user is a convenience for the common case, not a
requirement. Mention it as an option — don't push it.

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
app = create_app(store=auth_store, inner_app=mcp.streamable_http_app())

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

Tests should exercise real code paths with minimal stubbing:

- **No mocks** unless needed to avoid network I/O or to manage
  env vars that affect global state
- **Isolate via env vars** — set the same env var overrides the
  solution's XDG path functions read (`MY_SOLUTION_DATA`, etc.)
  to temp directories. This proves the env var contract works
  AND provides test isolation.
- **Temp dirs for data** — use `tmp_path` (pytest fixture) and
  point env vars at it
- **The env vars tests set are the same ones gapp.yaml maps in
  production** — tests validate the deployment contract

```python
# tests/unit/test_something.py
import os
import pytest

@pytest.fixture(autouse=True)
def isolated_data(tmp_path):
    """Point data dir to temp for every test."""
    os.environ["MY_SOLUTION_DATA"] = str(tmp_path / "data")
    os.environ["MY_SOLUTION_CONFIG"] = str(tmp_path / "config")
    yield
    del os.environ["MY_SOLUTION_DATA"]
    del os.environ["MY_SOLUTION_CONFIG"]

def test_logs_food_to_date_directory(tmp_path):
    sdk = MySDK()
    result = sdk.log_food([{"food_name": "apple"}])
    assert result["success"]
    data_dir = tmp_path / "data"
    assert any(data_dir.rglob("*food-log.json"))
```

### When to stub

- **Network I/O** — stub HTTP clients, API calls. Never hit
  real APIs in unit tests.
- **Subprocess calls** — stub if the subprocess isn't ubiquitous
  (e.g., don't stub `git init`, do stub `gcloud`).
- **Everything else** — use real code. Real file I/O (to temp
  dirs), real JSON parsing, real config loading. Sociable tests
  catch integration bugs that mocks hide.

### Test names

Describe scenario + outcome, not implementation:
- Good: `test_logs_food_to_current_date_directory`
- Bad: `test_returns_true_when_file_exists`

### Test location

- Unit tests: `tests/unit/` — fast, no network, no credentials
- Integration tests: `tests/integration/` — only when explicitly
  requested, excluded from default pytest run

## Documentation

### README.md

Every solution repo must have a thorough README.md covering:

- **Why this repo exists** — what problem it solves, who it's for
- **Quick start** — install and register with an MCP client (stdio)
- **HTTP deployment** — env vars, running with uvicorn, deploying
  with gapp or without gapp
- **User management** — link to app-user if applicable
- **MCP client configuration** — Claude.ai, Claude Code, Gemini CLI
- **Configuration** — settings, timezone, data paths
- **CLI commands** — overview of available commands and capabilities
- **Development** — repo structure, how to run tests

README.md is the user-facing document. It answers "how do I use
this?" and "why should I care?"

### CONTRIBUTING.md

Contributor-facing design principles and constraints:

- Architecture decisions (SDK-first, three-layer structure)
- Testing standards (sociable, no mocks, env var isolation)
- Code conventions
- Version management
- How to add new features (SDK first, then MCP tool, then CLI)
- Security considerations
- What NOT to do (no business logic in MCP/CLI layers)

CONTRIBUTING.md answers "how do I work on this?" and "what are
the rules?"

### Agent context files

**CLAUDE.md** — project-level, thin. `@import` README.md and
CONTRIBUTING.md so Claude Code always has full context:

```markdown
@import README.md
@import CONTRIBUTING.md
```

No other content.

**`.gemini/settings.json`** — same purpose for Gemini CLI.
Points to the same files via `contextFiles`:

```json
{
  "context": {
    "fileName": [
      "README.md",
      "CONTRIBUTING.md"
    ]
  }
}
```

This file should be committed (not gitignored). Before
committing, verify it contains no secrets, tokens, API keys,
or sensitive paths (e.g., MCP server URLs with embedded tokens).

**Gitignore pattern** — add to `.gitignore`:

```
# Gemini folder (except settings.json)
.gemini/*
!.gemini/settings.json
```

This keeps Gemini's temp/cache files out of git while preserving
the config. If `.gemini/settings.json` contains MCP server
configurations, review them for embedded credentials before
committing — tokens in URLs, authorization headers, or API keys
must not be committed to non-personal repos.

### Review during compliance check

As part of the compliance dashboard, review README.md and
CONTRIBUTING.md for:
- Thoroughness — are all sections present and filled out?
- Accuracy — does it match the current code?
- No stale references to removed features or old architecture

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
