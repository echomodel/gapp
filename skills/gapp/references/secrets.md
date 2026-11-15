# Secrets

Read this for anything about a deployment's secrets: naming them,
checking readiness before a deploy, setting or reading values.

## How secrets work

A secret is a value in Secret Manager, in the deployment's project,
that gapp gives the container as a variable. The definition names it;
the value never leaves Secret Manager.

```bash
gapp env set UPSTREAM_API_KEY --secret
gapp env set DB_PASSWORD --secret --generate
gapp env set DB_URL --secret database-url
```

| Kind | Command | Its value |
|---|---|---|
| User-provided | `--secret` | Must exist before a deploy. For upstream API keys and third-party credentials |
| Generated | `--secret --generate` | gapp makes a random value at the first deploy and never again. For anything that only needs to be random and stable |

The secret's name defaults to the variable's in lower case with dashes
(`UPSTREAM_API_KEY` reads `upstream-api-key`); give another after
`--secret`. In Secret Manager its id is `<deployment id>-<secret-name>`,
so two deployments in one project never collide. Every gapp command
takes the short name.

## Before a deploy

A deploy with a user-provided secret that has no value is refused, so
check first:

```bash
gapp secrets list
```

Each row has `generate` (from the definition) and `status`, the
secret's state in Secret Manager: `ready`, `missing`, `unattached` or
`conflict`.

- Generated, `missing`: fine. The deploy makes it.
- Any, `ready`: fine.
- User-provided, `missing`: have the user set it, then deploy.

`status` says nothing about the running service. A value set after the
last deploy reaches the app at the next deploy.

The user sets a value from a terminal. The command prompts with hidden
input, which keeps the value out of shell history and transcripts:

```bash
gapp secrets set upstream-api-key --deployment my-app
```

`--from-stdin` reads it from a pipe instead. There is no tool for this
and no argument that takes the value. Never ask the user to paste a
secret into chat, and never put one in a file, a plain variable or the
fleet. On a machine with no gcloud login, hand this step off
(`references/environment.md`).

`secrets list` also reports `unattached`, `conflict` and orphans, each
with what to run. Those come from ownership labels: gapp labels the
secrets it creates and never adopts one it did not label. The repo's
`docs/SECRETS.md` explains each state and its fix.

## After a deploy

- Rotate a user-provided secret: `gapp secrets set <secret-name>`, then
  redeploy so the service picks up the new version.
- Check one without printing it: `gapp secrets get <secret-name>` gives
  its length and a hash. `--plaintext` prints the value; use it only
  when the user asks to see it.
- A generated secret is never rotated by a deploy.
