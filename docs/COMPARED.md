# gapp compared

**In one paragraph:** gapp lets you deploy, redeploy and move apps
without thinking about it, securely and the same way every time, while
asking nothing of the apps themselves. An app needs no gapp files, no
particular framework or design, and nothing that ties it to how gapp
runs it: it stays an ordinary container that anyone could run some
other way.

The rest of this page says what gapp adds, how strong each reason is,
and what else exists for deploying HTTP apps to Google Cloud Run. Facts
about other tools were checked in November 2026 and link to their
sources; tools change, so check them before relying on this page.

## Why gapp exists

Without it, every app repo grows its own ops: build and deploy scripts,
a Terraform configuration, a CI workflow, and one particular security
and deployment model. Three things go wrong.

**The copies drift.** Each repo's scripts and workflows are a little
different, and each is used rarely, so nobody remembers where its pieces
are, how it is wired, or which version of what last ran it. Redeploying
an app untouched for months means finding all of that again, with no
confidence it is safe.

**The repo can't be a product.** An app meant for others to contribute
to and run can't hold its operator's project IDs, domains, environment
values, secrets or security choices. Kept out of the repo, they end up
scattered, so there is never a "go here and run this", for its author
or for anyone else.

**Growing up means switching systems.** An app starts local, then
earns a CI pipeline, then a server or service to run that pipeline,
each one more thing to set up, move, and remember. Apps end up on
different systems depending on when they were built, and nobody is sure
which used what.

gapp is that ops layer written once and kept outside the app. The repo
holds only the app. Every operator choice lives in the fleet, one file
or git repo, which the same commands read whether a deploy runs from a
laptop or a GitHub Actions runner, so moving an app from local to CI
changes nothing about how it is deployed. Every deployment goes through
the same code, so that code is exercised all the time, and a deploy of
an app untouched for months behaves like a deploy of one changed today.
Starting needs only the CLI; there is no CI server to stand up, and
adding the hundredth app is the same as adding the first.

## What gapp adds, strongest first

| Strength | What you get | Caveat |
|---|---|---|
| High | **No drift.** Every app is built, deployed and laid out the same way, by one tool, with no per-app scripts | Within what gapp models today: one web service per deployment (below) |
| High | **No infrastructure code to write or keep.** The service, its data bucket, secrets, service account and access, and domain come from named settings | gapp's choices, not yours: what the settings can express is what you get |
| High | **One CI setup for every app.** GitHub becomes the control panel for who can deploy and what ran | Repos on GitHub only |
| High | **Come back with no context.** One CLI finds and redeploys anything you run, and every deployment keeps its configuration and data in the same predictable places. Lose your list of deployments and gapp finds its projects by their label and rebuilds the list from records kept in each project | Records exist only for deployments gapp has deployed; data in an outside store is found through the variable or secret that points to it |
| High | **Apps stay products, not one operator's setup.** An app repo holds no deploy files, domains, projects or secrets, so anyone can run it in their own environment with their own tooling and their own rules on security and operations | Matters most for apps meant to be shared; an app only you will ever run gains less |
| Medium | **A record in each project** of what was deployed, from which commit, by whom and from where | No command shows all of it yet |
| Low | **Every command is an MCP tool,** with output written for agents | Any CLI can be wrapped |
| Low | **A live domain is not replaced by accident** | Only the domain is guarded |

### No infrastructure code: gapp and Terraform

gapp runs Terraform, so the comparison is not gapp or Terraform but
Terraform alone or Terraform inside gapp.

Terraform is a language and an engine, not a product. It is a Swiss army
knife: it can describe all of this, and nothing about it keeps you from
having to know where everything is and how it is wired. With Terraform
alone you:

- write the configuration for each app, put it somewhere, and keep it
  current;
- find the project, and decide where the state lives, then remember
  where;
- pass the variables it expects, correctly, every time;
- run it from somewhere with credentials;
- stitch together the parts it doesn't cover, such as building the image,
  generating secrets, and the steps before and after apply;
- avoid changing anything outside it, or reconcile the difference by
  hand.

It is not a closed system: it takes the arguments it is given, whether or
not they are right.

With gapp:

- the Terraform is written and tested once, and nobody sees a `.tf` file;
- a deployment's only inputs are named settings, checked before anything
  runs;
