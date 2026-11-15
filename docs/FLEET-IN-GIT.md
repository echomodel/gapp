# Why a fleet can live in a git repo

A fleet is kept in a file on one machine, or in a git repo (`gapp fleet
use <owner>/<repo>`), as `gapp.yaml` at its root. This
page records why the second is a git repo, why gapp makes the commits
itself, and why none of it has anything to do with CI. It is here so the
question is not worked through again.

## Why a git repo at all

Shared state needs a home. The usual alternative is a hosted service that
the tool's maker runs. gapp has no server and should not need one. A git
remote gives the same things with nothing to run or pay for: one place
several machines read, a history of every change, a way to undo one,
control over who can change it, and a backup.

Tools unrelated to deploying do the same. A password manager keeps its
encrypted store in a git repo and commits on every change. Dotfiles
managers keep a machine's configuration in one. A package manager keeps
its whole package list in git repos. Tools that say what should run in a
cluster read that from one.

## Why it fits gapp more than most

Those tools choose git as storage. gapp already works in it:

- **Git is already required.** A deploy builds from a clone, so everyone
  using gapp has git and access to a remote. A fleet in a repo adds no
  tool, account or login.
- **A definition is already about git.** It names a repo and a ref. What
  is running is then two git facts: this commit of the app, deployed by
  this version of its definition. With both in git, both have history
  and can be lined up.
- **Same habits.** Reading the fleet's history or undoing a change uses
  what people already use on their code.

This is a reason for git as the fleet's home. It is not a reason to keep
the fleet in an app's repo: a fleet covers many apps and holds the
operator's decisions, so it has a repo of its own.

## Why it is separate from CI

Where the fleet is kept and where a deploy runs are two questions.

| Question | Answers |
|---|---|
| Where is the fleet kept? | A file on this machine, or a git repo |
| Where does a deploy run? | Wherever `gapp deploy` is run: a workstation, any CI tool, a runner |

Neither decides the other. Keeping a fleet in a repo puts one file there
and nothing else. A change to the fleet is a commit, never a run.
Everything about GitHub Actions is under `gapp ci`, is opt-in, and
nothing outside it knows it exists. Someone who keeps their fleet in a
repo for the history, or to share it with a team, is not assumed to want
GitHub Actions, and another CI tool deploys by running `gapp deploy`.

Because the fleet in a repo is plain git (fetch, commit, push), it works
with any git host.

## Why gapp makes the commits

There are two styles.

| | The tool commits | You commit |
|---|---|---|
| A change is | One command | A command, then a commit, a push, a pull request, a merge and a pull |
| A clone of the fleet | Not needed; gapp keeps its own, out of sight | Needed on every machine, and kept up to date by hand |
| Acting on a stale copy | Cannot happen: gapp fetches before each read | Routine: a clone holds what it held at the last pull |
| Two changes at once | The second is refused and run again | Merged by hand |
| A change takes effect | At once | When someone merges it |
| Each change is reviewed | No | Yes |

gapp uses the first. A tool must work on a fresh machine with nothing to
configure, its operator should not need to know where state is kept, and
changing one setting should be one step. gapp never touches a clone of
yours.

The second style multiplies the steps of every change, for everyone,
every time. It buys one thing: review. For an operator who needs each
fleet change approved before it counts, that is worth the cost, and it
can be added without changing the first: a fleet that is a file in a
clone you point gapp at, where gapp writes the file and stops. It is not
the better way waiting to be built. It is a heavier way, for the one case
that needs it.

Review is not the same as protection. Reviewing fleet changes guards
nothing while anyone who can push a branch to the fleet repo can also run
a workflow there that signs in to Google Cloud. Review becomes a control
only together with the rest: the repo's main branch takes changes only by
merged pull request, Google Cloud trusts only that branch, and deploys
build only merged refs. All of it, or none.
