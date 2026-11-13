# gapp — GCP App Deployer

`gapp` is a professional-grade CLI for deploying containerized applications to Google Cloud Run. It is designed to solve the complexity of managing "Infrastructure Sprawl" across multiple GCP projects while enabling perfectly portable, "Zero-Fork" deployments from public repositories.

---

## Why gapp, and when plain gcloud is enough

`gcloud run deploy --source .` builds and runs a container in one command, and for one service you deploy by hand it may be all you need. gapp adds what that command leaves to you:

- **Declared state.** Each deployment's service, IAM, secrets wiring, volume and domain mapping are applied with Terraform from state kept in the deployment's own bucket, so a redeploy changes only what changed, and from any machine.
- **Project foundation.** `gapp setup` enables the APIs and creates the bucket and labels once per project, idempotently.
- **Secrets lifecycle.** Secrets are declared in gapp.yaml, generated or set with `gapp secrets`, scoped to the deployment and checked before deploy.
- **A data volume.** Each deployment gets a bucket mounted at a fixed path, so state survives redeploys.
- **Domain guardrails.** A deploy that would replace or remove a live custom domain stops before anything is built.
- **The CI fleet.** `gapp ci` deploys from GitHub Actions with no gcloud on the machine that asks, including from a cloud agent session.
- **Agent tools.** Every command is also an MCP tool returning the same result.
- **Everything in one list.** `gapp list` shows every deployment on every project marked `gapp=hosted`.

Plain gcloud is enough when there's one service, deployed by hand, with no secrets, data or custom domain to keep in step.

---

## 🎯 The Problems We Solve

### **1. Eliminating "Infrastructure Sprawl"**
As your footprint grows across dozens of GCP projects, remembering exactly where you deployed a specific app—and which service account, bucket, and state file it uses—becomes a nightmare. `gapp` solves this by moving the "Registry" into the Cloud itself. You never have to track where things live; `gapp` discovers them for you.

### **2. The "Zero-Fork" Public Repo**
Your repo describes the app, not where it runs. A repo deployed with `gapp` says what the app needs: how it starts (a Procfile or Dockerfile) and its settings in `gapp.yaml` (public access, environment variables, which secrets it reads). Through CI, even that can live outside the repo: the fleet's manifest for a deployment holds the settings and the start command, so you can deploy a repo whose author never added either. It never says where it runs: no project id, region or bucket name, and no secret values. Those come from whoever deploys it, so anyone can deploy the same repo to their own Google Cloud project without changing it.

### **3. Stateful Portability**
Because `gapp` calculates infrastructure locations deterministically, you can switch machines or users and `gapp` will automatically "re-attach" to the same buckets and data. You get durable, stateful deployments from nothing but a repo and a `gcloud` login.

---

## 🚀 The Gapp Journey

### **1. Your First Deployment**
Get your app live in a few steps.
```bash
gapp init                          # Scaffold the local manifest (gapp.yaml)
gapp manifest show                 # See its settings; gapp manifest set / env set change them
gapp setup --project <project-id>  # Prep the project and register it as home
gapp deploy                        # Build and push your first version live
```

**How gapp starts your app.** gapp starts your app from the first of these it finds:

- **A Procfile** with a `web:` line: the command that starts the app and serves HTTP on `$PORT`. gapp builds the image from its own template, installs the app's declared dependencies, and runs that command.
  ```bash
  echo 'web: my-app serve' > Procfile
  echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile
  ```
- **A Dockerfile**, which gapp builds as is.
- **`start` in gapp.yaml** (`gapp manifest set start 'my-app serve'`), only for a repo with neither file. It's meant for a repo you can't change, set in the fleet's manifest for a CI deployment; a repo you own should use a Procfile, which other tools read too.

The Dockerfile wins over the Procfile, and the Procfile over `start`; `gapp init` and `status` say which one is used and which are ignored. With none, `gapp init`, `status` and `setup` say the app isn't deployable yet, and `gapp deploy` refuses before building.

**Configuring the deployment: gapp.yaml.** Everything else about the deployment is in the app's `gapp.yaml`, and the `gapp manifest` commands read and change it, so nothing needs editing by hand:

