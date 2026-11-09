# gapp — GCP App Deployer

`gapp` is a professional-grade CLI for deploying containerized applications to Google Cloud Run. It is designed to solve the complexity of managing "Infrastructure Sprawl" across multiple GCP projects while enabling perfectly portable, "Zero-Fork" deployments from public repositories.

---

## 🎯 The Problems We Solve

### **1. Eliminating "Infrastructure Sprawl"**
As your footprint grows across dozens of GCP projects, remembering exactly where you deployed a specific app—and which service account, bucket, and state file it uses—becomes a nightmare. `gapp` solves this by moving the "Registry" into the Cloud itself. You never have to track where things live; `gapp` discovers them for you.

### **2. The "Zero-Fork" Public Repo**
Traditional deployment tools often require you to hardcode project IDs, regions, or bucket names in your repository. `gapp` allows a repo to remain entirely **deployment-agnostic**. Anyone can clone a public, `gapp`-enabled repo and deploy it to their own isolated environment without modifying a single line of code or setting up complex local env vars.

### **3. Stateful Portability**
Because `gapp` calculates infrastructure locations deterministically, you can switch machines or users and `gapp` will automatically "re-attach" to the same buckets and data. You get durable, stateful deployments from nothing but a repo and a `gcloud` login.

---

## 🚀 The Gapp Journey

### **1. Your First Deployment**
Get your app live in three simple steps.
```bash
gapp init                          # Scaffold the local manifest (gapp.yaml)
gapp setup <project-id>            # Prep the project and register it as home
gapp deploy                        # Build and push your first version live
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
gapp has **two fleet systems, kept apart**. The root commands (`gapp setup`, `deploy`, `status`, `list`, `secrets`) deploy the local checkout to GCP with gcloud. The `gapp ci` commands manage your **fleet** through GitHub only: they never use gcloud (except `gapp ci project setup`) and never read the working directory. The fleet is kept in a private **fleet repo**: `gapp/deployments.yaml`, one entry per **deployment** (a repo deployed to a project under a name you choose; the same repo deployed twice is two deployments), plus gapp's three workflows (deploy, register, status). The `ci` group mirrors the root verbs.

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
| `gapp ci setup [<name>] --repo <owner>/<repo> --project <p> [--domain <d>] [--config <file>]` | adds or updates a deployment; the name defaults to the repo's name | `gh` dispatch |
| `gapp ci setup <name> --copy-of <existing> [--project <p>]` | a new deployment from an existing one (an environment, a second instance) | `gh` dispatch |
| `gapp ci rename <old> <new>` | changes the name only; the GCP resources stay | `gh` dispatch |
| `gapp ci deploy <name> [--ref <r>]` | deploys it through GitHub Actions | `gh` dispatch |
| `gapp ci status <name> [--live]`, `gapp ci list [--repo <owner>/<repo>]` | where things stand; `--live` asks the project (a runner start) | `gh` read; `--live` dispatch |

`--repo` always means the deployed app's repo, never the fleet repo.

**From a cloud session** (no gcloud, no GitHub API write, just a `gh` that can dispatch): `gapp ci setup`, `gapp ci deploy`, `gapp ci status` and `gapp ci list` are the whole story. `ci setup` dispatches the register run, which validates and writes the entry with its own token; `ci deploy` dispatches the deploy workflow with the deployment's name. The two one-time steps that need more are told to you verbatim when they are missing:

```
<owner>/<repo> can't be set up yet: your fleet isn't trusted to deploy to <project>.
Fleet repo <fleet-owner>/<fleet-repo> is not yet trusted to deploy to GCP project <project>. One-time setup:

1. Run `gapp ci project setup <project>`
   - Requires gcloud signed in as an Owner of <project>
   - Requires `gh` signed in as an admin of <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

```
<owner>/<repo> can't be set up yet: apps owned by <owner> aren't allowed in your fleet.
Apps owned by <owner> aren't allowed in your fleet yet (fleet repo <fleet-owner>/<fleet-repo>). One-time setup:

1. Run `gapp ci sources add <owner>`
   - Or: gh variable set GAPP_ALLOWED_SOURCES --repo <fleet-owner>/<fleet-repo> --body <current-list>,<owner>
   - Requires `gh` signed in with write access to <fleet-owner>/<fleet-repo>
2. Run this command again: gapp ci setup <name> --repo <owner>/<repo> --project <project>
```

