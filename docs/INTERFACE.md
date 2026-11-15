# The 4.0.0 interface

**Agreed. Being built on the `one-fleet` branch.** This page lists every
command, flag, MCP tool and file gapp reads or writes in 4.0.0. A test
compares the real CLI to this page and fails on any difference. Nothing
here changes meaning before the next major version; later releases may
only add.

## Words

| Word | Meaning |
|---|---|
| Fleet | Every deployment definition you have. It lives in one place: a file on this machine, or a GitHub repo |
| Definition | One deployment's entry in the fleet: its name, where its code is, which project it runs in, and its settings |
| Deployment | The running service with its data and secrets, in Google Cloud |
| Setting | One value in a definition that shapes the deployment |

## What gapp reads from an app's repo

Nothing in a repo names gapp or Google Cloud, and gapp never writes to a
repo.

| File | Required | What gapp takes from it |
|---|---|---|
| `Dockerfile` or `Procfile` | One of them, unless the definition has `start` | How to build and start the app |
| `app.json` | No | The `env` block only: the variables the app reads |

`app.json` is Heroku's published format, unextended. It is read in one
place: a deploy, from the commit the deploy builds. Nothing from it is
kept. **The definition wins, and `app.json` fills in what the definition
leaves out.** Per variable the definition does not set:

| In `app.json` | What the deploy does |
|---|---|
| A `value` | Uses it as the default |
| No `value`, and required (true unless set to false) | Refuses until the variable is set on the definition. The refusal gives the description and the command |
| `generator: "secret"` | The same refusal, suggesting a generated secret: `gapp env set NAME --secret --generate`. gapp makes a secret only when the definition asks for one |
| No `value`, `"required": false` | Leaves it unset |

A variable the definition sets is used whatever `app.json` says about
it. When `app.json` has an `env` block, a variable the definition sets
that the block does not list gets a warning at deploy, because the app
may not read it; the deploy goes ahead. An app with no `app.json`, or
one with no `env` block, is never checked.

Every other key in `app.json` is ignored, never refused. No other
command reads the file: `gapp show` and `gapp secrets list` report the
definition, and `gapp deploy --dry-run` shows what `app.json` adds.

`gapp.yaml` is not read.

Nothing in 4.0.0 exists to carry an earlier setup forward: no import of
old files, no old names accepted. A fleet is brought in with `gapp fleet
import`.

## The fleet file

One YAML file, `gapp.yaml`: in the config directory for a local fleet, at
the root of the repo for a GitHub fleet. The name says only that gapp
starts here. Nobody edits it by hand; every change has a command.

```yaml
format: 1
deployments:
  my-app:
    id: my-app
    repo: owner/my-app
    project: my-project
    ref: main
    gapp: 4.0.0
    settings:
      public: true
      domain: app.example.com
      start: my-app serve --port 8080
      memory: 1Gi
      cpu: "1"
      max_instances: 2
      apis: [drive.googleapis.com]
      env:
        LOG_LEVEL: {value: info}
        APP_KEY: {secret: app-key, generate: true}
        APP_DATA: {value: "{{DATA_PATH}}/users"}
```

This file is the entry point for everything gapp keeps in a fleet.
`format` says how the fleet is laid out. gapp refuses a format newer than
it knows, and says to install a newer gapp. Today the file holds every
definition; a later format may have it point to other files, and a gapp
that knows both reads both.

| Field | Meaning | Changes after creation |
|---|---|---|
| The key under `deployments` (`my-app`) | The name. Unique in the fleet. The word you type | `gapp rename` |
| `id` | The prefix on the deployment's cloud resources. gapp picks it when the deployment is added: the name, or the name with a number when that is taken on the project | Never |
| `repo` | Where the code is: `owner/repo` on GitHub, `host/path` elsewhere. Absent for an app with no remote | `gapp set repo` |
| `project` | The Google Cloud project | Never |
| `ref` | The branch, tag or commit a deploy builds. Absent: the default branch | `gapp set ref` |
| `gapp` | The gapp version this definition was written by, and the one that deploys it. Never typed | `gapp upgrade` |
| `settings` | Everything below | The settings commands |

