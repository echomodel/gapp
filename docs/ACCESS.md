# Access: what each command needs and touches

gapp has two systems, and a wall between them:

- **Root commands** (`gapp setup`, `deploy`, `status`, `list`, `secrets`) work on Google Cloud **from your machine**, with your gcloud login. They never read GitHub, except `gapp secrets ... --deployment <name>` (below).
- **`gapp ci` commands** work on GitHub **from your machine**, with your `gh` login, and never use gcloud there. Google Cloud is reached only by a **runner**: a GitHub Actions job in your fleet repo, signing in to the project with its own deploy identity.

The one exception is `gapp ci project setup <project>`, run once per project. It creates the trust that lets the fleet repo's runners sign in to the project at all, so until it has run there is no runner that could do it: you do it yourself, as an Owner of the project.

So a machine with only `gh` (a cloud agent session, a teammate's laptop) can run the whole `ci` system after that one step, and never holds a Google Cloud credential.

## Every command

| Command | Your machine needs | Google Cloud touched by | GitHub touched by |
|---|---|---|---|
| `gapp init` | the app's folder | nothing | you, only if `gh` is signed in: adds the `gapp-solution` topic to the repo |
| `gapp manifest ...` | the app's folder (or `--file`) | nothing | nothing |
| `gapp setup` | gcloud | you | nothing |
| `gapp deploy` | gcloud, Terraform | you (Cloud Build, then Terraform) | nothing |
| `gapp status`, `gapp list` | gcloud (`status` also Terraform, to read state) | you, read-only | nothing |
| `gapp secrets list\|get\|set` | gcloud, the app's folder | you | nothing |
| `gapp secrets list\|get\|set --deployment <name>` | gcloud, `gh` | you | you, read-only (the fleet repo and the app repo) |
| `gapp ci init`, `ci sources ...` | `gh` | nothing | you |
| `gapp ci project setup <project>` | gcloud as an Owner, `gh` as an admin of the fleet repo | you, once per project | you |
| `gapp ci setup`, `ci rename` | `gh` that can dispatch workflows | nothing | you dispatch; the register runner writes the fleet file |
| `gapp ci manifest <name>`, `ci status <name>`, `ci list` | `gh` | nothing | you, read-only (the fleet repo) |
| `gapp ci manifest <name> --merged` | `gh` | nothing | you, read-only (the fleet repo and the app repo) |
| `gapp ci deploy` | `gh` that can dispatch workflows | the deploy runner | you dispatch; the deploy runner reads the app repo |
| `gapp ci status <name> --live` | `gh` that can dispatch workflows | the status runner, read-only | you dispatch |

"You" means your own login on your machine. A runner signs in to Google Cloud as the project's `gapp-deploy` service account, and reads a private app repo with the fleet's read-only GitHub App (see [CI/CD setup](CI.md)).

## Which machine can do what

- **A laptop with gcloud and Terraform:** the root commands, for an app checked out there.
- **Any machine with `gh`:** every `ci` command except `ci project setup`, for every deployment in the fleet, with no checkout.
- **A machine with gcloud as a project Owner and `gh` as a fleet repo admin:** `ci project setup`, once per project.

`gapp secrets ... --deployment <name>` is the one exception to the wall: a root command that reads the fleet. It does not use a runner, a workflow or Actions; it only reads the fleet file (and the app repo's gapp.yaml) with your `gh` login, and writes nothing to GitHub. A secret's value goes from your machine straight to Secret Manager, so it needs gcloud; the fleet only says which project and which secrets. It is how a deployment with no folder of its own (configured only in the fleet, or a copy) gets its secrets. If your `gh` login can't read the app repo, it works from the settings the fleet holds and says so.

`gapp ci manifest --merged` reads the app repo with your own `gh` login, so it can fail where a deploy works: the runner reads with the fleet's own access.