Every `gapp ci` command ends the same way: it did the thing, or it prints a plain sentence with the exact fix (no stack traces, no raw errors). Something is called missing only when GitHub confirms it; a read gapp could not make says "Couldn't check ..." in GitHub's own words. Without `gh`, press **Run workflow** on the fleet repo's Actions tab instead. The runner authenticates with Workload Identity Federation, so no key is stored anywhere.

**The entry is the deployment's configuration.** `repo` and `project` are the entry's own fields; every other key is a gapp.yaml key (`domain`, `env`, `service`, ...) merged over the repo's `gapp.yaml` at deploy time, or written as the whole file when the repo has none, so deployment values never live in the deployed repo and a repo that runs on Cloud Run can be deployed without knowing about gapp. `--domain` sets one key, `--config <file>` any. Private repos work too: install a read-only GitHub App on the organization that holds them and store its id and key in the fleet repo under the fixed names `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY`; `gapp ci init --verbose` reports them. Design notes and the security model: [CI/CD setup](docs/CI.md).

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
*   `gapp deploy [--ref <ref>] [--project <id>] [--rebuild] [--replace-domain]`: Build with Cloud Build and apply Terraform. `--replace-domain` is required for a deploy that would replace or remove the live custom domain mapping.
*   `gapp status`: Check infrastructure health, service URLs, and the custom domain's state (`pending_dns`, `pending_cert`, `active`, `error`). Reports the direct system only; with no usable Google Cloud identity it says so and names the login command instead of failing.
*   `gapp secrets list`: Show declared secrets, deploy-readiness, and remediation hints. See [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) for the full label model and conflict-resolution guide.
*   `gapp secrets get <name>`: Fetch a secret value or its hash + length.
*   `gapp secrets set <name> [<value>]`: Store secrets in GCP Secret Manager.
*   `gapp manifest schema`: Print the live JSON Schema for `gapp.yaml`.

### **CI/CD (`gapp ci`)**
*   `gapp ci init [<owner>/<repo>] [--create] [--owner <org>]`: Show, find, set or create your fleet repo; the only command that sets it. Setting an existing repo installs or updates gapp's three workflows (deploy, register, status) and creates `gapp/deployments.yaml` (never overwritten); `--create` makes a new private, tagged fleet repo first.
*   `gapp ci sources add|remove|list <owner>`: The GitHub owners whose repos may be deployed from your fleet (the fleet repo variable `GAPP_ALLOWED_SOURCES`).
*   `gapp ci project setup <project>`: Make your fleet trusted to deploy to a GCP project (once per project, idempotent). The only `ci` command that touches GCP: creates or confirms the WIF pool, provider and `gapp-deploy` service account, binds the pool to the fleet repo, and records them as the fleet repo's GitHub Environment named after the project. Requires gcloud signed in as an Owner of the project and `gh` signed in as an admin of the fleet repo.
*   `gapp ci setup [<name>] --repo <owner>/<repo> --project <project> [--domain <host>] [--config <file>] [--copy-of <existing>] [--no-watch]`: Add or update a deployment (the name defaults to the repo's name; the same name again updates its gapp.yaml keys). Never uses gcloud; needs only a `gh` that can dispatch workflows. A repo already deployed under another name is refused: update that one, or add another deployment of it with `--copy-of`.
*   `gapp ci rename <old> <new>`: Rename a deployment; its id and GCP resources are unchanged.
*   `gapp ci deploy <name> [--ref <ref>] [--no-watch]`: Deploy one deployment through the fleet's deploy workflow and stream the run.
*   `gapp ci status <name> [--live]`: One deployment: repo, project, domain, last deploy, and the next step if a deploy cannot run (GitHub only). `--live` dispatches the fleet's read-only status workflow, which runs `gapp status` for the deployment on a runner with the project's deploy identity and returns the service state. Nothing is stored in the fleet repo.
*   `gapp ci list [--repo <owner>/<repo>] [--verbose]`: The fleet's deployments, optionally one repo's; `--verbose` adds the fleet repo's files and variables.
*   Every `gapp ci` command takes `--verbose` (fleet detail) and `--json` (the result dict only, as the MCP tool returns it; exit code unchanged).

---

## Further reading

*   [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) — how gapp manages Secret Manager, the label model, and what to do when statuses go sideways (`unattached`, `conflict`, `orphan`).
*   [CI/CD setup](docs/CI.md) — wiring GitHub Actions deployments via Workload Identity Federation.
*   [Contributing](CONTRIBUTING.md) — architecture, design principles, conventions for code, comments, and examples.