Settings:

| Key | Meaning | Default |
|---|---|---|
| `public` | Anyone can call the URL; the app does its own sign-in | false |
| `domain` | A custom domain mapped at deploy | none |
| `start` | The start command, for a repo with no Dockerfile or Procfile | none |
| `memory`, `cpu`, `max_instances` | Cloud Run sizing | 512Mi, 1, 1 |
| `apis` | Extra Google Cloud APIs the app uses | none |
| `env` | Variables the service gets, by name. Each is a plain `value`, or a `secret` (optionally `generate`) | none |

`{{DATA_PATH}}` in a value is the folder where the deployment's bucket is
mounted. `{{NAME}}` is the deployment's id.

**Reserved, not in 4.0.0:** a deployment made of several services. It
would add a `services:` map inside `settings`, each with its own keys, and
a `--service <name>` flag on the settings commands. A definition without
`services:` keeps meaning exactly what it means now.

## Versions

Two things record a version, and every gapp checks them before anything
else. Their names and places never change.

| Where | Says | Checked |
|---|---|---|
| `format`, at the top of the entry file | How the file is laid out | Before anything else in the file is read |
| The `gapp-version` label on the deployment's bucket | Which gapp last deployed it, written like `4-0-0` | Before a deploy builds or changes anything |

A gapp that finds a value it does not support, or none, stops there. It
says what it found and where, reads no further, writes nothing and
touches no deployment.

Each definition also records, in `gapp`, the version of the gapp that
wrote it. `gapp add` writes the version of the gapp that ran it.

**A runner deploys with the definition's version**, whatever is installed
on the machine that asked. So a gapp upgrade on a workstation changes
nothing by itself. There is no fleet-wide version.

**A newer gapp and an older definition.** Within the range the newer gapp
supports:

| Action | Allowed |
|---|---|
| Read it, show it, start a runner deploy | Yes |
| Change a setting the definition's version knows | Yes |
| Use a setting it does not know | No: `gapp upgrade` first |
| Deploy from this machine | No: `gapp upgrade` first |

So an upgrade can be available without being required. `gapp list` and
`gapp show` say which.

**Upgrading is two deliberate steps.**

1. `gapp upgrade` changes the definition to the installed gapp's version
   and prints the plan: what changes in the definition, what the next
   deploy will change in the cloud, and anything to do by hand before or
   after. It never goes down, and only the gapp being upgraded to knows
   the way there, so it takes no version.
2. The next deploy applies it. A deploy that would apply an upgrade is
   refused without `--upgrade`, and `--upgrade` is refused when no upgrade
   is pending. `gapp show` and `gapp deploy --dry-run` say in advance
   which it is.

Which earlier versions a gapp can upgrade from is that release's choice,
and may be none. When a definition is too old, the refusal names a gapp
to install first: install it, upgrade, and repeat.

What this depends on: the way a workflow starts a gapp on a runner stays
the same across releases, and a test holds it there.

## Which deployment a command means

One rule, for every command about one deployment:

1. `--deployment <name>`, or `-d <name>`, names it. This works anywhere.
2. Left out, inside a clone: the one definition whose `repo` is the
   clone's remote. Several: refused, with each full command printed. None:
   refused, with the `gapp add` command printed.
3. Left out, outside a clone: refused.

The name is a positional argument in exactly three places, where the name
is the thing being made, changed or removed: `gapp add <name>`, `gapp
rename <old> <new>`, `gapp remove <name>`. Everywhere else positional
arguments are the things inside the deployment: a setting, a variable, a
secret.

A folder's name is never used. A clone with no remote is matched only by
`--deployment`.

## Commands

Every command takes `--json`.

A command that changes the fleet writes straight to where the fleet lives:
the file on this machine, or a commit to the repo. No runner is involved,
so keeping a fleet in a repo needs no CI. Only the `gapp ci` commands use
GitHub Actions; `gapp ci deploy` starts a run and takes `--no-watch`. The
reasons are in [FLEET-IN-GIT.md](FLEET-IN-GIT.md).

### Projects

