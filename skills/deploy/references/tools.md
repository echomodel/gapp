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
| Deploy | `gapp deploy [--env e] [--project p] [--ref r]` | `gapp_deploy(env=, project_id=, public=)` | build via Cloud Build, then terraform apply; blocking |
| Status | `gapp status [--solution s] [--env e]` | `gapp_status(solution=, env=)` | includes `next_step` |
| List solutions | `gapp list [--all]` | `gapp_list(all_owners=)` | `--all` crosses owner namespaces |
| Secrets | `gapp secrets list`, `gapp secrets get <name> [--plaintext]`, `gapp secrets set <name> [value]` | `gapp_secret_list`, `gapp_secret_get(name, plaintext=)`, `gapp_secret_set(name, value)` | `get` returns a hash and length unless plaintext is requested |
| Project env binding | `gapp projects set-env <id> <env> [--force]`, `gapp projects clear-env <id>`, `gapp projects list` | `gapp_projects_set_env`, `gapp_projects_clear_env`, `gapp_projects_list` | setup never writes `gapp-env`; only these do |
| CI: designate CI repo | `gapp ci init <repo> [--local-only]` | `gapp_ci_init(repo=, local_only=)` | once per operator |
| CI: wire a solution | `gapp ci setup [--solution s] [--solution-repo owner/name]` | `gapp_ci_setup(solution=, solution_repo=)` | creates WIF, service account, workflow; needs project admin and CI repo write |
| CI: deploy | `gapp ci trigger [--solution s] [--ref r] [--no-watch]` | `gapp_ci_trigger(solution=, ref=, watch=)` | needs `gh` logged in; any GitHub tool that dispatches the workflow is equivalent |
| CI: readiness | `gapp ci status [--solution s]` | `gapp_ci_status(solution=)` | |

When neither surface is present, say so and go to
`references/environment.md`. Do not invent commands.
