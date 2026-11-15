# gapp

gapp deploys apps that serve HTTP (web servers, APIs, MCP servers) to
Google Cloud Run. You define a deployment once, and gapp builds it,
deploys it with Terraform, wires its secrets and data, and maps its
domain.

```bash
gapp project setup my-project
gapp add my-app --project my-project
gapp deploy
```

## Why gapp, and when plain gcloud is enough

`gcloud run deploy --source .` builds and runs a container in one command.
For one service with no secrets, data or domain, that is enough. gapp
adds what that command leaves to you:

| You get | How |
|---|---|
| A redeploy that changes only what changed | Each deployment's service, access, secrets, volume and domain are applied with Terraform, from state kept in the project's own gapp bucket, which no app can reach |
| Data that survives redeploys | Each deployment has a bucket, mounted in the container |
| Secrets that are checked before a deploy | A deployment names the secrets it reads. gapp makes generated ones, and refuses to deploy while one has no value |
| A domain that is not taken down by accident | A deploy that would replace or remove a live custom domain stops before anything is built |
| One record of everything you run | Your fleet: each deployment's repo, project, ref and settings, in one file |
| A GitHub-only machine runs everything after setup | Once a project Owner has set up a project, a machine with only GitHub access (a teammate's laptop, a cloud agent session) adds, changes, upgrades, deploys and checks deployments: `gapp ci` runs the deploy on a GitHub Actions runner |
| The same commands for an agent | Every command is also an MCP tool |

An app's repo never names gapp, a project or Google Cloud, so anyone can
deploy the same repo to their own project without changing it.

## Install

```bash
pipx install "gapp @ git+https://github.com/echomodel/gapp.git@<release-tag>"
```

A deploy from your machine also needs `gcloud`, signed in, and Terraform.
`gapp ci` needs only the GitHub CLI, `gh`.

## The words gapp uses

| Word | Meaning |
|---|---|
| Fleet | The record of your deployments. One file, kept on your machine or in a git repo |
| Definition | One deployment's entry in the fleet: its name, repo, project, the ref a deploy builds, and its settings |
| Deployment | The running service with its data and secrets, in Google Cloud |
| Setting | One value in a definition: public access, the domain, sizing, a variable |

[The interface](docs/INTERFACE.md) lists every command, the fleet file's
shape and every MCP tool.

## Your first deployment

### 1. Make the app startable

gapp starts your app from the first of these it finds:

| In the repo | What gapp does |
|---|---|
| A `Dockerfile` | Builds it as it is |
| A `Procfile` with one `web:` line | Builds an image from its own template and runs that command |
| Neither | Uses the definition's `start` setting, for a repo you can't change |

The command must serve HTTP on `$PORT`:

```bash
echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile
```

### 2. Prepare the project, once

```bash
gapp project setup my-project
```

It turns on the APIs every deployment needs, gives Cloud Build its
permissions, makes the project's gapp bucket (`gapp-<project>`, for
Terraform state and a record of each deploy) and marks the project. It needs gcloud signed in as an Owner
of the project.

### 3. Define the deployment

Inside a clone of the app's repo:

```bash
gapp add --project my-project
```

The name and repo come from the clone's remote. From anywhere else, name
them: `gapp add my-app --project my-project --repo owner/my-app`. This
touches no cloud.

### 4. Give it its settings

```bash
gapp set public true
gapp set domain app.example.com
gapp env set LOG_LEVEL info
gapp env set APP_DATA --data-path users
gapp env set API_KEY --secret
gapp env set APP_KEY --secret --generate
gapp show
```

| Command | Does |
|---|---|
| `gapp set <key> <value>` / `gapp unset <key>` | `public`, `domain`, `start`, `memory`, `cpu`, `max_instances`, and `ref` |
| `gapp env set NAME <value>` | A plain variable |
| `gapp env set NAME --data-path [<subfolder>]` | The variable points at the deployment's data folder |
| `gapp env set NAME --secret [<secret-name>]` | The variable comes from a secret. `--generate` has gapp make its value once |
| `gapp apis add <api>` | An extra Google Cloud API the app uses |
| `gapp apply <file> [--dry-run]` | Replaces all the settings with a file's, all at once |
| `gapp show` | The definition, and every setting a deploy would use with where it comes from |

