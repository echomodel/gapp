"""Deploying a deployment of the fleet, and reading what is live.

What gapp writes in a project:

  Project label, by `gapp project setup` (an Owner):
    gapp=hosted                    the project is set up for gapp deployments

  Labels on a deployment's bucket, by gapp once a deploy has succeeded, and
  on its Cloud Run service, by Terraform:
    gapp-deployment=<id>
    gapp-version=<the gapp version that last deployed it>

A deployment's project, id and settings come from its definition in the
fleet. Nothing is searched for.

See CONTRIBUTING.md for the full model.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from gapp.admin.sdk.cloud import get_provider
from gapp.admin.sdk.cloud.base import CloudProvider, CloudAccessError, ci_identity_hint, on_ci_runner
from gapp.admin.sdk.manifest import get_domain, get_service_config, get_public
from gapp.admin.sdk.models import DomainStatus

# The project label that marks a project as set up for gapp deployments.
# Only an Owner writes it (`gapp project setup`); a CI run never does.
HOSTED_LABEL = "gapp"
HOSTED_VALUE = "hosted"

# Labels on a deployment's own resources: its bucket and its Cloud Run service.
DEPLOYMENT_LABEL = "gapp-deployment"
VERSION_LABEL = "gapp-version"

# Region every gapp service, image, and domain mapping lives in.
REGION = "us-central1"

# The APIs every deployment needs, turned on by `gapp project setup`.
# cloudresourcemanager backs the project reads the next steps make
# (project number, labels), so it must be on before them.
FOUNDATION_APIS = (
    "cloudresourcemanager.googleapis.com",
    "run.googleapis.com", "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
)


class GappSDK:
    """Deploys the fleet's deployments and reads what is live, through a cloud provider."""

    def __init__(self, provider: Optional[CloudProvider] = None):
        self.provider = provider or get_provider()

    def get_bucket_name(self, deployment_id: str, project_id: str) -> str:
        """The deployment's bucket. A deployment id is unique within its project."""
        return f"gapp-{deployment_id}-{project_id}".lower()

    def _identity_hint(self, project_id: Optional[str] = None) -> str:
        """What to do when no Google Cloud identity this machine holds works."""
        from gapp.admin.sdk.util import configured_account
        account = configured_account()
        if on_ci_runner():
            return ci_identity_hint(account, project_id)
        who = f" for the configured account {account}" if account else ""
        login = f"gcloud auth login {account}" if account else "gcloud auth login"
        return (
            f"No Google Cloud identity this machine can use{who}: the login is missing, expired, "
            f"or not allowed to read the project. Sign in with `{login}` (or point gapp at another "
            f"account with `gapp config account <email>`), then run `gapp status` again."
        )

    def _domain_status(self, project_id: str, domain: str) -> DomainStatus:
        """Fill DomainStatus from the Cloud Run domain mapping's conditions."""
        mapping = self.provider.get_domain_mapping(project_id, REGION, domain)
        if not mapping:
            return DomainStatus(
                name=domain, status="error",
                detail="Terraform state records this domain, but Cloud Run has no "
                       "mapping for it. Redeploy to recreate the mapping.",
            )
        status_block = mapping.get("status") or {}
        state, detail = _domain_status_from_conditions(status_block.get("conditions") or [])
        cname = next(
            (r.get("rrdata") for r in status_block.get("resourceRecords") or []
             if (r.get("type") or "").upper() == "CNAME" and r.get("rrdata")),
            None,
        )
        kwargs = {"cname_target": cname.rstrip(".")} if cname else {}
        return DomainStatus(name=domain, status=state, detail=detail, **kwargs)

    # -- Internal helpers --

    def _check_domain_safety(self, service_name, project_id, requested_domain,
                             bucket_name, state_prefix, replace_domain):
        """Refuse a deploy that would take a live custom domain down.

        Terraform replaces a changed `google_cloud_run_domain_mapping` by
        destroying the old one first, so a typo, a placeholder, or an omitted
        `domain:` silently removes the live mapping. Two guards, both before
        any build or apply:

        1. A live mapping exists (recorded in Terraform state) and this deploy
           maps a different domain or none: refuse unless replace_domain.
        2. This deploy creates or replaces a mapping: the deploying identity
           must have verified the domain or a parent, or Cloud Run rejects
           the mapping after the old one is already gone.

        A redeploy that keeps the same domain changes nothing about the
        mapping, so neither guard applies to it.
        """
        requested = (requested_domain or "").strip().lower()
        # Fail closed: a state that cannot be read is not "no live mapping",
        # or this deploy could remove or replace a domain that is serving.
        try:
            outputs = self.provider.get_infrastructure_outputs(
                _get_staging_dir(service_name), bucket_name, state_prefix, strict=True,
            ) or {}
        except CloudAccessError as e:
            raise CloudAccessError(
                f"Refusing to deploy '{service_name}': its Terraform state could not be read, so gapp "
                f"cannot tell whether a custom domain is mapped to it now, and deploying could take "
                f"that domain down. Nothing was built or changed. Fix the access to the state bucket "
                f"{bucket_name} for this identity, then deploy again. ({e})"
            ) from e
        live = (outputs.get("custom_domain") or "").strip().lower()

        if live and live != requested and not replace_domain:
            if requested:
                raise RuntimeError(
                    f"Refusing to replace the live domain mapping of '{service_name}': "
                    f"{live} is mapped now and this deploy would map {requested} instead. "
                    f"Terraform destroys the old mapping before creating the new one, so "
                    f"{live} would stop serving. If the change is intended, re-run with "
                    f"--replace-domain (replace_domain=True); otherwise set the deployment's "
                    f"domain back: gapp set domain {live}"
                )
            raise RuntimeError(
                f"Refusing to remove the live domain mapping {live} of '{service_name}': "
                f"this deploy sets no domain. "
                f"If removing it is intended, re-run with --replace-domain "
                f"(replace_domain=True); otherwise set the deployment's domain: "
                f"gapp set domain {live}"
            )

        if requested and requested != live:
            verified = self.provider.list_verified_domains()
            if not _domain_is_verified(requested, verified):
                parent = requested.split(".", 1)[1] if requested.count(".") >= 2 else requested
                raise RuntimeError(
                    f"The deploying identity has not verified ownership of {requested} or a "
                    f"parent domain, so Cloud Run would refuse the domain mapping. Verify "
                    f"once, then redeploy:\n"
                    f"  gcloud domains verify {parent}      # opens Search Console; verify "
                    f"{parent} or the narrowest parent you control\n"
                    f"  gcloud domains list-user-verified   # must list it afterwards\n"
                    f"A domain is verified per identity. For CI deploys, also add the deploy "
                    f"service account (gapp-deploy@{project_id}.iam.gserviceaccount.com) as a "
                    f"verified owner of that Search Console property."
                )

    # -- The fleet's commands: a definition in, a deployment out --

    def project_setup(self, project_id: str) -> dict:
        """Make a project ready for gapp deployments. Once per project; running it again only adds what is missing.

        Turns on the APIs every deployment needs, gives Cloud Build its
        permissions, and marks the project. Nothing about CI.
        """
        from gapp.admin.sdk import fleet
        from gapp.admin.sdk.definitions import Refused
        try:
            project_id = fleet.validate_project(project_id)
        except fleet.FleetRefusal as e:
            raise Refused(e.message) from e
        for api in FOUNDATION_APIS:
            self.provider.enable_api(project_id, api)
        self.provider.ensure_build_permissions(project_id)
        labels = dict(self.provider.get_project_labels(project_id) or {})
        marked = labels.get(HOSTED_LABEL) == HOSTED_VALUE
        if not marked:
            self.provider.set_project_labels(project_id, {**labels, HOSTED_LABEL: HOSTED_VALUE})
        return {"project": project_id, "apis": list(FOUNDATION_APIS), "was_set_up": marked}

    def _clone_of(self, definition: dict, name: str, cwd: Path) -> Path:
        """The clone a deploy builds from: the folder's git root, whose remote must be the definition's repo."""
        from gapp.admin.sdk import lookup
        from gapp.admin.sdk.definitions import Refused
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=cwd)
        repo = definition.get("repo")
        if res.returncode != 0:
            raise Refused(f"`gapp deploy` builds from a clone. Run it in a clone of {repo or 'the app'}.")
        root = Path(res.stdout.strip())
        if repo:
            remotes = lookup.clone_remotes(root)
            if not any(r.repo.lower() == repo.lower() for r in remotes.values()):
                here = ", ".join(sorted({r.repo for r in remotes.values()})) or "a repo with no remote"
                raise Refused(f"'{name}' runs {repo}, and this clone is {here}. Run it in a clone of {repo}.")
        return root

    def _check_at_ref(self, root: Path, definition: dict, name: str) -> str:
        """Refuse unless the clone is on what the definition builds. Returns the commit.

        A branch (or, with no ref, the repo's default branch): the clone
        must have that branch checked out, and its own commits on it are
        built, pushed or not. A tag or a commit: the clone must be at
        exactly that commit.
        """
        from gapp.admin.sdk.definitions import Refused

        def git(*args):
            r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=root)
            return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

        def commit(rev):
            return git("rev-parse", "--verify", "-q", f"{rev}^{{commit}}")

        head = commit("HEAD")
        if not head:
            raise Refused("This clone has no commit yet, so there is nothing to build.")
        on_branch = git("symbolic-ref", "--short", "-q", "HEAD")
        ref = definition.get("ref")
        change = f"or change what it builds: gapp set ref <ref> --deployment {name}"
        if ref:
            is_branch = bool(git("show-ref", "--verify", f"refs/heads/{ref}")
                             or git("show-ref", "--verify", f"refs/remotes/origin/{ref}"))
            branch = ref if is_branch else None
        else:
            default = git("symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD")
            branch = default.split("/", 1)[1] if default and "/" in default else None
            if branch is None:
                return head                       # the default branch is not known here; build what is checked out
        if branch:
            if on_branch != branch:
                here = f"on '{on_branch}'" if on_branch else f"at {head[:12]}, on no branch"
                what = f"'{branch}'" + ("" if ref else ", the repo's default branch")
                raise Refused(f"'{name}' builds {what}, and this clone is {here}. Check out {branch}, {change}")
            return head
        wanted = commit(ref)
        if not wanted:
            raise Refused(f"'{name}' builds '{ref}', which this clone does not have. Fetch it, then check it out.")
        if wanted != head:
            raise Refused(f"'{name}' builds '{ref}' ({wanted[:12]}), and this clone is at {head[:12]}. "
                          f"Check that out, {change}")
        return head

    def deploy_definition(self, deployment: Optional[str] = None, *, cwd: Optional[Path] = None,
                          dry_run: bool = False, rebuild: bool = False, replace_domain: bool = False,
                          upgrade: bool = False) -> dict:
        """Build a deployment's ref from this clone and deploy it, from its definition in the fleet.

        A refusal (`definitions.Refused`) means nothing was built or
        changed. `dry_run` reports what a deploy would do, including
        whether it would apply an upgrade, and refuses nothing about that.
        """
        from gapp.admin.sdk import appjson, definitions, entryfile, settings as settings_mod
        from gapp.admin.sdk.definitions import Refused
        from gapp.admin.sdk.start import start_method_of, missing

        cwd = Path(cwd or Path.cwd())
        _, deployments = definitions._load()
        name = definitions.which(deployments, deployment, cwd, "gapp deploy")
        definition = deployments[name]
        try:
            entryfile.require_current(definition, name, "deploy it from this machine")
            settings_mod.validate(definition.get("settings"))
        except (entryfile.EntryFileRefusal, settings_mod.SettingsRefusal) as e:
            raise Refused(e.message) from e
        root = self._clone_of(definition, name, cwd)
        sha = self._check_at_ref(root, definition, name)
        # The definition wins; the app's own app.json, at the commit being
        # built, fills in the variables the definition leaves out.
        try:
            declared = appjson.read(root)
        except appjson.AppJsonRefusal as e:
            raise Refused(f"'{name}' cannot be deployed: {e.message} Nothing was built or changed.") from e
        filled = appjson.fill(definition.get("settings"), declared)
        manifest = settings_mod.as_manifest(definition["id"], filled["settings"])
        procfile = root / "Procfile"
        start = start_method_of(dockerfile=(root / "Dockerfile").is_file(),
                                procfile=procfile.read_text() if procfile.is_file() else None,
                                start=manifest.get("start"))
        if not start["method"]:
            raise Refused(missing(name, start, consequence=", so nothing was built or deployed"))

        deployment_id, project = definition["id"], definition["project"]
        bucket = self.get_bucket_name(deployment_id, project)
        has_bucket = self.provider.bucket_exists(project, bucket)
        label = (self.provider.list_bucket_labels(project).get(bucket) or {}).get(VERSION_LABEL) if has_bucket else None
        last = settings_mod.label_version(label)
        if last is None:
            standing = "first"
        else:
            theirs, mine = entryfile.version_key(last), entryfile.version_key(definition["gapp"])
            standing = "same" if theirs == mine else ("upgrade" if theirs < mine else "ahead")
        plan = {
            "deployment": name, "id": deployment_id, "project": project, "repo": definition.get("repo"),
            "ref": definition.get("ref"), "commit": sha[:12], "gapp": definition["gapp"],
            "last_deployed_by": last, "upgrade_pending": standing == "upgrade", "first_deploy": standing == "first",
            "bucket": bucket, "start": start["method"],
            "app_json": "none" if declared is None else "read",
            "defaults_from_app_json": filled["defaults"], "missing_variables": filled["missing"],
            "warnings": appjson.unlisted_lines(name, filled["unlisted"]),
        }
        if dry_run:
            return {**plan, "dry_run": True}
        if filled["missing"]:
            raise Refused(appjson.missing_text(name, filled["missing"]))
        if standing == "ahead":
            raise Refused(
                f"'{name}' was last deployed by gapp {last}, and its definition names gapp {definition['gapp']}. "
                f"A deployment does not go back. Nothing was changed."
            )
        if standing == "upgrade" and not upgrade:
            raise Refused(
                f"'{name}' was last deployed by gapp {last}, and its definition is on gapp {definition['gapp']}. "
                f"This deploy would apply that upgrade. To go ahead: gapp deploy --deployment {name} --upgrade"
            )
        if upgrade and standing != "upgrade":
            raise Refused(f"No upgrade is pending for '{name}'. Deploy it without --upgrade.")
        if (self.provider.get_project_labels(project) or {}).get(HOSTED_LABEL) != HOSTED_VALUE:
            raise Refused(f"{project} is not set up for gapp yet. Once per project: gapp project setup {project}")

        for api in (manifest.get("prerequisites") or {}).get("apis", []):
            self.provider.enable_api(project, api)
        if not has_bucket:
            self.provider.create_bucket(project, bucket)
        labels = {DEPLOYMENT_LABEL: deployment_id.lower(), VERSION_LABEL: settings_mod.version_label(definition["gapp"])}

        state_prefix = "terraform/state"
        self._check_domain_safety(deployment_id, project, get_domain(manifest), bucket, state_prefix, replace_domain)
        self.provider.ensure_artifact_registry(project, REGION)
        short = sha[:12]
        image = f"{REGION}-docker.pkg.dev/{project}/gapp/{deployment_id}:{short}"
        if rebuild or not self.provider.image_exists(project, REGION, deployment_id, short):
            build_dir = _prepare_build_dir(root, image, start, ref="HEAD")
            try:
                self.provider.submit_build_sync(project, Path(build_dir), image)
            finally:
                shutil.rmtree(build_dir, ignore_errors=True)
        from gapp.admin.sdk.secrets import validate_declared_secrets, materialize_generated_secrets
        validate_declared_secrets(project, deployment_id, manifest)
        materialize_generated_secrets(project, deployment_id, manifest)
        tfvars = _build_tfvars(
            deployment_id, project, image, get_service_config(manifest), root,
            get_public(manifest), get_domain(manifest), bucket,
            solution_name=deployment_id, labels=labels, manifest=manifest,
        )
        outputs = self.provider.apply_infrastructure(
            staging_dir=_get_staging_dir(deployment_id), bucket_name=bucket,
            state_prefix=state_prefix, auto_approve=True, tfvars=tfvars,
        )
        self.provider.set_bucket_labels(project, bucket, labels)        # only once the deploy has succeeded
        return {**plan, "image": image, "service_url": outputs.get("service_url"),
                "upgraded_from": last if standing == "upgrade" else None}

    def status_definition(self, deployment: Optional[str] = None, *, cwd: Optional[Path] = None) -> dict:
        """What is live for a deployment of the fleet: its URL and health, the
        domain's state, which gapp last deployed it, and the next step."""
        from gapp.admin.sdk import definitions, entryfile, settings as settings_mod

        @definitions._guard
        def find():
            _, deployments = definitions._load()
            name = definitions.which(deployments, deployment, cwd, "gapp status")
            definition = deployments[name]
            if entryfile.standing(definition, name) == "newer":
                entryfile.require_current(definition, name, "read its status")
            return name, definition

        name, definition = find()
        deployment_id, project = definition["id"], definition["project"]
        bucket = self.get_bucket_name(deployment_id, project)
        result = {"deployment": name, "project": project, "deployed": False, "url": None, "healthy": None,
                  "domain": None, "gapp": definition.get("gapp"), "last_deployed_by": None,
                  "upgrade_pending": False, "next_step": None}
        try:
            has_bucket = self.provider.bucket_exists(project, bucket)
            label = (self.provider.list_bucket_labels(project).get(bucket) or {}).get(VERSION_LABEL) if has_bucket else None
        except CloudAccessError:
            result["next_step"] = self._identity_hint(project)
            return result
        last = settings_mod.label_version(label)
        result["last_deployed_by"] = last
        outputs = self.provider.get_infrastructure_outputs(
            _get_staging_dir(deployment_id), bucket, "terraform/state") if has_bucket else None
        if outputs and (url := outputs.get("service_url")):
            result.update(deployed=True, url=url, healthy=self.provider.check_http_health(url))
        if outputs and (live := (outputs.get("custom_domain") or "").strip()):
            result["domain"] = self._domain_status(project, live).model_dump()
        if last and definition.get("gapp"):
            result["upgrade_pending"] = entryfile.version_key(last) < entryfile.version_key(definition["gapp"])
        if not result["deployed"]:
            result["next_step"] = f"It is not deployed yet. From a clone of its repo: gapp deploy --deployment {name}"
        elif result["upgrade_pending"]:
            result["next_step"] = (f"It was last deployed by gapp {last}, and its definition is on gapp "
                                   f"{definition['gapp']}. Apply that: gapp deploy --deployment {name} --upgrade")
        return result

    def list_definitions(self) -> dict:
        """Every definition in the fleet, with how its version stands against this gapp."""
        from gapp.admin.sdk import definitions, entryfile, fleetstore
        where = fleetstore.location()
        try:
            deployments = fleetstore.read(where)
        except fleetstore.FleetUnavailable as e:
            raise definitions.Refused(str(e)) from e
        rows = []
        for name in sorted(deployments):
            d = deployments[name]
            try:
                stands = entryfile.standing(d, name)
            except entryfile.EntryFileRefusal:
                stands = "unknown"
            rows.append({"deployment": name, "repo": d.get("repo"), "project": d.get("project"),
                         "ref": d.get("ref"), "gapp": d.get("gapp"),
                         "upgrade": {"older": "available", "newer": "install a newer gapp"}.get(stands)})
        return {"fleet": where.describe(), "deployments": rows}


