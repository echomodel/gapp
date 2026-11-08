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

### **4. Project env binding**
Bind a GCP project to a named env so you can address it by `--env` later.
```bash
# Mark a project as your prod environment
gapp projects set-env <project-id> prod

# Setup deploys the solution onto a project that's already bound to prod
gapp init
gapp setup --project <project-id>
gapp deploy
```

### **5. Managing Multiple Environments**
Deploy the same app to different projects for `dev`, `staging`, or `prod`.
```bash
# Bind the production project to env=prod (one-time)
gapp projects set-env <prod-project-id> prod

# Setup the solution there
gapp setup --project <prod-project-id>

# Deploy — gapp finds the only project hosting this solution
gapp deploy

# Or, when the same solution is on multiple projects, disambiguate with --env
gapp deploy --env prod
```
`gapp` ensures you never accidentally deploy to the wrong env: when `--env` is given, it verifies the resolved project's `gapp-env` label matches before applying.

### **6. Team & Enterprise Isolation (Namespacing)**
Work in shared organization projects without colliding with colleagues. By setting an **Owner**, you create an isolated namespace for your apps.

```bash
# View all global deployments
gapp list --all

# Set your workstation owner
gapp config owner owner-a

# Now 'list' only shows your apps
gapp list

# Try to setup an app that someone else already has on the same project
gapp setup --project <pid>
# FAILS (Layer-1 cross-owner check): gapp warns about the other owner.
# Pass --force only if intentional — bucket and Cloud Run service names
# are owner-blind, so two owners with the same solution name share resources.
gapp setup --project <pid> --force
```

### **7. Audit & Recovery**
Use this when it's been a while and you have no idea where your apps are or what owner namespace you were using.
```bash
# Audit everything across your entire GCP footprint
gapp list --all

# Result:
#   app-a    project-123    <global>      <undefined>   v-3
#   app-b    project-456    owner-a       prod          v-3

# Insight: Now you know you have one global app (no owner declared) and
# one owned by 'owner-a' under env=prod. You can set your owner to 'owner-a'
# to work on app-b, or unset it to work on app-a.
```

### **8. Deploy From Anywhere (CI/CD)**
Wire a solution to GitHub Actions once, then deploy from your phone, a CI runner, or an agent session, with no `gcloud` or `terraform` on the deploying machine. The runner authenticates with Workload Identity Federation, so no key is stored anywhere.
```bash
gapp ci init <owner>/<private-ci-repo>   # once per operator: the repo that holds deploy workflows
gapp ci setup --domain mcp.example.com   # once per solution: WIF pool, deploy service account, workflow
gapp ci trigger --ref main               # any time, from any machine with `gh` logged in
```
Scope: the pool, provider, and `gapp-deploy` service account are per project and shared by every solution on it; the workflow file is per solution, so a second solution on the same project costs one `gapp ci setup`. `gapp ci init` and `gapp ci setup` need a `gcloud` login with project-admin rights; after that your workstation is optional. Without `gh`, press **Run workflow** on the CI repo's Actions tab instead.

Deployment values stay out of the solution repo: `--domain` writes the custom domain into the workflow, where it overrides `gapp.yaml` at deploy time, and re-running `gapp ci setup` keeps it. The first `gapp ci setup` on a project records the project's deploy identity in the CI repo as a GitHub Environment named after the project id; after that, `gapp ci setup --project <id>` for another solution on the same project needs only `gh`, no `gcloud`. Private solution repos work too: install a read-only GitHub App on the organization that holds them, store its id and key in the CI repo under the fixed names `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` (`gapp ci init` prints the two commands when they are missing), and `gapp ci setup` wires the rest; `gapp ci status` says what is missing. Every `gcloud` call uses the account from `gapp config account` when one is set. Design notes: [CI/CD setup](docs/CI.md).