Inside a clone, these act on the clone's deployment. Anywhere else, add
`--deployment <name>`.

### 5. Let the app say which variables it reads (optional)

An app can list its variables in an `app.json` at the repo's root. It is
Heroku's format, and gapp reads only its `env` block:

```json
{
  "env": {
    "LOG_LEVEL": { "description": "debug, info or warning", "value": "info" },
    "API_URL":   { "description": "Address of the upstream API" },
    "TRACE":     { "description": "Set to 1 to trace requests", "required": false },
    "APP_KEY":   { "description": "Signs session tokens", "generator": "secret" }
  }
}
```

Your settings always win. A deploy reads `app.json` from the commit it
builds, and uses it only for variables you have not set:

| In `app.json` | What the deploy does |
|---|---|
| `value` | Uses it as the default |
| No `value` (and not `"required": false`) | Refuses until you set it: `gapp env set API_URL <value>` |
| `"generator": "secret"` | Refuses until you add it: `gapp env set APP_KEY --secret --generate` |
| `"required": false`, no `value` | Leaves it unset |

The refusal lists every variable that is missing, each with its command.
`gapp deploy --dry-run` shows the same ahead of time. If you set a
variable that the app's `app.json` does not list, the deploy warns that
the app may not read it, and goes ahead.

Nothing in the file names gapp or Google Cloud, so it works on other
hosts too. An app without one is deployed from your settings alone.

### 6. Set the secrets it reads

```bash
gapp secrets list
gapp secrets set api-key
```

`secrets set` prompts for the value, or reads it from stdin with
`--from-stdin`. The value goes from your machine to Secret Manager and
nowhere else. See [Secrets](docs/SECRETS.md).

### 7. Deploy

```bash
gapp deploy --dry-run
gapp deploy
gapp status
```

Run it in a clone of the definition's repo. `deploy` prints the URL.
`status` gives the URL, whether it answers, and the domain's state.

## What a deploy builds

A definition's `ref` is a branch, a tag or a commit of the app's repo.
With none, it is the repo's default branch.

| To | Run |
|---|---|
| Release a version | `gapp set ref v1.5.0`, then deploy |
| Roll back | `gapp set ref v1.4.0`, then deploy |
| Follow a branch | `gapp set ref main`: each deploy builds its tip |

`gapp deploy` refuses when the clone is not on that ref. On a branch, it
builds the commits you have, pushed or not.

## The same app more than once

Test and production are two deployments of one repo:

```bash
gapp add my-app-test --copy-of my-app --project my-test-project --ref develop
```

The copy takes the settings except the domain. Inside the clone, a repo
with several deployments needs `--deployment <name>`; gapp lists them
when you leave it out.

## Where your fleet lives

```bash
gapp fleet
gapp fleet use owner/my-fleet --create
gapp fleet use ~/ops/gapp.yaml
gapp fleet use local
```

By default the fleet is a file on your machine. `gapp fleet use` moves it
to a git repo, as `gapp.yaml` at its root. Each change is then one
commit, pushed with the git access your machine has, and every machine
that uses that repo sees the same fleet. The repo can be on any git host.
[Why a git repo](docs/FLEET-IN-GIT.md). It can also move the fleet to a
file you name, by its full path ending in `.yaml` or `.yml`.

The `GAPP_FLEET` environment variable names the fleet for one process,
whatever the profile says. It takes what `gapp fleet use` takes, and
suits a machine you don't keep: a container, a cloud session, a runner.

```bash
GAPP_FLEET=owner/my-fleet gapp list
GAPP_FLEET=/tmp/try/gapp.yaml gapp add my-app --project my-project
```