def _domain_is_verified(domain: str, verified: List[str]) -> bool:
    """True when `domain` or one of its parents is in the verified list."""
    d = domain.strip().rstrip(".").lower()
    for v in verified:
        v = (v or "").strip().rstrip(".").lower()
        if v and (d == v or d.endswith("." + v)):
            return True
    return False


def _domain_status_from_conditions(conditions: list) -> tuple[str, Optional[str]]:
    """Map a Cloud Run domain mapping's conditions to gapp's DomainStatus.

    Cloud Run reports three conditions: `DomainRoutable`, `CertificateProvisioned`
    and the summary `Ready`. Returns (status, detail) where status is one of
    pending_dns, pending_cert, active, error.
    """
    by_type = {c.get("type"): c for c in conditions if isinstance(c, dict)}

    def _status(kind):
        return (by_type.get(kind) or {}).get("status")

    def _detail(kind):
        c = by_type.get(kind) or {}
        return c.get("message") or c.get("reason") or None

    if not by_type:
        return "pending_dns", "The mapping reports no conditions yet; check again shortly."
    if _status("Ready") == "True":
        return "active", None
    if "DomainRoutable" in by_type and _status("DomainRoutable") != "True":
        return "pending_dns", _detail("DomainRoutable")
    cert = by_type.get("CertificateProvisioned")
    if cert and cert.get("status") != "True":
        detail = _detail("CertificateProvisioned") or ""
        # Cloud Run words a missing CNAME as a certificate condition:
        # "You must configure your DNS records for certificate issuance to begin."
        if "dns" in detail.lower() or "record" in detail.lower():
            return "pending_dns", detail or None
        return "pending_cert", detail or None
    return "error", _detail("Ready") or "The mapping is not ready."