- where the state lives follows from the project and the deployment;
- projects are found by a label gapp puts on them;
- credentials come from whoever runs the deploy: a person's login or the
  runner's identity.

Terraform is how; gapp is what.

### One CI setup for every app

GitHub Actions is the engine, and gapp uses it. What gapp removes is the
per-app setup: without it, every app needs its own workflow file, its
own way of signing in to Google Cloud, and its own copy of its settings,
and those drift exactly as the Terraform copies do.

With gapp, one fleet repo holds one deploy workflow for every app in
every project. The only step per project is trust: each project agrees
once to let that repo's runners deploy to it (`gapp ci projects
connect`). That step is per project on purpose, so each project decides
for itself who can deploy into it. Then:

- **Access is managed on GitHub:** whoever can run workflows in the fleet
  repo can deploy, and nobody else. No Google Cloud keys are stored
  anywhere.
- **Deploy from anywhere a workflow can be started:** the github.com page,
  `gh`, a phone, or an agent session with no Google Cloud login.
- **One log:** every deploy of every app is a run in one repo.

### Come back with no context

The fleet lists every deployment: its repo, project, ref and settings.
`gapp deploy` in a clone of an app finds that app's deployment;
`gapp ci deploy --deployment <name>` needs only the name. If the fleet is
lost, `gapp deployments scan` finds gapp's projects by their label, reads
the deploy record each deployment left in its project, and prints the
`gapp add` commands that bring every deployment back with its settings.
If a project's label is gone, naming the project still works.

gapp asks nothing of an app beyond what any container already does. It
takes the ordinary ways a containerized app keeps state and settings,
and backs each one with something durable that is named and placed the
same way for every deployment:

- **Files written to a local path** (the usual way a container keeps
  state) land in the deployment's own bucket: the bucket is mounted into
  the container, and the variable the app reads its path from points
  there. They survive restarts and redeploys and can be found, backed up
  or moved without knowing anything about the app.
- **Settings read from environment variables** are kept in the
  deployment's definition.
- **Secrets read from environment variables** are kept in Secret
  Manager, under names that start with the deployment's and carry its
  label.

An app that keeps data in an outside store reaches it the same way,
through a variable or a secret, so even that store is named in the
definition.

So months later, operating, updating, backing up, migrating or retiring
a deployment starts from the same places for every app, without
remembering how that one was set up.

### Apps stay products

An app's repo never names gapp, a project, a domain or a cloud. Its
operator's choices live in the fleet, not in the code. So the same repo
can be run by its author, by someone at home, and by a company with its
own operations model and security rules, each with their own tooling,
and none of them has to strip out or work around anyone else's setup.
That matters more as people build MCP servers and other small services
meant to be shared: an app that carries its author's deploy setup
looks like a one-off, while one that carries none can be adopted by
anyone.

### The record in each project

Each deploy writes, into the project's own gapp bucket, the definition it
used, the commit it built, the image, the gapp version, who ran it and
from where (a machine, or a runner with a link to its run). A CI run log
shows part of this, and Cloud Run keeps part of it on each revision (the
image, variables, secret references, sizing). What only the record does
is cover deploys from a machine as well as from a runner, and hold the
whole definition, which is what makes rebuilding a fleet possible.

## What gapp does not do today

A deployment is one web service on Cloud Run. Cloud Run jobs, a database
or document store, and apps made of several services are not modelled
yet; nothing in the design rules them out.

gapp keeps no history of its own beyond the records above: no rollback
command and no "what changed since the last deploy" view.

## Other ways to get here

### Platforms that host apps themselves

Fly.io, Render, Railway and Heroku are what most people mean by "push and
it runs": connect a repo or run one command, and the app is live with its
database, domain and certificates. Their price is that the app runs on
their infrastructure, not in your Google Cloud project. Heroku moved to
sustaining engineering, with no new features, in February 2026
([source](https://simonwillison.net/2026/Feb/6/an-update-on-heroku/)).

gapp aims at that experience for Cloud Run in your own project. No
mature tool found offers it there; the nearest are below.

### Closest alternatives on Cloud Run

| Tool | What it does | Gaps against gapp |
|---|---|---|
| [Defang](https://github.com/DefangLabs/defang) | Deploys a Docker Compose project to your own AWS, Google Cloud, DigitalOcean or Azure account, with a hosted control plane, a GitHub Action and an MCP server | Google Cloud support is in preview: no sensitive config values, custom domains in development, no volumes on any provider ([GCP](https://github.com/DefangLabs/defang-docs/blob/main/docs/providers/gcp.md), [Compose](https://github.com/DefangLabs/defang-docs/blob/main/docs/concepts/compose.md)). Needs `compose.yaml` and Dockerfiles in each repo. State lives in its hosted account, and deploying to your own cloud is a paid plan |
| [`gcloud run compose up`](https://docs.cloud.google.com/run/docs/deploy-run-compose) | Part of Google Cloud's CLI: deploys a `compose.yaml` to Cloud Run, mapping volumes to a bucket and Compose secrets to Secret Manager | Preview, and its docs say it "isn't a replacement for a comprehensive IaC strategy for production". Needs a compose file in the repo. No fleet, domains, service accounts or CI |
| [Coherence cnc](https://github.com/coherenceplatform/cnc) | Open-source tool with a Cloud Run flavor, driven by `cnc.yml` and `environments.yml` | Small and of unclear maintenance; config files live in each repo |
| [Nitric](https://github.com/nitrictech/nitric) and [Encore](https://encore.dev/docs/platform/deploy/own-cloud) | Infrastructure from code: deploy to Cloud Run with storage and secrets in your project | The app must be written with their SDK, so an unchanged API, MCP server or container can't be deployed. Encore's own-cloud mode is a paid hosted service |
| [Firebase App Hosting](https://firebase.google.com/docs/app-hosting/frameworks-tooling) | Google Cloud's "connect a repo, deploy on push", on Cloud Run | Web frameworks only (Next.js, Angular, a few community adapters); no Dockerfile, no Python, so no plain API or MCP server |
| Cloud Run [continuous deployment](https://docs.cloud.google.com/run/docs/continuous-deployment) and `gcloud run deploy --source` | One service built from a repo, on every push or on demand | Per service: no bucket, secrets, service account, domain or fleet |

Tools that deploy into your own cloud account but not onto Cloud Run:
Qovery, Porter, Northflank and Zeabur run a GKE cluster in your project
([Qovery](https://www.qovery.com/docs/getting-started/installation/gcp.md),
[Zeabur](https://zeabur.com/docs/en-US/byos/gcp)); Flightcontrol and SST
target AWS; Kamal, Coolify and Dokku run on servers or VMs. AWS's own
analogue to gapp, the Copilot CLI, reached end of support in June 2026
([source](https://aws.amazon.com/blogs/containers/announcing-the-end-of-support-for-the-aws-copilot-cli)).

### Building blocks gapp could use

None of these replaces gapp, and some could take over a piece of it:

- **Google Cloud's Terraform module for Cloud Run**
  ([on GitHub](https://github.com/GoogleCloudPlatform/terraform-google-cloud-run))
  covers the service, its access, a domain mapping and secret variables.
  gapp would still own the bucket, the service account and secret
  generation.
- **Buildpacks** would build a Procfile app in any language. gapp's own
  image template, used only when a repo has no Dockerfile, installs
  Python apps.
- **Cloud Run revisions** already keep each deploy's image, variables and
  secret references, and can take traffic back to an earlier revision
  without a rebuild. Recording the commit and ref on each revision would
  put gapp's deploys in Cloud Run's own history.
- **[Cloud Deploy](https://cloud.google.com/deploy/pricing)** adds
  releases, promotion between environments, approvals and rollback. It
  provisions nothing around the service and needs a pipeline per app; it
  is worth it only for promotion from one environment to another, which
  gapp doesn't model. Single-target pipelines are free.

## What only gapp was found to do

- Keep every operator choice (projects, domains, secrets, settings) in
  one file or git repo, apart from the app repos, which need no deploy
  files at all. Every alternative found needs something in the repo:
  a compose file, its own config file, an SDK, or a Dockerfile
  convention.
- Find its projects by a label, and rebuild its list of deployments from
  records kept in each project.
- Deploy every app in every project from one GitHub Actions setup, with
  no stored keys and no Google Cloud login on the caller's machine. The
  only per-project step is trust, kept per project on purpose so each
  project controls who can deploy into it.
- Generate or check every secret a deployment reads before deploying it.
- Mount a data bucket into every deployment by default.
- Do all of this as a local, free, open-source CLI with no hosted control
  plane.