```bash
gapp manifest show                                  # this repo's settings, each with what it means
gapp manifest set public true                       # also: name, domain, start, memory, cpu, max-instances
gapp manifest unset domain
gapp manifest env set LOG_LEVEL INFO                # a plain value
gapp manifest env set API_KEY --secret api-key      # read from a secret; then gapp secrets set api-key
gapp manifest env set SIGNING_KEY --secret signing-key --generate   # gapp generates the value
gapp manifest env set APP_USERS_PATH --data-path users              # the data volume, kept across deploys
gapp manifest env unset LOG_LEVEL
gapp manifest example                               # the annotated file below
gapp manifest schema                                # the JSON Schema, for tools
```

Each change is checked against the schema and written only when the file changes. `gapp manifest` on its own lists these commands. Two advanced keys have no command and are edited in gapp.yaml at the root of the app's repo: `paths`, for a repo with several services, and `prerequisites.apis`, for extra GCP APIs. The example below shows their format, and every gapp command checks the file.

**The name.** `gapp init` writes the deployment's name into gapp.yaml: the origin repo's name, else the folder's, unless gapp.yaml already has one. It prints the name and where it came from. Writing it keeps the name the same in any clone, including one with no origin remote. The name keys the Cloud Run service, the data bucket and the secrets, so a different name is a new, empty deployment while the old one keeps its URL and data. That's why `gapp manifest set name` refuses a different name unless you pass `--new-deployment`. CI deploys don't use this name: a fleet deployment's id names its resources.

**When gapp.yaml is broken.** If gapp.yaml isn't valid YAML, or has a setting gapp doesn't accept, every command that reads it stops with the same message: the line and column, or each key and what's wrong with it. Nothing changes the file. There are two ways out:

- Fix the lines it names.
- Start over: `gapp init --fresh` moves the file to `gapp.yaml.bak` (or `.bak.1`, `.bak.2`, ...) and writes a new one holding only the name, then you set what you need with `gapp manifest`. It keeps the old file's name when it can read it, and says so when it can't. It asks nothing: the flag is the instruction, and nothing is deleted.

Every key gapp.yaml takes, as `gapp manifest example` prints it:

```yaml
# gapp.yaml: every key is optional.
# An app starts from its Dockerfile, else its Procfile's web: line, else start below.

# The deployment's name. It names the Cloud Run service, the data bucket and the secrets, so changing it makes a new deployment. Unset: the origin repo's name, else the folder's.
name: my-app

# The command that starts the app and serves HTTP on $PORT, used only when the repo has no Dockerfile or Procfile. For a repo you can't change; a repo you own should use a Procfile.
# start: my-app serve

# true: anyone can call the URL, and the app does its own auth. Unset or false: only callers with Cloud Run invoker access.
public: true

# Custom subdomain mapped at deploy (CNAME it to ghs.googlehosted.com). Unset: the .run.app URL only.
domain: app.example.com

# Environment variables the service gets.
env:
  # The variable's name, as the app reads it.
  - name: LOG_LEVEL
    # A plain value. {{SOLUTION_DATA_PATH}} is the data volume's mount path and {{SOLUTION_NAME}} the app's name. Not with 'secret'.
    value: INFO
  - name: APP_DATA
    value: "{{SOLUTION_DATA_PATH}}/data"
  - name: API_KEY
    # Take the value from a secret: a mapping with 'name' (and optional 'generate'). Not with 'value'.
    secret:
      # Short secret name, stored as '<app name>-<this name>' in Secret Manager. Set its value with `gapp secrets set <this name>`.
      name: api-key
  - name: SIGNING_KEY
    secret:
      name: signing-key
      # true: gapp generates a random value on the first deploy if none is set.
      generate: true

# Cloud Run sizing for each instance.
service:
  # Memory per instance (default 512Mi).
  memory: 1Gi
  # CPUs per instance (default 1).
  cpu: "1"
  # Most instances Cloud Run runs at once (default 1).
  max_instances: 2

# Extra GCP APIs to enable at setup.
prerequisites:
  # GCP APIs `gapp setup` enables besides the ones gapp needs.
  apis:
    - firestore.googleapis.com

# Subfolders that are each their own service, for a repo with several. Each subfolder has its own gapp.yaml.
# paths:
#   - services/api
#   - services/worker
```

### **2. Redeploying Later**
Iterate on your code without ever mentioning the project ID again.
```bash
# Make changes to your code...
git add . && git commit -m "add new feature"
gapp deploy                        # Finds its registered home automatically
```