| Command | Does | Needs |
|---|---|---|
| `gapp project setup <project>` | Once per project: enables APIs, grants Cloud Build its permissions, marks the project. Nothing about CI | gcloud as an Owner |

### Definitions

| Command | Does |
|---|---|
| `gapp add [<name>] --project <p> [--repo <r>] [--ref <r>]` | Defines a deployment. Touches no cloud. Refused if the name exists. Inside a clone, name and repo default to the clone's |
| `gapp add <name> --copy-of <existing> [--project <p>] [--ref <r>]` | A second deployment of the same repo. Copies settings except `domain`. Project and ref default to the original's |
| `gapp upgrade` | Brings one definition to the installed gapp's version. Takes no version. Prints its plan first. Changes the definition only. Refused when there is nothing to upgrade, when the definition is newer than this gapp, or when it is older than this gapp can upgrade from; the refusal names the gapp to install |
| `gapp rename <old> <new>` | Changes the name only. The id and the cloud resources stay |
| `gapp remove <name>` | Takes the definition out of the fleet and touches no cloud. Names the service, bucket and secrets it leaves in the project, and says that a deployment of the same name added on that project uses them again. Its settings do not come back with it; the output gives the command that shows them: the commit that removed it, for a fleet in a repo, or the copy of the fleet from before, for a local one. Refused for a name the fleet does not hold |
| `gapp list` | Every definition: name, repo, project, ref, gapp version, and "upgrade available" or "upgrade required" where that applies. Says where the fleet lives |
| `gapp show` | One definition and its settings, each marked as set on the definition or as its default. Says when the next deploy would apply an upgrade, and from which version to which. With `--json`, `settings` is the settings object as `gapp apply` takes it |

### Settings

Each changes one definition and nothing else.

| Command | Does |
|---|---|
| `gapp set <key> <value>` | Sets `ref`, `repo`, `public`, `domain`, `start`, `memory`, `cpu`, `max_instances` |
| `gapp unset <key>` | Clears one of those. `repo`, `project` and `id` cannot be cleared |
| `gapp apis add <api>` / `gapp apis remove <api>` | Changes the extra APIs |
| `gapp env set <NAME> <value>` | A plain variable |
| `gapp env set <NAME> --data-path [<subfolder>]` | The variable points at the deployment's data folder, which survives restarts |
| `gapp env set <NAME> --secret [<secret-name>] [--generate]` | The variable comes from a secret. The secret's value is set with `gapp secrets set` |
| `gapp env unset <NAME>` | Removes the variable from the definition. Leaves any secret in place |
| `gapp apply <file> [--dry-run]` | Replaces a definition's settings with the file's, all at once or not at all. The file has the shape of `settings:` above. Prints each setting that changes, before and after. With `--dry-run` it prints the same and changes nothing |

### Running

| Command | Does | Needs |
|---|---|---|
| `gapp deploy [--dry-run] [--rebuild] [--replace-domain] [--upgrade]` | Builds the definition's ref from this clone and deploys it. Refused when the clone is not at that ref. Creates the deployment's bucket the first time. `--dry-run` is the full preview: everything the deploy would use and do, including what the app's `app.json` adds and any variable it needs that is not set, with nothing built or changed | gcloud, Terraform, a clone |
| `gapp status` | What is live: URL, revision, domain state | gcloud |
| `gapp secrets list` | The definition's secrets, whether each is generated, and its state in Secret Manager: set, not set, unlabelled, or another deployment's. What to do about each is listed under the table | gcloud |
| `gapp secrets set <secret-name> [--from-stdin]` | Stores a value, typed at a prompt or piped | gcloud |
| `gapp secrets get <secret-name> [--plaintext]` | Shows whether it is set; the value only with `--plaintext` | gcloud |

### GitHub Actions (optional)

Everything about GitHub Actions is under `gapp ci`, and nothing else knows
it exists. Keeping a fleet in a repo does not need it, and any other CI
tool deploys by running `gapp deploy`. These need the fleet to be in a
GitHub repo.

