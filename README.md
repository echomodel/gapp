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

### **4. Hands-Free Setup (The Home Base)**
Designate a project as your **Home Base** for new apps so they find it automatically.
```bash
# Designate your sandbox as the default target for this workstation
gapp projects set-env my-sandbox-999 --env default

# Start a brand new app
gapp init
gapp setup                         # Auto-registers into your Home Base!
gapp deploy                        # Deploys automatically!
```

### **5. Managing Multiple Environments**
Deploy the same app to different projects for `dev`, `staging`, or `prod`.
```bash
# Prep and register your production project
gapp setup --env prod <prod-project-id>

# Deploy to production
gapp deploy --env prod
```
`gapp` ensures you never accidentally deploy `dev` code to a `prod` project by verifying the cloud designation before every push.

### **6. Expert Mode (Explicit Management)**
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
*   `gapp config owner [--unset]`: Set your namespace owner for shared projects.
*   `gapp config discovery [on|off]`: Toggle the cloud registry search.

### **Fleet Management (`gapp projects`)**
*   `gapp projects set-env <id> [--env default]`: Designate a project's role.
*   `gapp projects list [--all]`: View your project inventory and roles.

### **Development**
*   `gapp status`: Check infrastructure health and service URLs.
*   `gapp secrets set <name> <value>`: Store secrets in GCP Secret Manager.
*   `gapp manifest schema`: Print the live JSON Schema for `gapp.yaml`.
