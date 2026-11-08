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
| Status | `gapp status [--solution s] [--env e]` | `gapp_status(solution=, env=)` | the direct system only (never CI); includes `next_step` and `domain` (`pending_dns`, `pending_cert`, `active`, `error`, with the CNAME target). With no usable Google Cloud identity it returns `next_step: sign in` with the login command rather than failing |
| List solutions | `gapp list [--all]` | `gapp_list(all_owners=)` | `--all` crosses owner namespaces |
| Secrets | `gapp secrets list`, `gapp secrets get <name> [--plaintext]`, `gapp secrets set <name> [value]` | `gapp_secret_list`, `gapp_secret_get(name, plaintext=)`, `gapp_secret_set(name, value)` | `get` returns a hash and length unless plaintext is requested |
| Project env binding | `gapp projects set-env <id> <env> [--force]`, `gapp projects clear-env <id>`, `gapp projects list` | `gapp_projects_set_env`, `gapp_projects_clear_env`, `gapp_projects_list` | setup never writes `gapp-env`; only these do |
| CI: designate CI repo | `gapp ci init <repo> [--local-only]` | `gapp_ci_init(repo=, local_only=)` | once per operator; needs a `gh` login that can write to the CI repo (GitHub API). Installs or updates gapp's three files there (`gapp-deploy.yml`, `gapp-register.yml`, `gapp/solutions.yaml`) and reports whether `GAPP_ALLOWED_SOURCES`, `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` exist, with the commands to set them; never sets them. Where the API refuses writes, `next_step` says which machine to run it from |
| CI: trust a project | `gapp ci project setup <project>` | `gapp_ci_project_setup(project=)` | once per project, idempotent; the only `ci` operation that touches GCP. Needs gcloud signed in as an Owner of the project and `gh` as an admin of the CI repo; creates or confirms WIF pool, provider, `gapp-deploy` service account and binding, and records them as the CI repo's GitHub Environment `<project>`. On a machine without gcloud the result says to run it where gcloud is |
| CI: allow a source owner | `gapp ci sources add\|remove\|list [<owner>]` | `gapp_ci_sources(action=, owner=)` | the CI repo variable `GAPP_ALLOWED_SOURCES`; needs `gh` with write access to the CI repo |
| CI: register a solution | `gapp ci setup [--solution s] [--solution-repo owner/name] --project id [--domain host] [--no-watch]` | `gapp_ci_setup(solution=, solution_repo=, project=, domain=, watch=)` | never uses gcloud; needs only a `gh` that can dispatch workflows. Dispatches the register workflow and reports the run (`dispatched`, `registered`, `result_code`, `messages`, `next_step`). `--project` is required. A project not yet trusted, or a source owner not yet allowed, comes back as `ok: false` with the verbatim one-time message naming `gapp ci project setup` or `gapp ci sources add`; relay it as is. `--domain` sets the entry's domain (wins over `gapp.yaml`); omitted, the existing one is kept; `""` removes it |
| CI: deploy | `gapp ci deploy [--solution s] [--ref r] [--no-watch]` | `gapp_ci_deploy(solution=, ref=, watch=)` | dispatches `gapp-deploy.yml` with the solution's name; needs `gh` logged in; any GitHub tool that dispatches that workflow with `solution` and `ref` is equivalent. An unregistered solution comes back as `ok: false` with the `ci setup` step; a failed run with its link. (`ci trigger` / `gapp_ci_trigger` are hidden aliases for one release) |
| CI: readiness | `gapp ci status [--solution s]` | `gapp_ci_status(solution=)` | the CI system's report, never touching GCP: `files` (present, current), `variables`, `solutions` (each with project, domain, gapp_ref and `environment_exists`), `environments`, `legacy_workflows`, and for the current solution `registered`, `environment`, `environment_record`, and `next_step` naming the gap |

Two fleet systems, one rule: the root operations are the direct system
(Google Cloud identity; the GCP label is the record) and the `ci`
operations are the CI system (GitHub; `gapp/solutions.yaml` and the
project's Environment are the record). A solution is deployed by one of
them, and no command reads the other's state. Every `ci` result carries
`ok` and, when false, `next_step` with the exact step; relay that step
instead of improvising one.

When neither surface is present, say so and go to
`references/environment.md`. Do not invent commands.
