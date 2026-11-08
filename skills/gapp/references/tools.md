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
| Status | `gapp status [--solution s] [--env e]` | `gapp_status(solution=, env=)` | includes `next_step` and `domain` (`pending_dns`, `pending_cert`, `active`, `error`, with the CNAME target) |
| List solutions | `gapp list [--all]` | `gapp_list(all_owners=)` | `--all` crosses owner namespaces |
| Secrets | `gapp secrets list`, `gapp secrets get <name> [--plaintext]`, `gapp secrets set <name> [value]` | `gapp_secret_list`, `gapp_secret_get(name, plaintext=)`, `gapp_secret_set(name, value)` | `get` returns a hash and length unless plaintext is requested |
| Project env binding | `gapp projects set-env <id> <env> [--force]`, `gapp projects clear-env <id>`, `gapp projects list` | `gapp_projects_set_env`, `gapp_projects_clear_env`, `gapp_projects_list` | setup never writes `gapp-env`; only these do |
| CI: designate CI repo | `gapp ci init <repo> [--local-only]` | `gapp_ci_init(repo=, local_only=)` | once per operator; needs a `gh` login that can write to the CI repo. Installs or updates gapp's three files there (`gapp-deploy.yml`, `gapp-register.yml`, `gapp/solutions.yaml`) and reports whether `GAPP_ALLOWED_OWNERS`, `HOSTING_GITHUB_APP_ID` and `HOSTING_GITHUB_APP_PRIVATE_KEY` exist, with the `gh` commands to set them; never sets them. Where the API refuses writes, `next_step` says which machine to run it from |
| CI: register a solution | `gapp ci setup [--solution s] [--solution-repo owner/name] --project id [--domain host] [--no-watch]` | `gapp_ci_setup(solution=, solution_repo=, domain=, project=, watch=)` | dispatches the register workflow and reports the run (`dispatched`, `registered`, `conclusion`, `messages`, `next_step`); needs only a `gh` that can dispatch workflows, no gcloud, no API write. The first run on a project is the exception: it needs project admin (gcloud) to create WIF and the service account and record them as the CI repo's GitHub Environment `<project-id>`; on a machine without gcloud that case stops with `next_step`. Without `--project`, the project is found by label when gcloud is here, else the command stops naming the flag. `--domain` sets the entry's domain (wins over `gapp.yaml`); omitted, the existing one is kept; `""` removes it. A check this machine cannot perform comes back `checked: false` with a note, not an error; the run performs it |
| CI: deploy | `gapp ci trigger [--solution s] [--ref r] [--no-watch]` | `gapp_ci_trigger(solution=, ref=, watch=)` | dispatches `gapp-deploy.yml` with the solution's name; needs `gh` logged in; any GitHub tool that dispatches that workflow with `solution` and `ref` is equivalent. Refuses, with the reason, when the solution is not registered, its project has no Environment, or a private repo's hosting app variable or secret is known to be missing |
| CI: readiness | `gapp ci status [--solution s]` | `gapp_ci_status(solution=)` | reports `files`, `variables`, `solutions` (each with project, domain and `environment_exists`), `legacy_workflows`, and for the current solution `registered`, `solution_repo_private`, `domain`, `environment`, `environment_record`, `app_credentials`, and `next_step` naming the gap |

When neither surface is present, say so and go to
`references/environment.md`. Do not invent commands.