Each change to a fleet kept in a file keeps the copy it replaced, in
`~/.local/state/gapp/backups/`. A backup is a fleet file `gapp fleet
import` takes as it is.

`gapp fleet export [<file>]` copies the fleet out, and `gapp fleet import
<file>` replaces it, keeping the one it replaced.

## Custom domains

```bash
gapp set domain app.example.com
gapp deploy
gapp status
```

`status` shows the CNAME record to add. Cloud Run maps a domain only for
an identity that has verified it, so verify once: `gcloud domains verify
example.com`. For deploys on a runner, also add the project's
`gapp-deploy` service account as a verified owner in Search Console.

A deploy that would replace or remove a live domain is refused unless you
pass `--replace-domain`, because the old domain stops serving.

## Upgrading gapp

A definition records the gapp version that wrote it, and a deployment
records the version that last deployed it. A newer gapp changes nothing
on its own:

```bash
gapp upgrade --deployment my-app
gapp deploy --deployment my-app --upgrade
```

`gapp upgrade` brings the definition to the installed gapp's version and
prints what will change. The next deploy applies it, and must be given
`--upgrade` when an earlier gapp has deployed it; `gapp upgrade` says
which, from the deploy record when it can read it. `gapp list` and `gapp show` say when an upgrade is available.

## Deploying on GitHub Actions

`gapp ci` is optional. It runs the same deploy on a runner, so the
machine that asks needs only `gh`. It works on a fleet kept in a GitHub
repo.

```bash
gapp fleet use owner/my-fleet --create
gapp ci init
gapp ci sources add owner
gapp ci projects connect my-project
gapp ci deploy --deployment my-app
gapp ci status --deployment my-app --live
```

| Command | Does | Needs |
|---|---|---|
| `gapp ci init` | Installs two workflows in the fleet repo | `gh` as an admin of it |
| `gapp ci sources add <owner>` | Lets runners deploy that owner's repos | `gh` |
| `gapp ci projects connect <project>` | Lets the fleet repo's runners sign in to the project. Once per project, after `gapp project setup` | gcloud as an Owner; `gh` as an admin |
| `gapp ci projects list` | The projects runners can deploy to | `gh` |
| `gapp ci deploy [--upgrade] [--no-watch]` | Deploys on a runner and reports what the run said | `gh` |
| `gapp ci status [--live]` | The last deploy run. `--live` asks the project what is running | `gh` |

Details, private app repos and the security model: [CI](docs/CI.md).

## More than one fleet or login

A profile is a separate fleet and a separate Google Cloud login on one
machine, for example work and personal.

```bash
gapp config profile add work
gapp config profile use work
gapp config account me@example.com
gapp config profile list
```

## Known limits

- A variable you set is always passed, whether or not the app still
  reads it. gapp can warn only when the app's `app.json` lists its
  variables.
- `gapp secrets list` shows a secret's state in Secret Manager, not
  whether the running service has it: a value set after the last deploy
  reaches the app at the next one.

- One deployment is one Cloud Run service.
- A deployment's region is `us-central1`.
- `gapp remove <name>` takes a deployment out of your fleet and leaves
  what it deployed in Google Cloud. Nothing takes those resources down,
  and a deployment of the same name added later on that project uses
  them again.
- `gapp ci` works with a fleet repo on github.com.

## For agents

`gapp-mcp` serves every command as an MCP tool with the same arguments.
A refusal comes back as data and changes nothing. The
[gapp skill](skills/gapp/SKILL.md) has the step-by-step workflows; it is
optional, and this README is self-sufficient.

## Further reading

- [The interface](docs/INTERFACE.md): every command, file and MCP tool
- [Secrets](docs/SECRETS.md): how secrets are named, owned and recovered
- [CI](docs/CI.md): deploying on GitHub Actions
- [Access](docs/ACCESS.md): what each command needs on your machine
- [Contributing](CONTRIBUTING.md): architecture and design
