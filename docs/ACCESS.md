# Access: what each command needs and touches

Three kinds of command, by what they reach:

- **Fleet commands** (`gapp add`, `set`, `env`, `apply`, `show`, `list`,
  `fleet`, `config`) read and change your fleet. They touch no cloud. With
  the fleet in a git repo they use git, with the access your machine has.
- **Cloud commands** (`gapp project setup`, `deploy`, `status`, `secrets`)
  work on Google Cloud from your machine, with your gcloud login.
- **`gapp ci` commands** work on GitHub from your machine, with your `gh`
  login. Google Cloud is reached by a runner: a GitHub Actions job in
  your fleet repo that signs in to the project as its deploy identity.

The one `gapp ci` command that touches Google Cloud from your machine is
`gapp ci projects connect <project>`. It creates the trust that lets the
fleet repo's runners sign in to the project at all, so no runner could do
it: you do it once per project, as an Owner.

After that, a machine with only `gh` can deploy every deployment in the
fleet and never holds a Google Cloud credential.

## Every command

| Command | Your machine needs | Google Cloud touched by | The fleet |
|---|---|---|---|
| `gapp add`, `rename`, `set`, `unset`, `apis`, `env`, `apply` | git, for a fleet in a repo | nothing | changed |
| `gapp upgrade` | git, for a fleet in a repo; gcloud optional | you, read-only: the deploy record, when it can be read, to say whether the next deploy needs `--upgrade` | changed |
| `gapp list`, `show`, `fleet`, `fleet export` | git, for a fleet in a repo | nothing | read |
| `gapp fleet use`, `fleet import` | git; `gh` for `--create` | nothing | moved or replaced |
| `gapp config ...` | nothing | nothing | not touched |
| `gapp project setup <project>` | gcloud as an Owner | you | not touched |
| `gapp deploy` | gcloud, Terraform, a clone of the app's repo | you (Cloud Build, then Terraform) | read |
| `gapp status` | gcloud, Terraform (to read state) | you, read-only | read |
| `gapp deployments list`, `deployments scan` | gcloud | you, read-only: deploy records (and, for `scan`, a search for gapp's projects) | read |
| `gapp secrets list\|get\|set` | gcloud | you | read |
| `gapp ci init`, `ci projects list` | `gh` | nothing | read |
| `gapp ci projects connect <project>` | gcloud as an Owner, `gh` as an admin of the fleet repo | you, once per project | not touched |
| `gapp ci deploy` | `gh` that can start workflows | the deploy runner | read, by you and by the runner |
| `gapp ci status` | `gh` | nothing | read |
| `gapp ci status --live` | `gh` that can start workflows | the status runner, read-only | read, by you and by the runner |

"You" means your own login on your machine. A runner signs in as the
project's `gapp-deploy` service account, and reads a private
app repo with a read-only GitHub App (see [CI](CI.md)).

## Which machine can do what

| Machine | Can run |
|---|---|
| Any machine | The fleet commands, on a local fleet |
| One with git access to the fleet repo | The fleet commands, on a fleet in a repo |
| One with gcloud and Terraform | `deploy`, `status` and `secrets`, for any deployment in the fleet; `deploy` needs a clone of the app's repo |
| One with `gh` | Every `gapp ci` command except `projects connect` |
| One with gcloud as a project Owner | `project setup`; with `gh` as an admin of the fleet repo, `ci projects connect` |

A secret's value goes from your machine straight to Secret Manager. No
runner and no workflow is involved, and the fleet holds only the secret's
name.
