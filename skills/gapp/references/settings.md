# Fit, start method and settings

Read this before defining a deployment for a repo, and before changing
a deployment's settings.

## Is the repo a fit?

Inspect the working directory first. gapp deploys an app that serves
HTTP and runs in a container. A repo with its own `Dockerfile` can be
any language. Without one, gapp builds from its own template, which
installs a Python project's declared dependencies (`pyproject.toml` or
`setup.py`), so the app must be a Python package.

Not a fit: no HTTP interface (a CLI or a library only), or already
deployed elsewhere and the user does not want to move. Say so and
stop; do not force it.

When it fits, present gapp briefly: the app's repo stays
cloud-agnostic and needs only a Procfile or a Dockerfile; the
deployment is defined once, outside the repo; gapp handles the build,
secrets, a data volume, the domain and access. Each step says what
comes next. Then ask whether to set it up.

## How the app starts

gapp starts the app from the first of these:

| In the repo | What gapp does |
|---|---|
| A `Dockerfile` | Builds it as it is |
| A `Procfile` with exactly one `web:` line | Builds from its own template and runs that command |
| Neither | Uses the definition's `start` setting |

The command must serve HTTP on `$PORT`. gapp detects nothing and knows
no frameworks, so when a repo has neither file:

1. Work out the start command from the README, the code and the
   project file. Do not invent a command the repo does not support.
2. Propose the one-line Procfile to the user and get a yes before
   writing it:

   ```
   web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT
   ```

3. Commit it. A deploy builds the committed tree.

For a repo the user cannot change, set the command on the definition
instead: `gapp set start 'my-app serve'`. A Procfile or Dockerfile
wins over `start`.

## Settings

Everything else about a deployment is a setting of its definition.
Nothing goes in the app's repo.

| To | Run |
|---|---|
| See the definition and every setting a deploy would use | `gapp show` |
| Let anyone call the URL (the app does its own auth) | `gapp set public true` |
| Map a domain | `gapp set domain app.example.com` (`references/lifecycle.md`, Custom domain) |
| Size it | `gapp set memory 1Gi`, `gapp set cpu 2`, `gapp set max_instances 3` |
| A plain variable | `gapp env set LOG_LEVEL info` |
| A variable that points at the data folder | `gapp env set APP_DATA --data-path users` |
| A variable from a secret | `gapp env set API_TOKEN --secret` (`references/secrets.md`) |
| An extra Google Cloud API | `gapp apis add sheets.googleapis.com` |
| Remove one | `gapp unset <key>`, `gapp env unset <NAME>`, `gapp apis remove <api>` |
| Replace all of them at once | `gapp apply <file>`; `--dry-run` first |

Defaults: not public, no domain, `512Mi`, 1 cpu, 1 instance.

**Data.** Each deployment has a bucket mounted in the container. A
variable set with `--data-path` holds the path of a folder there, which
survives redeploys and restarts. Use it for whatever the app writes to
disk; anything written elsewhere is lost when the container stops.

**`gapp apply`.** The file is the settings object and nothing else:

```yaml
public: true
domain: app.example.com
memory: 1Gi
env:
  LOG_LEVEL: {value: info}
  APP_DATA: {value: "{{DATA_PATH}}/users"}
  API_TOKEN: {secret: api-token}
  APP_KEY: {secret: app-key, generate: true}
```

A setting the file leaves out goes back to its default, variables
included, so start from what is there: `gapp show --json`, under
`settings`. With the tools, `gapp_show` then `gapp_apply`.

Map the app's needs to these primitives from the app's own docs: which
variables it reads, which are secrets, where it writes data, whether it
does its own auth. gapp knows nothing about the app's framework.

## Cloud readiness check for MCP servers

Before defining the deployment, look at how the server authenticates to its backend
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
auth layer the app chooses then sits in front of this path.

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

This is safe because the load balancer in front of Cloud Run validates
hosts, and locally the SDK's localhost-only protection stays on.
