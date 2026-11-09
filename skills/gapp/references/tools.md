# Operations: CLI command and MCP tool

Both surfaces call the same SDK and return the same structured
result. Use whichever is present. Arguments map one to one unless
noted.

| Operation | CLI | MCP tool | Notes |
|---|---|---|---|
| Live `gapp.yaml` JSON schema | `gapp manifest schema` | `gapp_schema` | single source of truth for fields |
| View or set account and owner | `gapp config account <email>`, `gapp config owner <name>` | `gapp_user(account=, app_owner=)` | per-machine config |
| Initialize or update `gapp.yaml` | `gapp init` | `gapp_init(entrypoint=, secrets=, domain=)` | idempotent; non-None params are written, others kept; `domain=""` removes a domain |
| GCP foundation | `gapp setup <project-id> [--env e] [--force]` | `gapp_setup(project_id=, env=, force=)` | first run on a project needs the id; `force` bypasses the cross-owner check |
| Deploy | `gapp deploy [--env e] [--project p] [--ref r] [--replace-domain]` | `gapp_deploy(env=, project_id=, ref=, replace_domain=)` | build via Cloud Build, then terraform apply; blocking. Refuses before terraform to replace or remove a live domain mapping without `--replace-domain`, and to map a domain the deploying identity has not verified |
| Status | `gapp status [--solution s] [--env e] [--project p]` | `gapp_status(solution=, env=, project=)` | the direct system only (never CI); includes `next_step` and `domain` (`pending_dns`, `pending_cert`, `active`, `error`, with the CNAME target). With no usable Google Cloud identity it returns `next_step: sign in` with the login command rather than failing |
| List solutions | `gapp list [--all]` | `gapp_list(all_owners=)` | `--all` crosses owner namespaces |
| Secrets | `gapp secrets list`, `gapp secrets get <name> [--plaintext]`, `gapp secrets set <name> [value]` | `gapp_secret_list`, `gapp_secret_get(name, plaintext=)`, `gapp_secret_set(name, value)` | `get` returns a hash and length unless plaintext is requested |
| Project env binding | `gapp projects set-env <id> <env> [--force]`, `gapp projects clear-env <id>`, `gapp projects list` | `gapp_projects_set_env`, `gapp_projects_clear_env`, `gapp_projects_list` | setup never writes `gapp-env`; only these do |
| CI: the fleet repo | `gapp ci init [<owner>/<repo>] [--create] [--owner <org>]` | `gapp_ci_init(repo=, owner=, create=)` | the only operation that sets the fleet repo (gapp's local config). No args: shows the saved one, or finds the one repo tagged `gapp-ci` under the gh user (`owner=` for an org); several are listed, none gives the `--create` step. With a repo: sets it if it exists and installs or updates gapp's workflows and `gapp/deployments.yaml` (needs a `gh` that can write); `create` makes a new private fleet repo. A repo that does not exist, or exists when `create` is asked, is `ok: false` with the exact command |
| CI: allow a source owner | `gapp ci sources add\|remove\|list <owner>` | `gapp_ci_sources(action=, owner=)` | the fleet repo variable `GAPP_ALLOWED_SOURCES`; needs `gh` with write access |
| CI: trust a project | `gapp ci project setup <project>` | `gapp_ci_project_setup(project=)` | once per project, idempotent; the only `ci` operation that touches GCP. Needs gcloud signed in as an Owner of the project and `gh` as an admin of the fleet repo; creates or confirms WIF pool, provider, `gapp-deploy` service account and binding, and records them as the fleet repo's GitHub Environment `<project>`. Without gcloud the result says to run it where gcloud is |
| CI: a deployment | `gapp ci setup [<name>] --repo <owner>/<repo> --project <id> [--domain <host>] [--config <file>] [--no-watch]` | `gapp_ci_setup(name=, repo=, project=, domain=, config=, watch=)` | mirrors `setup` for the fleet; never gcloud, never the working directory. The name defaults to the repo's name; the same name again updates the entry's gapp.yaml keys (`domain` sets one, `""` removes it; `config` any). Dispatches the register run, which writes the entry with its own token, so this needs only a `gh` that can dispatch. A project not yet trusted or an owner not yet allowed is `ok: false` with the verbatim one-time message; relay it as is. A repo already deployed under another name is refused with both ways on (update it, or `--copy-of`) |
| CI: another deployment of a repo | `gapp ci setup <new> --copy-of <existing> [--project <id>] [--domain <host>]` | `gapp_ci_setup(name=, copy_of=, project=, domain=)` | an environment or second instance: same repo and gapp.yaml keys, its own id, no domain or secret values; `project` defaults to the source's. The result lists `copied_env` and the `secrets` the new deployment needs set on its project |
| CI: rename | `gapp ci rename <old> <new>` | `gapp_ci_rename(old=, new=)` | the name only; id and GCP resources unchanged |
| CI: deploy | `gapp ci deploy <name> [--ref r] [--no-watch]` | `gapp_ci_deploy(name=, ref=, watch=)` | mirrors `deploy` for the fleet; dispatches `gapp-deploy.yml` with the deployment's name; needs `gh` logged in; any GitHub tool that dispatches that workflow with `deployment` and `ref` is equivalent. An unknown name is `ok: false` naming `ci setup` and `ci list`; a failed run comes with its link |
| CI: one deployment | `gapp ci status <name> [--verbose]` | `gapp_ci_status(name=)` | mirrors `status` for the fleet, GitHub only and fast: `repo`, `project`, `domain`, `overlay` (the gapp.yaml keys the entry sets), `environment_record`, `last_deploy` (or that runs could not be read), `problem`, `next_step` and `live_hint`. `id` shows only with `--verbose` |
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
