# Why a fleet can live in a git repo

A fleet is kept in one of two places: a file on one machine, or a git
repo (`gapp fleet use <owner>/<repo>`), as `gapp.yaml` at its root. This
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

| Style | How it works | Good for |
|---|---|---|
| The tool commits | A command commits and pushes for you | One operator; no setup; no paths to know; machines with no clone of the fleet |
| You commit | The tool edits a file in your own clone; you commit, push and open a pull request | Teams that want each change reviewed |

gapp uses the first. A tool must work on a fresh machine with nothing to
configure, and its operator should not need to know where state is kept.
gapp keeps its own clone of the fleet repo out of sight and never touches
a clone of yours.

The cost is review: a direct push skips it. The second style answers
that, and can be added without changing the first: a fleet that is a file
in a clone you point gapp at, where gapp writes the file and stops. That
is also the simple route to requiring a merged pull request for every
fleet change.
