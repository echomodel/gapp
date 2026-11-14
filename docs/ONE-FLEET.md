# One fleet: the proposed command design

**Not built.** This is the proposed interface for 4.0.0. The reasons are
in CONTRIBUTING, "Direction: one fleet". "Picks for the open questions"
gives a proposed answer to each question that section leaves open.

## Three things, three words

| Word | What it is | Where |
|---|---|---|
| Deployment definition | One name, one repo, one project, and that deployment's settings | An entry in the fleet. Required, and enough on its own |
| App manifest | Defaults that travel with the code | `gapp.yaml` in the app's repo. Optional |
| Deployment | The running service, its data and secrets | Google Cloud |

A definition has a **name** (unique in the fleet, the word you type), an
**id** (the prefix on its cloud resources, fixed, the name by default)
and a **project**.

## What gets built is in the definition

A definition can carry a **ref**: a branch, a tag or a commit of the app's
repo. A deploy builds that, and takes no ref of its own.

- No ref: the repo's default branch, at its tip.
- A branch: its tip at the time of the deploy. An environment is a
  definition whose ref is a branch.
- A tag or a commit: exactly that.

Deploying something else is a change to the definition (`gapp manifest
<name> set ref <r>`), then a deploy. So a release, a rollback and trying
a branch (a copy with that branch as its ref) are all fleet changes, and
the fleet records what each deployment is meant to run.

Why no `--ref` on deploy: with one, whoever can start a deploy can build
code that was never merged, whatever the app repo's branch rules say. The
ref belongs with the rest of the definition, where a fleet that takes
changes by pull request can review it.

## The commands

| Command | Does | Needs |
|---|---|---|
| `gapp project setup <project>` | Once per project: APIs, Cloud Build's permissions, the hosted mark. With a GitHub fleet, also the trust that lets runners deploy there | gcloud as an Owner (and `gh` as an admin of the fleet repo, for a GitHub fleet) |
| `gapp add [<name>] --project <p> [--repo <r>] [--ref <r>] [--domain <d>]` | Defines a deployment. Touches no cloud | the fleet |
| `gapp add <name> --copy-of <existing> [--project <p>] [--ref <r>]` | Another deployment of the same repo | the fleet |
| `gapp manifest [<name>] set\|unset\|env ...` | Changes one setting of a definition, in place | the fleet |
| `gapp rename <old> <new>` | Changes the name only | the fleet |
| `gapp deploy [<name>]` | Builds and deploys from this machine, at the definition's ref. Creates the deployment's bucket the first time | gcloud, Terraform, a clone |
| `gapp status [<name>]` | What is live | gcloud |
| `gapp secrets list\|get\|set ... [--deployment <name>]` | The deployment's secrets | gcloud |
| `gapp list` | Every definition, and where the fleet lives | the fleet |
| `gapp ci deploy <name>` | The same deploy, on a runner | `gh`, a GitHub fleet |
| `gapp ci status <name>` | The same status, on a runner | `gh`, a GitHub fleet |
| `gapp ci sources add\|remove\|list <owner>` | Whose repos runners may deploy | `gh` |
| `gapp fleet` | Where the fleet lives | nothing |
| `gapp fleet use <owner>/<repo>` / `gapp fleet use local` | Chooses where it lives. For a repo, installs the workflows | `gh` for a repo |
| `gapp fleet export` / `gapp fleet import <file>` | Moves a fleet. Import keeps a backup first | the fleet |
| `gapp fleet recover` | Rebuilds the fleet from the copies saved in each deployment's bucket | gcloud |

"The fleet" means: nothing for a local fleet; `gh` for a GitHub fleet,
where each change is one recorded run there.

Gone: `gapp setup`, `gapp ci setup`, `gapp ci rename`, `gapp ci list`,
`gapp ci manifest`, `gapp ci project setup`, `gapp ci init`. `gapp init`
stays, optional, for a repo owner who wants a `gapp.yaml`.

## With nothing specified

| Left out | What happens |
|---|---|
| Name, on `add`, in a clone | The repo's name, shown before anything is made. Refused if another repo has it |
| `--repo`, on `add`, in a clone | The clone's remote |
| Name, on `deploy`, `status`, `secrets`, `manifest`, in a clone | The one deployment of this repo. With several: refused, and each command printed with its name and project |
| Name, outside a clone | Required |
| A clone with no remote | Type a name; else `name:` in its gapp.yaml; else refused. A name alone matches only a definition with no repo |
| `--project`, on a copy | The original's project |
| `--ref`, on `add` | None: the repo's default branch |
| `--ref`, on a copy | The original's |
| Domain, on a copy | Not copied |
| The fleet's location | A file on this machine |

`--project <p>` on `deploy`, `status` and `secrets` narrows "this repo's
deployments" to the ones on that project. It never creates anything.

## Picks for the open questions

1. **First run with deployments already in the cloud:** the fleet is
   built from their labels, once, with nothing to edit, and the result is
   printed. A name is the id; where one id is on several projects, each
   gets `<id>-<project>`, changeable with `gapp rename`.
2. **Where the local file lives:** the config directory, one per profile.
3. **`gapp deploy` from this machine:** always from a clone of the
   definition's repo. gapp checks the clone's remote against the
   definition and refuses a mismatch.
4. **Apps with no remote:** allowed; the definition has no repo; only
   deployable from this machine.
5. **`name:` in gapp.yaml:** read only for a clone with no remote.
   Accepted and ignored otherwise, so no file needs editing.
6. **One file per deployment in a GitHub fleet:** no. One file.
7. **Changing a definition's settings:** in place, one command per
   setting (`gapp manifest`). No export, edit, upload.
8. **Changing a definition's repo** (renamed or moved): `gapp add <name>
   --repo <new> --move`, which says what it re-points.
9. **Removing a definition:** not in 4.0.0.
9a. **Fleet changes by pull request** (a GitHub fleet whose default
    branch takes changes only through a merged pull request, set up by
    one opt-in command): not in 4.0.0. The ref in the definition is
    what makes it worth adding later.
10. **Secrets from a session with no gcloud:** not in 4.0.0.
11. **A team requiring pull requests on the fleet repo:** not in 4.0.0.
12. **State:** as today, in the deployment's own bucket. The
    one-state-bucket-per-project idea waits.
13. **Workflow files in the fleet repo:** as today, pinned to the gapp
    tag that installed them; `gapp fleet use` updates them.
14. **A shortcut for picking among several deployments:** not in 4.0.0.
15. **Hosts other than GitHub:** an app repo on any host deploys from
    this machine; runners deploy GitHub repos only.

## Build order

Each stage is a release candidate.

1. The local fleet file; every root command reads it; existing
   deployments picked up from labels.
2. `gapp add`, `--copy-of`, `gapp rename`, `gapp manifest` on a
   definition; `gapp project setup`; `gapp setup` goes; deploy creates
   the bucket.
3. A GitHub fleet through the same commands; `gapp ci deploy` and `gapp
   ci status`; the `gapp ci` fleet commands go.
4. Export, import, backup, recover.
