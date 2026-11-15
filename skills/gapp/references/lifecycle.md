# The steps in detail

Each step is idempotent and says what comes next. Find where the user
is (SKILL.md, "Which step is next") and do that step only.

## 1. Make the repo startable

`references/settings.md`: check the repo fits, and that it has a
Dockerfile or a Procfile. Propose a Procfile and get a yes before
writing one. Commit it.

## 2. Define the deployment

Needs no cloud login. Inside a clone of the app's repo:

```bash
gapp add --project <project-id>
```

The name and repo come from the clone's remote. From anywhere else:
`gapp add <name> --project <project-id> --repo <owner>/<repo>`.

- Ask for the project id if the user has not given one; never guess it.
- `--ref <branch|tag|commit>` sets what a deploy builds. Without it, the
  repo's default branch.
- If the repo already has a deployment, `gapp add` says so. A second
  one (test beside production) is `gapp add <name> --copy-of <existing>
  --project <other-project>`.
- The name can change later (`gapp rename`). The id, which names the
  cloud resources, cannot.

Then give it its settings (`references/settings.md`), and show the
result with `gapp show`.

## 3. Prepare the project (once per project)

```bash
gapp project setup <project-id>
```

Needs gcloud signed in as an Owner of the project. It turns on the APIs
every deployment needs, gives Cloud Build its permissions and marks the
project. A deploy to a project that has not had it is refused with this
command. On a machine with no Owner login, hand it off.

## 4. Secrets

`references/secrets.md`. `gapp secrets list`, then have the user set
each user-provided one that is missing. Generated ones need nothing.

## 5. Deploy

Always look first:

```bash
gapp deploy --dry-run
```

It says the commit, the project, whether this is a first deploy and
whether an upgrade is pending, and changes nothing.

### From this machine

```bash
gapp deploy
```

Needs gcloud, terraform and a clone of the definition's repo on the
definition's ref:

| The definition's ref | The clone must be |
|---|---|
| none | on the repo's default branch |
| a branch | on that branch; its unpushed commits are built |
| a tag or a commit | checked out at exactly that |

A refusal about the ref means either check that ref out, or change what
the deployment builds (`gapp set ref <ref>`). Ask the user which they
meant; do not change the ref to make a deploy go through.

Uncommitted changes are not deployed: the build is the committed tree.
Tell the user when the tree is dirty.

### On a runner

```bash
gapp ci deploy --deployment <name>
```

Needs only `gh`. One-time setup first (each step's output names the
next):

1. The fleet is in a GitHub repo: `gapp fleet use <owner>/<repo>
   --create`.
2. `gapp ci init` installs the workflows there.
3. `gapp project setup <project>` then `gapp ci projects connect
   <project>`, once per project, as an Owner.

`gapp ci deploy` refuses before starting a run when any of these is
missing, with the command to run. It waits for the run and reports what
the run said. A run builds the definition's ref from GitHub, so what it
deploys must be pushed.

Private app repos need a GitHub App the user creates in a browser; the
repo's `docs/CI.md`, "Private app repos", has the steps. Never ask for
its key in chat.

## 6. Verify and hand over

1. `gapp status` (or `gapp ci status --live` with no gcloud here): the
   URL, whether it answers, the domain's state.
2. If the app needs a generated credential the user never saw, fetch it
   with `gapp secrets get <secret-name> --plaintext` only when they ask,
   and hand it over out of band when you can.
3. Users, tokens and client registration belong to the app's own
   framework. Point at its README or skill.
4. Share the URL.

`status` calls the app's `/health` without credentials. An app with no
such route, or one that is not public, reads as not healthy while
working.

## Custom domain

Subdomains only (`app.example.com`).

1. **Verify ownership first.** Cloud Run maps a domain only for an
   identity that has verified it or a parent; gapp does not check this
   before deploying, and an unverified domain fails at the end of the
   deploy with Cloud Run's error. Ask the user whether the parent is
   verified; if not, they run `gcloud domains verify <parent>` and
   finish in Search Console.
   Verify at the narrowest parent that is useful: `example.com` covers
   every `*.example.com`. For deploys on a runner, the user also adds
   `gapp-deploy@<project-id>.iam.gserviceaccount.com` as a verified
   owner of that Search Console property.
2. **Set it:** `gapp set domain app.example.com`.
3. **Deploy.** The `.run.app` URL keeps working throughout.
4. **DNS.** The user adds the CNAME `gapp status` shows
   (`app.example.com` to `ghs.googlehosted.com`) at their DNS host. Ask
   which one they use and walk them to its page.
5. **Watch `gapp status`:** `pending_dns`, then `pending_cert` (usually
   minutes), then `active`; or `error` with Cloud Run's message.

**Changing or removing a domain takes the old one down.** A deploy that
would replace or remove a live domain is refused before anything is
built, naming the live domain. If the change is meant, deploy with
`--replace-domain`, then update the CNAME. If it is not, set the domain
back: `gapp set domain <the live one>`. A redeploy that keeps the domain
is never blocked.

## Ongoing operations

**Redeploy.** Ask whether the change affects how the app reads or
writes its data. If the layout changed, look at the bucket
(`gsutil ls gs://gapp-<id>-<project-id>/data/`) and raise it before
deploying. Then deploy by the usual path.

**Release, roll back, try a branch.** `gapp set ref <tag>` then deploy.
To try a branch without touching production, make a copy: `gapp add
<name> --copy-of <existing> --project <test-project> --ref <branch>`.

**Change a setting.** `gapp set ...` or `gapp env set ...`, then
deploy: a setting takes effect at the next deploy.

**Data on the volume.** An app that reads files from its data folder
gets them by copying to the same bucket path from a logged-in machine:
`gsutil -m rsync -r <folder> gs://gapp-<id>-<project-id>/data/<sub>`.

**A newer gapp.** `gapp list` and `gapp show` say "upgrade available"
(it works with the deployment as it is; upgrading is the user's choice),
"upgrade required" (it works with it only after `gapp upgrade`), or "too
old for this gapp" (the refusal names a gapp to install first).
Nothing changes until the user asks: `gapp upgrade --deployment <name>`
prints what will change and updates the definition, and says whether the
next deploy must be given `--upgrade` (yes when an earlier gapp deployed
it; it says when it could not tell). Do one deployment at a time.

**Several machines.** Keep the fleet in a git repo (`gapp fleet use`);
each machine then runs `gapp fleet use <owner>/<repo>` once and sees the
same fleet. A fleet command fails when the machine has no git access to
that repo: fix the access, do not copy the file.

**A login that stopped working** shows as an error naming the sign-in,
never as "not deployed". Take it at face value and fix the login.
