# One fleet: why, and the build order

The interface is in [INTERFACE.md](INTERFACE.md). The reasons for one
fleet are in CONTRIBUTING, "Direction: one fleet". This page holds two
things that page does not: why a deploy takes no ref, and the order the
work is built in.

## What gets built is in the definition

A definition can carry a **ref**: a branch, a tag or a commit of the app's
repo. A deploy builds that, and takes no ref of its own.

- No ref: the repo's default branch, at its tip.
- A branch: its tip at the time of the deploy. An environment is a
  definition whose ref is a branch.
- A tag or a commit: exactly that.

Deploying something else is a change to the definition (`gapp set ref
<r>`), then a deploy. So a release, a rollback and trying a branch (a copy
with that branch as its ref) are all fleet changes, and the fleet records
what each deployment is meant to run.

Why no `--ref` on deploy: with one, whoever can start a deploy can build
code that was never merged, whatever the app repo's branch rules say. The
ref belongs with the rest of the definition, where a fleet that takes
changes by pull request can review it.

The gapp version that deploys a deployment is in the definition for the
same reason.

## Build order

1. A test that compares the real CLI to INTERFACE.md.
2. The entry file in its final shape.
3. `add`, `rename`, `list`, `show`, `upgrade` and the settings commands,
   on a local fleet.
4. `deploy`, `status` and `secrets` read the fleet; `project setup`; the
   version label and its checks; the commands the page does not list go.
5. A GitHub fleet through the same commands.
6. `fleet export`, `import` and `recover`; then reading `app.json`.

Built so far: where the fleet lives and reading it there
(`gapp/admin/sdk/fleetstore.py`), the rules for which deployment a command
means (`gapp/admin/sdk/lookup.py`), and the ref as part of a definition.