### **9. Custom Domains**
Map a subdomain to the service with `domain:` in `gapp.yaml` (or `gapp ci setup --domain` for CI deploys), deploy, and add the CNAME `gapp status` shows. Cloud Run only maps a domain for an identity that has verified it, so verify once per domain (`gcloud domains verify example.com`, which opens Search Console; for CI deploys, add the `gapp-deploy` service account as a verified owner there too). Deploys guard the live mapping: one that would replace or remove it is refused unless you pass `gapp deploy --replace-domain`, because Terraform destroys the old mapping before creating the new one.

### **10. Expert Mode (Explicit Management)**
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
*   `gapp config owner [--unset]`: Set your namespace owner for shared projects.
*   `gapp config discovery [on|off]`: Toggle the cloud registry search.

> **Note on Enterprise Usage**: `gapp` follows your `gcloud` login wherever you go, but it also allows you to optionally work in a team or enterprise environment (e.g., a large GCP organization with thousands of users and projects) via the completely optional `owner` namespace configuration. You can use this to switch between team, personal, or org contexts at will, all without forcing that complexity on you as a required initial setup barrier.

### **Fleet Management (`gapp projects`)**
*   `gapp projects set-env <id> <env> [--force]`: Bind a project to a named env (writes the `gapp-env` label). `--force` required to overwrite an existing binding.
*   `gapp projects clear-env <id>`: Remove the env binding (project becomes undefined-env).
*   `gapp projects list`: View projects with env bindings.

### **Development**
*   `gapp deploy [--ref <ref>] [--env <env>] [--project <id>] [--rebuild] [--replace-domain]`: Build with Cloud Build and apply Terraform. `--replace-domain` is required for a deploy that would replace or remove the live custom domain mapping.
*   `gapp status`: Check infrastructure health, service URLs, and the custom domain's state (`pending_dns`, `pending_cert`, `active`, `error`).
*   `gapp secrets list`: Show declared secrets, deploy-readiness, and remediation hints. See [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) for the full label model and conflict-resolution guide.
*   `gapp secrets get <name>`: Fetch a secret value or its hash + length.
*   `gapp secrets set <name> [<value>]`: Store secrets in GCP Secret Manager.
*   `gapp manifest schema`: Print the live JSON Schema for `gapp.yaml`.

### **CI/CD (`gapp ci`)**
*   `gapp ci init <owner/repo> [--local-only]`: Designate the private CI repo that holds deploy workflows (once per operator). Also reports whether the repo holds the hosting app's `HOSTING_GITHUB_APP_ID` variable and `HOSTING_GITHUB_APP_PRIVATE_KEY` secret (private solution repos only) and prints the commands to store them; never sets them.
*   `gapp ci setup [--solution <name>] [--solution-repo <owner/name>] [--domain <host>] [--project <id>] [--out <path>]`: Wire a solution for CI (once per solution, idempotent). The first run on a project needs `gcloud`: it creates the WIF pool and deploy service account if missing and records them in the CI repo as a GitHub Environment named after the project id. With `--project <id>` and that Environment present, no `gcloud` call is made; `--project` is refused if the CI repo already binds the solution to another project. Writes the workflow through the GitHub API, or to a file (`--out`, or automatically when the API refuses the write) with the path to commit it to; `--domain` sets its domain input and is kept on re-runs when omitted. A private solution repo gets the hosting GitHub App's credentials wired in from the fixed variable and secret above; a check this machine cannot perform is reported as not checked, not failed.
*   `gapp ci trigger [--solution <name>] [--ref <ref>] [--no-watch]`: Dispatch the deploy workflow and stream the run. Refuses up front when a private solution repo's credentials are not in place.
*   `gapp ci status [--solution <name>]`: Show the CI repo, whether the workflow exists, the solution repo's visibility, the domain, the hosting app's variable and secret, and the next step.

---

## Further reading

*   [Secrets, ownership, and recovery scenarios](docs/SECRETS.md) — how gapp manages Secret Manager, the label model, and what to do when statuses go sideways (`unattached`, `conflict`, `orphan`).
*   [CI/CD setup](docs/CI.md) — wiring GitHub Actions deployments via Workload Identity Federation.
*   [Contributing](CONTRIBUTING.md) — architecture, design principles, conventions for code, comments, and examples.
