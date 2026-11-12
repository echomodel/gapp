# gapp.yaml: candidacy, minimal config, cloud readiness

Read this before writing or changing `gapp.yaml`, and before
proposing gapp to a repo that has none.

## Is the repo a fit?

Inspect the working directory first. gapp deploys an app that serves
HTTP and runs in a container. A repo with its own `Dockerfile` can be
any language. Without one, gapp builds from its own template, which
installs a Python project's declared dependencies (`pyproject.toml` or
`setup.py`), so the app must be a Python package.

Not a fit: no HTTP interface (a CLI or a library only), or already
deployed elsewhere and the user does not want to move. Say so and
stop; do not force it.

When it fits, present gapp briefly: your solution stays
cloud-agnostic, you add a small `gapp.yaml` and a one-line Procfile,
gapp handles build, secrets, data volumes, domain mapping, and IAM,
and it takes four idempotent steps (init, setup, secrets, deploy),
each of which tells you what comes next. Then ask whether to set it
up.

## How the app starts

gapp starts an app from a file in the app's folder, never from
`gapp.yaml`:

- **A Dockerfile.** gapp builds it as is.
- **A Procfile** with one `web:` line: the command that starts the
  app and serves HTTP on `$PORT`. gapp builds from its own template
  and runs that command.

With both, the Dockerfile wins and the Procfile is ignored. If the
repo has a Dockerfile, that is the start method; you are done.
Otherwise:

1. Work out the command that starts the app and serves HTTP on
   `$PORT`. Read the README, the code and `pyproject.toml`: a console
   script that serves (`[project.scripts]`), or an ASGI app object
   that a server such as uvicorn runs. Use only what the repo shows;
   do not assume a framework.
2. Propose it to the user as a Procfile line and say where it came
   from, for example:

   ```
   web: my-app serve
   web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT
   ```

   A server the command names (uvicorn above) must be one of the
   app's own dependencies; gapp supplies none.
3. Once the user confirms, write `Procfile` with that one line in the
   app's folder (the repo root, or each `paths:` service folder), and
   run `gapp init`. It reports `Starts with: web: ... (Procfile)` when
   gapp sees it.

Never put the start command in `gapp.yaml`: it has no start settings,
and unknown keys are refused. When any gapp command reports "Not
deployable yet" or "has no start method", relay its message to the
user and ask; do not guess a command.

## Minimal configuration

Put as little in `gapp.yaml` as possible. gapp derives what it can,
and the solution's own files (Dockerfile, Procfile, pyproject) take
precedence, so every line you add is a line that can drift.

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
# How the app starts is its Procfile or Dockerfile, not this file.

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

Make these changes with `gapp manifest` rather than by editing the
file: `gapp manifest set public true`, `gapp manifest env set
APP_KEY --secret app-key --generate`, `gapp manifest env set
APP_USERS_PATH --data-path users`, `gapp manifest env set LOG_LEVEL
INFO` (MCP: `gapp_manifest_set`, `gapp_manifest_env_set`). Each one is
validated and the file is written only when it changes. `gapp manifest
show` (`gapp_manifest_show`) shows what is set now, each setting with
what it means.

The name is the deployment's identity: it keys the service, data
bucket and secrets. `gapp init` writes the derived name; changing it
later makes a new, empty deployment, so `gapp manifest set name`
refuses without `--new-deployment`. Pass that only after the user
confirms they want a new deployment.

For every key with a comment, run `gapp manifest example`
(`gapp_manifest_example`); `gapp manifest schema` (`gapp_schema`) is the
JSON Schema. Both come from the model gapp validates against.

**A broken gapp.yaml.** When any command reports that gapp.yaml can't
be read or has settings gapp doesn't accept, relay the message: it
names the line or key. Prefer fixing those lines, which keeps the other
settings. Starting over with `gapp init --fresh` (`gapp_init(fresh=True)`)
keeps the old file as gapp.yaml.bak and leaves only the name; pass it
only after the user says yes, then re-add their settings with
`gapp manifest`.

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