### **3. Switching Machines (Zero-Config Portability)**
Move to a new laptop and pick up right where you left off.
```bash
gcloud auth login                  # Authenticate with Google
git clone <your-repo>              # Get your source code
gapp deploy                        # Discovers home in the cloud and "Just Works"
```

### **4. The Same App on Several Projects**
Deploy the same app to separate projects, for example one for testing and one for production. Each is its own deployment: the same name, with its own project, config and secrets.
```bash
gapp setup <test-project-id>
gapp deploy --project <test-project-id>

gapp setup <prod-project-id>
gapp deploy --project <prod-project-id>
```
When a name is on more than one project, `gapp deploy` and `gapp status` need `--project`; without it they stop and say so, and `gapp list` shows where each one is.

### **What names a deployment, and the limits**

- **Direct (`gapp setup`, `deploy`, `status`):** a deployment is named by the `name` in the app's `gapp.yaml` (else the repo's name). There is one deployment of an app per GCP project. Test and production are separate projects, chosen with `--project`. Two deployments of one app in the same project aren't possible from the app's folder; the CI system can do it with `--copy-of`.
- **CI (`gapp ci …`):** a deployment is named by its fleet entry's name. Its id, which names its GCP resources, is fixed at setup; `gapp ci rename` changes only the name. To deploy one repo twice, use `gapp ci setup <new> --copy-of <existing>`. A plain `gapp ci setup <new>` for a repo that's already set up is refused on purpose, so a repo isn't set up twice by accident.
- **A name on two projects:** `gapp status` and `gapp deploy` refuse without `--project` rather than guess. `gapp list` shows one row per project.
- **No environments:** a deployment and its project replace them ([why](CONTRIBUTING.md#no-environments)).
- **A fleet entry** can carry a manifest: gapp.yaml settings that the deploy merges over the app repo's `gapp.yaml` (settings key by key, env variables by name), or uses as the whole file when the repo has none. `--domain` sets just the domain; `--manifest FILE` sets the whole manifest, replacing what was there. If `gapp ci status` reports keys gapp.yaml doesn't have, re-running `gapp ci setup <name> --repo <repo> --project <project> --update` removes them. Any change to an existing entry needs `--update`; without it, setup shows what would change and changes nothing. You shouldn't need to edit `gapp/deployments.yaml` yourself.
- **`gapp ci status`** says what's deployed, where, and the last deploy. It doesn't check project trust; the next deploy run does, and names `gapp ci project setup <project>` if it's missing.
- **Allowed sources:** `gapp ci sources` reads and changes a repository variable of the fleet repo. That needs a machine with full GitHub API access; a sandboxed agent session may not reach the variables API.

Known limits:
- A deploy doesn't record which repo or commit it came from, so gapp can't warn before replacing a deployment built from a different repo.
- There's no plan-only step that previews infrastructure changes before a deploy applies them.
- A CI deploy run whose app repo has a broken gapp.yaml fails with the message above; fix it in the app's repo.
- Direct deploys (`gapp deploy`) use the repo's own `gapp.yaml` only. To give one deployment different settings without changing the repo, deploy it through CI with a fleet manifest.
- gapp 4 doesn't see deployments made by gapp 3 until each is redeployed once with gapp 4.

### **5. Teams and Shared Organizations**
A deployment's name is unique within its GCP project. Teams that must not touch each other's deployments use separate projects (and IAM on them), or separate fleets; gapp has no owner namespaces.

### **6. Audit & Recovery**
Use this when it's been a while and you have no idea where your apps are.
```bash
# Every deployment on every project marked gapp=hosted
gapp list

# Result:
#   App                  Project              Contract
#   app-a                project-123          v-4
#   app-a                project-456          v-4
#   app-b                project-456          v-4
```

### **7. Deploy From Anywhere (CI/CD)**
gapp has **two fleet systems, kept apart**. The root commands (`gapp setup`, `deploy`, `status`, `list`, `secrets`) deploy the local checkout to GCP with gcloud. The `gapp ci` commands manage your **fleet** through GitHub only: they never use gcloud (except `gapp ci project setup`) and never read the working directory. What each command needs on your machine, and who touches Google Cloud and GitHub: [Access](docs/ACCESS.md). The fleet is kept in a private **fleet repo**: `gapp/deployments.yaml`, one entry per **deployment** (a repo deployed to a project under a name you choose; the same repo deployed twice is two deployments), plus gapp's three workflows (deploy, register, status). The `ci` group mirrors the root verbs.

**From nothing to first deploy:**

```bash
gapp ci init                                     # finds your fleet repo, or says how to create one
gapp ci init <owner>/<fleet-repo> --create       # creates a private fleet repo and sets it for this machine
gapp ci sources add <owner>                      # allows the GitHub owner whose repos you deploy
gapp ci project setup <project-id>               # once per GCP project: gcloud as an Owner, gh as an admin
gapp ci setup my-app --repo <owner>/my-app --project <project-id> --domain app.example.com
gapp ci deploy my-app                            # any time, from any machine with gh
```

Each step's output names the next. `gapp ci status my-app` is one deployment (project, domain, last deploy; GitHub only, fast); `gapp ci status my-app --live` asks the project for the live service state (service up, URL, domain) through a read-only status workflow that runs `gapp status` on a runner with the project's deploy identity; `gapp ci list` is the fleet. `--verbose` adds the fleet repo, its files and variables, and ids; `--json` on any `ci` command prints the result dict (the same one the MCP tool returns) and nothing else.

| Command | Does | Needs |
|---|---|---|
| `gapp ci init [<owner>/<repo>] [--create] [--owner <org>]` | shows, finds, sets or creates your fleet repo; the only command that sets it | `gh` |
| `gapp ci sources add\|remove\|list <owner>` | the GitHub owners whose repos may be deployed from your fleet | `gh` write |
| `gapp ci project setup <project>` | once per GCP project: trust between the fleet repo and the project | gcloud Owner, `gh` admin |
| `gapp ci setup [<name>] --repo <owner>/<repo> --project <p> [--domain <d>]` | adds or updates a deployment; the name defaults to the repo's name | `gh` dispatch |
| `gapp ci setup <name> --copy-of <existing> [--project <p>]` | a new deployment from an existing one (an environment, a second instance) | `gh` dispatch |
| `gapp ci manifest <name>`, `gapp ci setup <name> ... --manifest FILE --update` | prints, and replaces, the deployment's fleet-side manifest | `gh` read; `gh` dispatch |
| `gapp ci manifest <name> --merged [--ref REF]` | prints what a deploy at REF would use: the manifest merged over the app repo's gapp.yaml | `gh` read of the fleet repo and the app repo |
| `gapp ci rename <old> <new>` | changes the name only; the GCP resources stay | `gh` dispatch |
| `gapp ci deploy <name> [--ref <r>]` | deploys it through GitHub Actions | `gh` dispatch |
| `gapp ci status <name> [--live]`, `gapp ci list [--repo <owner>/<repo>]` | where things stand; `--live` asks the project (a runner start) | `gh` read; `--live` dispatch |

`--repo` always means the deployed app's repo, never the fleet repo.

**From a cloud session** (no gcloud, no GitHub API write, just a `gh` that can dispatch): `gapp ci setup`, `gapp ci deploy`, `gapp ci status` and `gapp ci list` are the whole story. `ci setup` dispatches the register run, which validates and writes the entry with its own token; `ci deploy` dispatches the deploy workflow with the deployment's name. The two one-time steps that need more are told to you verbatim when they are missing:

```
<owner>/<repo> can't be set up yet.
Fleet repo <fleet-owner>/<fleet-repo> is not yet trusted to deploy to GCP project <project>. One-time setup:

1. Run `gapp ci project setup <project>`
   - Requires gcloud signed in as an Owner of <project>
   - Requires `gh` signed in as an admin of <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

```
<owner>/<repo> can't be set up yet.
Apps owned by <owner> aren't allowed in your fleet yet (fleet repo <fleet-owner>/<fleet-repo>). One-time setup:

1. Run `gapp ci sources add <owner>`
   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo <fleet-owner>/<fleet-repo> --body <current-list>,<owner>
   - Requires `gh` signed in with write access to <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

Every `gapp ci` command ends the same way: it did the thing, or it prints a plain sentence with the exact fix (no stack traces, no raw errors). Something is called missing only when GitHub confirms it; a read gapp could not make says "Couldn't check ..." in GitHub's own words. Without `gh`, press **Run workflow** on the fleet repo's Actions tab instead. The runner authenticates with Workload Identity Federation, so no key is stored anywhere.

**The entry names the deployment.** `repo` and `project` are the entry's own fields, and it can carry a manifest for the deployment, so a public repo keeps its real domain out of the repo, one repo's deployments can differ, and a repo you can't change deploys with no fork.

**Deploying a repo you don't control.** Build the deployment's manifest with the same `gapp manifest` commands, pointed at a file, then upload it:

```bash
gapp manifest --file thing.yaml set start 'thing serve'        # no Procfile or Dockerfile in the repo
gapp manifest --file thing.yaml set public true
gapp manifest --file thing.yaml env set API_KEY --secret api-key
gapp ci setup thing --repo someone/thing --project my-project --manifest thing.yaml
gapp ci deploy thing
```

To see what the deploy will use before running it: `gapp ci manifest thing --merged` (add `--ref <tag>` for a version other than the default branch). To change it later: `gapp ci manifest thing > thing.yaml`, edit it the same way, and upload it with `--manifest thing.yaml --update`. The upload replaces the deployment's manifest whole, so start from the current one. A manifest has no `name`: the deployment's id names it. Private repos work too: install a read-only GitHub App on the organization that holds them and store its id and key in the fleet repo under the fixed names `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY`; `gapp ci init --verbose` reports them. Design notes and the security model: [CI/CD setup](docs/CI.md).

### **8. Custom Domains**
Map a subdomain to the service with `domain:` in `gapp.yaml` (or `gapp ci setup <name> --repo <owner>/<repo> --project <project> --domain <host>` for fleet deploys), deploy, and add the CNAME `gapp status` shows. Cloud Run only maps a domain for an identity that has verified it, so verify once per domain (`gcloud domains verify example.com`, which opens Search Console; for CI deploys, add the `gapp-deploy` service account as a verified owner there too). Deploys guard the live mapping: one that would replace or remove it is refused unless you pass `gapp deploy --replace-domain`, because Terraform destroys the old mapping before creating the new one.

### **9. Expert Mode (Explicit Management)**
Use this when you use a configuration repository or external fleet management tool to track environment locations. In this mode, `gapp` acts as a **stateless deployment tool** where commands leverage only the local `gapp.yaml` and explicit command-line overrides.
```bash
# Turn off the cloud search registry
gapp config discovery off

# Manually target projects with every command
gapp deploy --project <project-id>
```

---

## 🛠 Command Reference

### **Configuration (`gapp config`)**
*   `gapp config profile <name>`: Switch workstation profiles (e.g., `work`, `personal`).
*   `gapp config account <email>`: Set the gcloud account used for operations.
*   `gapp config discovery [on|off]`: Toggle the cloud registry search.

> **Note on Enterprise Usage**: `gapp` follows your `gcloud` login wherever you go. In a large organization, keep teams apart with GCP projects and IAM; switch between work and personal contexts with `gapp config profile`.

### **Development**
*   `gapp setup --project <id>`: Enable the APIs, create and label the deployment's bucket, and mark the project hosted. Runs in the app's checkout; `--project` is required the first time.
*   `gapp deploy [--ref <ref>] [--project <id>] [--rebuild] [--replace-domain]`: Build with Cloud Build and apply Terraform. `--replace-domain` is required for a deploy that would replace or remove the live custom domain mapping.
*   `gapp status [<name>]`: Check infrastructure health, service URLs, and the custom domain's state (`pending_dns`, `pending_cert`, `active`, `error`). Reports the direct system only; with no usable Google Cloud identity it says so and names the login command instead of failing.
*   `gapp secrets list [<name>]`: Show declared secrets, deploy-readiness, and remediation hints. See [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) for the full label model and conflict-resolution guide.
*   `gapp secrets get <secret-name>`: Fetch a secret value or its hash + length.
*   `gapp secrets set <secret-name> [<value>]`: Store secrets in GCP Secret Manager.
*   `gapp init [--fresh]`: Write gapp.yaml with the deployment's name and report how the app starts. `--fresh` starts over, keeping the current file as `gapp.yaml.bak`.
*   `gapp manifest [--file <file>] show [--json]`: This repo's gapp.yaml settings, set or not, each with what it means. `--file` (on every `gapp manifest` command) works on another gapp.yaml-shaped file instead, such as a deployment's fleet-side manifest.
*   `gapp manifest set <key> <value>` / `unset <key>`: Change `name`, `public`, `domain`, `start`, `memory`, `cpu` or `max-instances`. Changing the name needs `--new-deployment`.
*   `gapp manifest env set <NAME> <value>` (or `--secret <secret> [--generate]`, or `--data-path [<subfolder>]`) / `env unset <NAME>`: The service's environment variables.
*   `gapp manifest example`: An annotated gapp.yaml with every key (valid YAML, nothing else).
*   `gapp manifest schema`: The JSON Schema for `gapp.yaml` (nothing else on stdout).

### **CI/CD (`gapp ci`)**
*   `gapp ci init [<owner>/<repo>] [--create] [--owner <org>]`: Show, find, set or create your fleet repo; the only command that sets it. Setting an existing repo installs or updates gapp's three workflows (deploy, register, status) and creates `gapp/deployments.yaml` (never overwritten); `--create` makes a new private, tagged fleet repo first.
*   `gapp ci sources add|remove|list <owner>`: The GitHub owners whose repos may be deployed from your fleet (the fleet repo variable `GAPP_ALLOWED_SOURCES`).
*   `gapp ci project setup <project>`: Make your fleet trusted to deploy to a GCP project (once per project, idempotent). The only `ci` command that touches GCP: enables the APIs a deployment needs and grants Cloud Build its permissions (so a project used only through CI needs no `gapp setup` from your machine), creates or confirms the WIF pool, provider and `gapp-deploy` service account, binds the pool to the fleet repo, and records them as the fleet repo's GitHub Environment named after the project. Requires gcloud signed in as an Owner of the project and `gh` signed in as an admin of the fleet repo.
*   `gapp ci manifest <name>`: Print the deployment's fleet-side manifest as YAML, and nothing else; one read of the fleet repo.
*   `gapp ci manifest <name> --merged [--ref REF]`: Print what a deploy at REF (default: the app repo's default branch) would use: the manifest merged over the app repo's gapp.yaml, how the app starts, and on stderr what the deploy would refuse. Reads the app repo with your own GitHub login and saves nothing. The deploy run reads it with the fleet's own access and runs the gapp version the fleet is pinned to; a repo you can't read here can still deploy, and a version difference is noted.
*   `gapp ci setup [<name>] --repo <owner>/<repo> --project <project> [--domain <host>] [--manifest <file>] [--copy-of <existing>] [--update] [--no-watch]`: Add or update a deployment (the name defaults to the repo's name; changing an existing deployment's entry needs `--update`, and without it setup shows what would change). Never uses gcloud; needs only a `gh` that can dispatch workflows. A repo already deployed under another name is refused: update that one, or add another deployment of it with `--copy-of`.
*   `gapp ci rename <old> <new>`: Rename a deployment; its id and GCP resources are unchanged.
*   `gapp ci deploy <name> [--ref <ref>] [--no-watch]`: Deploy one deployment through the fleet's deploy workflow and stream the run. Without `--ref` it deploys the app repo's default branch, whatever it is named.
*   `gapp ci status <name> [--live]`: One deployment: repo, project, domain, last deploy, and the next step if a deploy cannot run (GitHub only). `--live` dispatches the fleet's read-only status workflow, which runs `gapp status` for the deployment on a runner with the project's deploy identity and returns the service state. Nothing is stored in the fleet repo.
*   `gapp ci list [--repo <owner>/<repo>] [--verbose]`: The fleet's deployments, optionally one repo's; `--verbose` adds the fleet repo's files and variables.
*   Every `gapp ci` command takes `--verbose` (fleet detail) and `--json` (the result dict only, as the MCP tool returns it; exit code unchanged).

---

## Further reading

*   [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) — how gapp manages Secret Manager, the label model, and what to do when statuses go sideways (`unattached`, `conflict`, `orphan`).
*   [CI/CD setup](docs/CI.md) — wiring GitHub Actions deployments via Workload Identity Federation.
*   [Access](docs/ACCESS.md) — what each command needs on your machine, and whether you or a runner touches Google Cloud and GitHub.
*   [Contributing](CONTRIBUTING.md) — architecture, design principles, conventions for code, comments, and examples.
