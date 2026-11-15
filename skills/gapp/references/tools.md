# Operations: CLI command and MCP tool

Both call the same SDK and return the same result. Use whichever is
present. Every CLI command takes `--json`, which prints what the tool
returns. A deployment is `--deployment <name>` on the CLI and
`deployment=` on a tool; inside a clone of its repo it can be left out.

A refusal changes nothing: the CLI exits 2, and a tool returns
`{"refused": true, "message": ...}`. Relay the message; it names the
command that fixes it.

## The fleet and its definitions (no cloud)

| Operation | CLI | MCP tool | Notes |
|---|---|---|---|
| Where the fleet lives | `gapp fleet` | `gapp_fleet()` | a file on this machine, or a git repo; how many deployments |
| Move the fleet | `gapp fleet use <owner>/<repo> [--create]`, `gapp fleet use local` | `gapp_fleet_use(target, create=)` | any git host; refused when both places hold deployments |
| Copy it out or in | `gapp fleet export [<file>]`, `gapp fleet import <file>` | `gapp_fleet_export()`, `gapp_fleet_import(fleet)` | the tools take and return the document, not a path. Import replaces the whole fleet: get the user's yes |
| Every definition | `gapp list` | `gapp_list()` | name, repo, project, ref, gapp version, "upgrade available" |
| One definition | `gapp show` | `gapp_show(deployment=)` | `effective`: every setting a deploy would use and where it comes from; `settings`: the object `apply` takes |
| Define a deployment | `gapp add [<name>] --project <p> [--repo <r>] [--ref <r>]` | `gapp_add(name=, project=, repo=, ref=)` | in a clone, name and repo come from its remote |
| A second deployment of a repo | `gapp add <name> --copy-of <existing> [--project <p>] [--ref <r>]` | `gapp_add(name=, copy_of=, project=, ref=)` | copies settings except the domain |
| Rename | `gapp rename <old> <new>` | `gapp_rename(old, new)` | the name only; the id and cloud resources stay |
| Remove from the fleet | `gapp remove <name>` | `gapp_remove(name)` | the fleet only: the service, bucket and secrets stay in Google Cloud, and nothing in gapp takes them down. Confirm with the user first |
| Set or clear a value | `gapp set <key> <value>`, `gapp unset <key>` | `gapp_set(key, value, deployment=)`, `gapp_unset(key, deployment=)` | keys: `ref`, `repo`, `public`, `domain`, `start`, `memory`, `cpu`, `max_instances` |
| Extra APIs | `gapp apis add <api>`, `gapp apis remove <api>` | `gapp_apis(action, api, deployment=)` | |
| A variable | `gapp env set <NAME> <value>` / `--data-path [<sub>]` / `--secret [<secret-name>] [--generate]`, `gapp env unset <NAME>` | `gapp_env_set(name, value=, data_path=, secret=, generate=, deployment=)`, `gapp_env_unset(name, deployment=)` | one kind per variable |
| Replace all settings | `gapp apply <file> [--dry-run]` | `gapp_apply(settings, deployment=, dry_run=)` | what it leaves out goes back to its default; start from `gapp show` |
| Bring a definition to this gapp | `gapp upgrade` | `gapp_upgrade(deployment=)` | changes the definition only; the next deploy needs `upgrade` |

## Google Cloud (gcloud on this machine)

| Operation | CLI | MCP tool | Notes |
|---|---|---|---|
| Prepare a project | `gapp project setup <project>` | `gapp_project_setup(project)` | once per project, as an Owner. Run it again on a project a deploy says is not set up: it adds only what is missing |
| Deploy | `gapp deploy [--dry-run] [--rebuild] [--replace-domain] [--upgrade]` | `gapp_deploy(deployment=, dry_run=, rebuild=, replace_domain=, upgrade=)` | from a clone on the definition's ref; needs terraform. Dry run first |
| What is live | `gapp status` | `gapp_status(deployment=)` | URL, health, the domain's state, which gapp deployed it, the next step |
| Secrets and their state | `gapp secrets list` | `gapp_secrets_list(deployment=)` | |
| Is a secret set | `gapp secrets get <secret-name> [--plaintext]` | `gapp_secrets_get(name, plaintext=, deployment=)` | a hash and length unless plaintext is asked for |
| Set a secret's value | `gapp secrets set <secret-name> [--from-stdin]` | none | prompts; the user runs it. No tool takes a value |

## GitHub Actions (`gh` on this machine)

| Operation | CLI | MCP tool | Notes |
|---|---|---|---|
| Install the workflows | `gapp ci init` | `gapp_ci_init()` | in the fleet repo; again after installing another gapp |
| Whose repos runners deploy | `gapp ci sources add\|remove\|list [<owner>]` | `gapp_ci_sources(action, owner=)` | |
| Connect a project | `gapp ci projects connect <project>` | `gapp_ci_projects("connect", project)` | once per project; gcloud as an Owner and `gh` as an admin of the fleet repo; after `gapp project setup` |
| Connected projects | `gapp ci projects list` | `gapp_ci_projects("list")` | |
| Deploy on a runner | `gapp ci deploy [--upgrade] [--no-watch]` | `gapp_ci_deploy(deployment=, upgrade=, watch=)` | waits for the run and reports what it said |
| The last run, or what is live | `gapp ci status [--live]` | `gapp_ci_status(deployment=, live=)` | `live` starts a runner |

Every `gapp ci` result has `ok`. When true, `lines` is what to tell the
user and `next_step.hint` the next command. When false, `next_step.hint`
says why and what to run, and `refused` says whether nothing was changed
(true) or something failed part way (false). "Couldn't check ..." means
a read failed, in GitHub's words; it is not a verdict that something is
missing.

## This machine

| Operation | CLI | Notes |
|---|---|---|
| Profiles | `gapp config profile list\|use\|add\|remove\|rename` | a profile is a separate fleet and Google Cloud login |
| The login gapp uses | `gapp config account [<email>]` | for the active profile |

When neither surface is present, say so and go to
`references/environment.md`. Do not invent commands.