def _prepare_build_dir(path, image, start, ref="HEAD"):
    """The build context: the repo at `ref`, plus gapp's Dockerfile template
    when the app starts from a Procfile. The Procfile's web: command becomes
    the image's CMD, run by the shell so `$PORT` expands when the container
    starts. An app with its own Dockerfile is built from that file as is.
    """
    d = tempfile.mkdtemp(prefix="gapp-build-")
    subprocess.run(
        ["tar", "xf", "-", "-C", d],
        stdin=subprocess.Popen(["git", "archive", "--format=tar", ref], stdout=subprocess.PIPE, cwd=path).stdout,
        check=True,
    )
    t = Path(__file__).resolve().parent.parent.parent / "templates"
    shutil.copy2(t / "cloudbuild.yaml", Path(d) / "cloudbuild.yaml")
    if start["method"] in ("procfile", "start"):
        dockerfile = (t / "Dockerfile").read_text().rstrip("\n")
        (Path(d) / "Dockerfile").write_text(f"{dockerfile}\n\nCMD exec {start['web']}\n")
    return d


def _build_tfvars(name, pid, img, cfg, repo_path, public, domain,
                  bucket_name, solution_name=None, labels=None, manifest=None):
    # Every key returned here corresponds to a structurally-required tfvar in
    # modules/cloud-run-service/variables.tf. None of them have defaults, so
    # dropping a key fails terraform plan instead of silently mis-deploying.
    # See issue #35: an earlier refactor lost `data_bucket` here, and the
    # module's old `default = ""` + conditional opt-out turned that into a
    # silent volume strip. Don't reintroduce optional-empty tfvars for things
    # the module structurally needs.
    from gapp.admin.sdk.manifest import resolve_env_vars, get_env_vars
    env = {}
    env_vars = get_env_vars(manifest)
    sol = solution_name or name
    secrets_map = {}
    if env_vars:
        for e in resolve_env_vars(env_vars, {"SOLUTION_DATA_PATH": "/mnt/data", "SOLUTION_NAME": name}):
            if "value" in e:
                env[e["name"]] = e["value"]
            elif isinstance(e.get("secret"), dict):
                secrets_map[e["name"]] = f"{sol}-{e['secret']['name']}"
    custom_domain = domain if domain and domain.strip() else ""
    return {
        "project_id": pid, "service_name": name, "image": img,
        "memory": cfg["memory"], "cpu": cfg["cpu"], "max_instances": cfg["max_instances"],
        "env": env,
        "secrets": secrets_map,
        "public": bool(public), "custom_domain": custom_domain,
        "data_bucket": bucket_name,
        "labels": dict(labels or {}),
    }


def _get_staging_dir(name):
    return Path(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))) / "gapp" / name / "terraform"
