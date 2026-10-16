# gapp.yaml: candidacy, minimal config, cloud readiness

Read this before writing or changing `gapp.yaml`, and before
proposing gapp to a repo that has none.

## Is the repo a fit?

Inspect the working directory first: `pyproject.toml` or
`setup.py` means a Python package; look for uvicorn entrypoints,
FastMCP, Starlette, or FastAPI; a `Dockerfile` means it may already
be containerized.

Good candidates: a Python web server (FastAPI, Starlette, ASGI
Flask), a Python MCP server over HTTP, a clear ASGI entrypoint
(`module.path:app`), something that needs to be reachable on the
internet.

Not a fit: not Python, CLI-only with no web server, no ASGI or HTTP
interface, or already deployed elsewhere and the user does not want
to move. Say so and stop; do not force it.

When it fits, present gapp briefly: your solution stays
cloud-agnostic, you add a small `gapp.yaml`, gapp handles build,
secrets, data volumes, domain mapping, and IAM, and it takes four
idempotent steps (init, setup, secrets, deploy), each of which
tells you what comes next. Then ask whether to set it up.

## Minimal configuration

Put as little in `gapp.yaml` as possible. gapp derives what it can,
and the solution's own files (Dockerfile, pyproject) take precedence,
so every line you add is a line that can drift.

What gapp needs to know, in priority order:

**How to build and run the container.**
1. A `Dockerfile` exists: gapp builds it as is; no `service` block.
2. The app has its own serve command: `service.cmd`, and gapp
   generates a Dockerfile with it as CMD.
3. The app exposes an ASGI object at module level:
   `service.entrypoint` such as `my_package.server:app`, and gapp
   wraps it with uvicorn.
4. None of the above: one of them has to be provided; find or create
   the entrypoint with the user.

**Whether to allow public access.** `public: true` grants allUsers
on Cloud Run. Default is locked down. Solutions that do their own
auth (most MCP servers, since MCP clients cannot attach IAM tokens)
need `public: true`. A deploy can override it per run without
editing the file.

**Environment variables and secrets.** Read the app's README or
framework docs for what it needs from its environment, then map
each item:

- secret (API key, token, credential): always the mapping form,
  `secret: { name: <short-name> }` when the user supplies the value,
  or `secret: { name: <short-name>, generate: true }` for a value
  that only needs to be strong and random. gapp stores it in Secret
  Manager as `<solution>-<short-name>` and injects it at runtime; the
  value never touches the repo. A bare `secret: true` is rejected by
  validation; only the mapping form exists.
- persistent storage path: a subdirectory under
  `{{SOLUTION_DATA_PATH}}`, which resolves to the GCS FUSE mount at
  deploy time and survives restarts and redeploys.
- plain config: `value: "..."`.

`{{SOLUTION_NAME}}` resolves to the solution name.

```yaml
# Required when there is no Dockerfile; pick one:
service:
  cmd: my-app-mcp serve              # the app's own serve command
  # OR
  entrypoint: my_package.server:app  # ASGI module:app for uvicorn

# Optional, default false:
public: true

# Optional custom domain (subdomain only, needs a CNAME):
domain: mcp.example.com

# Optional env vars and secrets:
env:
  - name: APP_KEY
    secret:
      name: app-key
      generate: true
  - name: APP_USERS_PATH
    value: "{{SOLUTION_DATA_PATH}}/users"
  - name: LOG_LEVEL
    value: INFO
```

For the full field list, run `gapp manifest schema` (or the
`gapp_schema` tool) rather than guessing; the schema is the live
source of truth and validation errors quote it.

## Cloud readiness check for MCP servers

Before init, look at how the server authenticates to its backend
API. Find where the HTTP client gets its credential.

- Reads the `Authorization` header from the incoming request:
  cloud-ready. The credential arrives per request.
- Reads a credential from an environment variable or a local file
  (`os.getenv("SOME_TOKEN")`, `~/.config/tool/token`): not
  cloud-ready. Deployed as is, every unauthenticated request to the
  URL would act as the user. Do not suggest a Cloud Run secret env
  var as a workaround; that is the same unauthenticated proxy with
  extra steps.

When you find the second pattern, propose the refactor before
proceeding, roughly:

> Your service reads backend credentials from an environment
> variable or local file. That works for local stdio, but on Cloud
> Run it would mean any request to the URL gets full access to your
> account. A small refactor keeps both working: the SDK accepts an
> optional token; when a request supplies one it is used, otherwise
> it falls back to the environment variable for local use, and if
> neither exists it raises a proper "not authenticated" error. Want
> me to make that change?

On yes, in order: make the credential-fetching function accept an
optional token (provided token, else env var, else raise an auth
error, not a missing-env-var error); thread it through the client
to the API calls; in HTTP mode extract `Authorization: Bearer` from
the request and pass it in, in stdio mode pass nothing. Any client
auth layer the solution chooses then sits in front of this path.

## DNS rebinding protection on Cloud Run

FastMCP rejects requests whose `Host` header it does not expect, and
Cloud Run's load balancer sets one it does not expect, so every
request fails with HTTP 421 "Invalid Host header". If `FastMCP` is
constructed with no transport security override, fix it keyed on the
`K_SERVICE` variable Cloud Run sets:

```python
if os.environ.get("K_SERVICE"):
    from mcp.server.transport_security import TransportSecuritySettings
    mcp = FastMCP("name", transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    ))
else:
    mcp = FastMCP("name")
```

This is safe because Google's load balancer validates hosts on Cloud
Run, and locally the SDK's localhost-only protection stays on.
