# Operations: CLI command and MCP tool

Both surfaces call the same SDK and return the same structured
result. Use whichever is present. Arguments map one to one unless
noted.

| Operation | CLI | MCP tool | Notes |
|---|---|---|---|
| Live `gapp.yaml` JSON schema | `gapp manifest schema` | `gapp_schema` | single source of truth for fields |
| Annotated example | `gapp manifest example` | `gapp_manifest_example` | every key with a comment; valid YAML |
| Show this repo's settings | `gapp manifest show` | `gapp_manifest_show()` | every setting, set or not, with what it means; env variables by kind; how the app starts; `issues` for anything the schema refuses |
| Change a setting | `gapp manifest set <key> <value>`, `gapp manifest unset <key>` | `gapp_manifest_set(key, value, new_deployment=)`, `gapp_manifest_unset(key, new_deployment=)` | keys: name, public, domain, memory, cpu, max-instances. Validated; written only when it changes. A different name is a new deployment: refused unless `new_deployment`, which needs the user's yes |
| Environment variables | `gapp manifest env set <NAME> <value>` / `--secret <s> [--generate]` / `--data-path [<sub>]`, `gapp manifest env unset <NAME>` | `gapp_manifest_env_set(name, value=, secret=, generate=, data_path=)`, `gapp_manifest_env_unset(name)` | one kind per variable; replaces by name |
| View or set the account | `gapp config account <email>` | `gapp_user(account=)` | per-machine config |
| Initialize `gapp.yaml` | `gapp init [--fresh]` | `gapp_init(fresh=)` | idempotent; writes the deployment's name (`name_source` says where it came from); every other setting goes through `gapp manifest`. A broken gapp.yaml is never changed; `fresh` keeps it as gapp.yaml.bak and writes one with only the name (`fresh_result`), so get the user's yes first. Reports how the app starts (`start`: its Dockerfile, or its Procfile `web:` line); with neither, `next_step` says it isn't deployable yet. Takes no start command: that is the app's Procfile |
| GCP foundation | `gapp setup --project <project-id>` | `gapp_setup(project_id=)` | first run on a project needs the id; marks the project `gapp=hosted` once (never from a CI run) |
| Deploy | `gapp deploy [--project p] [--ref r] [--replace-domain]` | `gapp_deploy(project_id=, ref=, replace_domain=)` | build via Cloud Build, then terraform apply; blocking. Refuses before terraform to replace or remove a live domain mapping without `--replace-domain`, and to map a domain the deploying identity has not verified |
| Status | `gapp status [<name>] [--project p]` | `gapp_status(name=, project=)` | the direct system only (never CI); includes `next_step` and `domain` (`pending_dns`, `pending_cert`, `active`, `error`, with the CNAME target). With no usable Google Cloud identity it returns `next_step: sign in` with the login command rather than failing |
| List solutions | `gapp list` | `gapp_list(project_limit=)` | every deployment on the projects marked `gapp=hosted` |
| Secrets | `gapp secrets list [<name>]`, `gapp secrets get <secret-name> [--plaintext]`, `gapp secrets set <secret-name> [value]` | `gapp_secret_list(name=)`, `gapp_secret_get(name, plaintext=)`, `gapp_secret_set(name, value)` | `list` takes a deployment name (the checkout's by default); `get` and `set` use the checkout's gapp.yaml, where `name` is the secret's name. `get` returns a hash and length unless plaintext is requested |
| CI: the fleet repo | `gapp ci init [<owner>/<repo>] [--create] [--owner <org>]` | `gapp_ci_init(repo=, owner=, create=)` | the only operation that sets the fleet repo (gapp's local config). No args: shows the saved one, or finds the one repo tagged `gapp-ci` under the gh user (`owner=` for an org); several are listed, none gives the `--create` step. With a repo: sets it if it exists and installs or updates gapp's workflows and `gapp/deployments.yaml` (needs a `gh` that can write); `create` makes a new private fleet repo. A repo that does not exist, or exists when `create` is asked, is `ok: false` with the exact command |
| CI: allow a source owner | `gapp ci sources add\|remove\|list <owner>` | `gapp_ci_sources(action=, owner=)` | the fleet repo variable `GAPP_ALLOWED_SOURCES`; needs `gh` with write access |
| CI: trust a project | `gapp ci project setup <project>` | `gapp_ci_project_setup(project=)` | once per project, idempotent; the only `ci` operation that touches GCP. Needs gcloud signed in as an Owner of the project and `gh` as an admin of the fleet repo; creates or confirms WIF pool, provider, `gapp-deploy` service account and binding, and records them as the fleet repo's GitHub Environment `<project>`. Without gcloud the result says to run it where gcloud is |
| CI: a deployment | `gapp ci setup [<name>] --repo <owner>/<repo> --project <id> [--domain <host>] [--update] [--no-watch]` | `gapp_ci_setup(name=, repo=, project=, domain=, update=, watch=)` | mirrors `setup` for the fleet; never gcloud, never the working directory. The name defaults to the repo's name; the same name again changes the entry's domain (`domain` sets it, `""` removes it) only with `update`: without it the result is `ok: false` with `changes_shown` (before → after), so show it to the user and ask before passing `update`; every other setting comes from the app repo's own gapp.yaml. Re-running it with `update` also drops any key gapp.yaml does not have, which is how `gapp ci status` repairs an entry a deploy would refuse. Dispatches the register run, which writes the entry with its own token, so this needs only a `gh` that can dispatch. A project not yet trusted or an owner not yet allowed is `ok: false` with the verbatim one-time message; relay it as is. A repo already deployed under another name is refused with both ways on (update it, or `--copy-of`) |
| CI: another deployment of a repo | `gapp ci setup <new> --copy-of <existing> [--project <id>] [--domain <host>]` | `gapp_ci_setup(name=, copy_of=, project=, domain=)` | an environment or second instance: same repo, its own id, no domain or secret values; `project` defaults to the source's. |
| CI: rename | `gapp ci rename <old> <new>` | `gapp_ci_rename(old=, new=)` | the name only; id and GCP resources unchanged |
| CI: deploy | `gapp ci deploy <name> [--ref r] [--no-watch]` | `gapp_ci_deploy(name=, ref=, watch=)` | mirrors `deploy` for the fleet; dispatches `gapp-deploy.yml` with the deployment's name (and `ref` when given; without it the run deploys the app repo's default branch); needs `gh` logged in; any GitHub tool that dispatches that workflow with `deployment` and `ref` is equivalent. An unknown name is `ok: false` naming `ci setup` and `ci list`; a failed run comes with its link |
| CI: one deployment | `gapp ci status <name> [--verbose]` | `gapp_ci_status(name=)` | mirrors `status` for the fleet, GitHub only and fast: `repo`, `project`, `domain`, `overlay` (the gapp.yaml keys the entry sets), `last_deploy` (or that runs could not be read), `problem`, `next_step` and `live_hint`. `id` shows only with `--verbose` |
| CI: live service state | `gapp ci status <name> --live` | `gapp_ci_status(name=, live=True)` | dispatches the fleet's read-only status workflow, which runs `gapp status` for the deployment on a runner with the project's deploy identity; `live` is that result (the same dict `gapp_status` returns: services, URL, domain, next step). Takes a runner start; stores nothing; needs only a `gh` that can dispatch |
| CI: the fleet | `gapp ci list [--repo <owner>/<repo>] [--verbose]` | `gapp_ci_list(repo=, verbose=)` | mirrors `list` for the fleet: every deployment with name, id, repo, project, domain and keys; `verbose` adds the fleet repo's files (present, current, or not checked), variables and per-solution workflows left from gapp 3.x |

Two fleet systems, kept apart: the root operations deploy the local
checkout with a Google Cloud identity (the GCP label is the record) and
the `ci` operations manage the fleet through GitHub (the fleet repo's
`gapp/deployments.yaml` and the project's Environment are the record).
No `ci` operation reads the working directory, so every one takes names.
Every `ci` result carries `ok` and, when false, `next_step` with a plain
sentence and the exact fix; relay that instead of improvising one. Every
`ci` command takes `--json`, which prints that result dict and nothing else.
Something is "missing" only when GitHub confirmed it; "Couldn't check
..." means a read failed, in GitHub's words, and is not a verdict.

When neither surface is present, say so and go to
`references/environment.md`. Do not invent commands.