| Command | Does | Needs |
|---|---|---|
| `gapp ci init` | Installs the workflows in the fleet repo | `gh` as an admin of it |
| `gapp ci projects connect <project>` | Lets the fleet repo's runners sign in to that project and deploy. Refused on a project that is not set up | gcloud as an Owner; `gh` as an admin of the fleet repo |
| `gapp ci projects list` | The projects they can deploy to, from the fleet repo's record | `gh` |
| `gapp ci deploy [--upgrade]` | The same deploy, on a runner | `gh` |
| `gapp ci status [--live]` | The last run and what it deployed | `gh` |
| `gapp ci sources add <owner>` | Lets runners deploy that owner's repos | `gh` |
| `gapp ci sources remove <owner>` | Stops that | `gh` |
| `gapp ci sources list` | Whose repos runners may deploy | `gh` |

### The fleet

| Command | Does |
|---|---|
| `gapp fleet` | Where the fleet lives |
| `gapp fleet use <owner>/<repo> [--create]` | Moves the fleet to that repo. Installs nothing else there |
| `gapp fleet use local` | Moves it to this machine |
| `gapp fleet export [<file>]` / `gapp fleet import <file>` | Copies the fleet out or in. Import keeps a backup first |

### This machine

| Command | Does |
|---|---|
| `gapp config profile list` | Profiles: separate fleets and accounts on one machine |
| `gapp config profile use <name>` | Switches to one. Refused when it does not exist |
| `gapp config profile add <name>` | Makes one |
| `gapp config profile remove <name>` | Removes one |
| `gapp config profile rename <old> <new>` | Renames one |
| `gapp config account [<email>]` | Shows or sets the Google Cloud login the active profile uses |

## MCP tools

One tool per command, named after it, with the same arguments. A
deployment is always the `deployment` argument.

`gapp_project_setup`, `gapp_add`, `gapp_upgrade`, `gapp_rename`, `gapp_remove`, `gapp_list`, `gapp_show`,
`gapp_set`, `gapp_unset`, `gapp_apis`, `gapp_env_set`, `gapp_env_unset`,
`gapp_apply`, `gapp_deploy`, `gapp_status`, `gapp_secrets_list`,
`gapp_secrets_get`, `gapp_ci_init`, `gapp_ci_projects`,
`gapp_ci_deploy`, `gapp_ci_status`,
`gapp_ci_sources`, `gapp_fleet`, `gapp_fleet_use`,
`gapp_fleet_export`, `gapp_fleet_import`.

No tool takes a secret's value or a path on the server's machine. There is
no tool for `gapp secrets set`: a value is typed at its prompt or piped in.

## Not in 4.0.0, and addable without a break

- Removing a definition (`gapp remove <name>`).
- Several services in one deployment (reserved above).
- Fleet changes by pull request.
- Secrets from a machine with no gcloud.
- A shortcut for choosing among several deployments of one repo.
- Reading more of a repo, such as a Compose file.
- Rebuilding a lost fleet from what is deployed (`gapp fleet recover`).
- Disconnecting a project from the runners (`gapp ci projects disconnect`).
- A command that describes gapp to an agent (`gapp guide`).
- A gapp version chosen per deployment ahead of an upgrade, and upgrading
  every definition at once.

## Rules that hold everywhere

- **Exit codes.** 0: done. 1: failed. 2: refused, and nothing changed.
- **A deployment belongs to one fleet.** Two fleets that both define the
  same deployment are not supported, and gapp does not try to reconcile
  them.
- **A release starts strict.** A newer gapp asks for `gapp upgrade` before
  it changes an older definition or deploys it from this machine. A
  release may choose to allow more for the versions it names.
- **A runner is started with the deployment's name and nothing else.**
  Everything else comes from the definition, so the way a workflow starts
  gapp has little reason to change.
- **A secret's name** defaults to its variable's, in lower case with
  dashes (`APP_KEY`, `app-key`). `--secret <name>` gives another.
- **`gapp show` works from the definition alone**, from any folder. What
  it says does not depend on where it is run or on any commit.
- **A variable the definition sets is always passed**, whether or not
  the app still reads it. The warning above covers apps whose `app.json`
  lists their variables; for others nothing can tell.
