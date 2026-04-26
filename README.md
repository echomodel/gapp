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

### **8. Expert Mode (Explicit Management)**
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
*   `gapp status`: Check infrastructure health and service URLs.
*   `gapp secrets set <name> <value>`: Store secrets in GCP Secret Manager.
*   `gapp manifest schema`: Print the live JSON Schema for `gapp.yaml`.
