# The 4.0.0 interface

**Proposed, for markup. Not built.** This page lists every command, flag,
MCP tool and file gapp reads or writes in 4.0.0. Once agreed it is frozen:
a test compares the real CLI to this page and fails on any difference, and
nothing here changes meaning before the next major version. Later releases
may only add.

Where this page and [ONE-FLEET.md](ONE-FLEET.md) differ, this page wins.
Lines marked **Open** are not decided; each has a proposed answer.

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

`app.json` is Heroku's published format, unextended. Per variable:

| Attribute | What gapp does |
|---|---|
| `value` | The default. A value in the definition wins |
| `required` (true unless set to false) with no value | The deploy is refused until the variable is set on the definition; the refusal names the command |
| `generator: "secret"` | A random value is made and stored as a secret on the first deploy, unless the definition already sets the variable |
| `description` | Shown in that refusal and by `gapp show` |

Every other key in `app.json` is ignored, never refused.

`gapp.yaml` is not read.

Nothing in 4.0.0 exists to carry an earlier setup forward: no import of
old files, no old names accepted. A fleet is brought in with `gapp fleet
import`.

## The fleet file

One YAML file, `deployments.yaml`: in the config directory for a local
fleet, at `gapp/deployments.yaml` in a GitHub fleet. Nobody edits it by
hand; every change has a command.

```yaml
format: 1
deployments:
  my-app:
    id: my-app
    repo: github.com/owner/my-app
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
        AUTH_ISSUER: {value: "https://issuer.example.com/"}
        SIGNING_KEY: {secret: signing-key, generate: true}
        APP_USERS_PATH: {value: "{{DATA_PATH}}/users"}
```

This file is the entry point for everything gapp keeps in a fleet.
`format` says how the fleet is laid out. gapp refuses a format newer than
it knows, and says to install a newer gapp. Today the file holds every
definition; a later format may have it point to other files, and a gapp
that knows both reads both.

| Field | Meaning | Changes after creation |
|---|---|---|
| The key under `deployments` (`my-app`) | The name. Unique in the fleet. The word you type | `gapp rename` |
| `id` | The prefix on the deployment's cloud resources. The name by default | Never |
| `repo` | Where the code is. Absent for an app with no remote | `gapp set repo` |
| `project` | The Google Cloud project | Never |
| `ref` | The branch, tag or commit a deploy builds. Absent: the default branch | `gapp set ref` |
| `gapp` | The gapp version this definition was written by, and the one that deploys it. Never typed | `gapp upgrade` | Brings one definition to the installed gapp's version. Takes no version. Prints its plan first. Changes the definition only. Refused when there is nothing to upgrade, when the definition is newer than this gapp, or when it is older than this gapp can upgrade from; the refusal names the gapp to install |
| `gapp rename <old> <new>` | Changes the name only. The id and the cloud resources stay |
| `gapp list` | Every definition: name, repo, project, ref, gapp version, and "upgrade available" or "upgrade required" where that applies. Says where the fleet lives |
| `gapp show` | One definition, and the settings a deploy would use, each marked as from the definition, from `app.json`, or a default. Says when the next deploy would apply an upgrade, and from which version to which |

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
| `gapp apply <file>` | Replaces a definition's settings with the file's, all at once or not at all. The file has the shape of `settings:` above. Prints what changes first |

### Running

| Command | Does | Needs |
|---|---|---|
| `gapp deploy [--dry-run] [--rebuild] [--replace-domain] [--upgrade]` | Builds the definition's ref from this clone and deploys it. Refused when the clone is not at that ref. Creates the deployment's bucket the first time | gcloud, Terraform, a clone |
| `gapp status` | What is live: URL, revision, domain state | gcloud |
| `gapp secrets list` | The definition's secrets and whether each has a value | gcloud |
| `gapp secrets set <secret-name> [--from-stdin]` | Stores a value, typed at a prompt or piped | gcloud |
| `gapp secrets get <secret-name> [--plaintext]` | Shows whether it is set; the value only with `--plaintext` | gcloud |

### Runners (a GitHub fleet only)

| Command | Does | Needs |
|---|---|---|
| `gapp ci deploy [--upgrade]` | The same deploy, on a runner | `gh` |
| `gapp ci status [--live]` | The last run and what it deployed | `gh` |
| `gapp ci sources add <owner>` / `remove <owner>` / `list` | Whose repos runners may deploy | `gh` |

### The fleet

| Command | Does |
|---|---|
| `gapp fleet` | Where the fleet lives |
| `gapp fleet use <owner>/<repo> [--create]` | Moves the fleet to that repo and installs the workflows there, if it has none |
| `gapp fleet use local` | Moves it to this machine |
| `gapp fleet export [<file>]` / `gapp fleet import <file>` | Copies the fleet out or in. Import keeps a backup first |
| `gapp fleet recover` | Rebuilds the fleet from the copy saved in each deployment's bucket |

### This machine

| Command | Does |
|---|---|
| `gapp config profile list` / `use <name>` / `add <name>` / `remove <name>` / `rename <old> <new>` | Profiles: separate fleets and accounts on one machine. `use` refuses a name that does not exist |
| `gapp config account [<email>]` | Shows or sets the Google Cloud login the active profile uses |

## MCP tools

One tool per command, named after it, with the same arguments. A
deployment is always the `deployment` argument.

`gapp_project_setup`, `gapp_add`, `gapp_upgrade`, `gapp_rename`, `gapp_list`, `gapp_show`,
`gapp_set`, `gapp_unset`, `gapp_apis`, `gapp_env_set`, `gapp_env_unset`,
`gapp_apply`, `gapp_deploy`, `gapp_status`, `gapp_secrets_list`,
`gapp_secrets_set`, `gapp_secrets_get`, `gapp_ci_deploy`, `gapp_ci_status`,
`gapp_ci_sources`, `gapp_fleet`, `gapp_fleet_use`,
`gapp_fleet_export`, `gapp_fleet_import`, `gapp_fleet_recover`.

No tool takes a secret's value or a path on the server's machine.

## Not in 4.0.0, and addable without a break

- Removing a definition (`gapp remove <name>`).
- Several services in one deployment (reserved above).
- Fleet changes by pull request.
- Secrets from a machine with no gcloud.
- A shortcut for choosing among several deployments of one repo.
- Reading more of a repo, such as a Compose file.

## Open

1. **Secret names.** A secret has its own short name (`signing-key`),
   separate from the variable that reads it (`SIGNING_KEY`). Proposed: keep
   both, with the secret's name defaulting to the variable's in lower case
   with dashes, so most people type one name.
2. **`gapp apply`.** Proposed: in 4.0.0, as above. It is the only way to
   make several changes at once.
3. **`gapp show` with no clone.** Proposed: it works from the definition
   alone and says `app.json` was not read.
4. **Exit codes.** Proposed: 0 done, 1 failed, 2 refused with nothing
   changed. The same for every command.
5. **A self-describing command for agents** (`gapp guide`). Proposed:
   later; it adds a command and changes none.
6. **`app.json` in 4.0.0 or the release after.** Proposed: 4.0.0, as the
   last stage.
7. **The flag's name.** `--upgrade` on `gapp deploy` and `gapp ci deploy`
   (see "Versions"). Proposed: `--upgrade`.
8. **The file's name and place.** It is the entry point for whatever
   gapp keeps in a repo, so its name should assume nothing about what is
   inside: not that it is a list of deployments, and not that there is one
   fleet. Proposed: `gapp.yaml` at the repo's root, with the same name for
   the local file. A rename after 4.0.0 would be a break.
