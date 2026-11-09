"""Mock implementation of CloudProvider for testing."""

from pathlib import Path
from typing import Optional, List, Dict

from gapp.admin.sdk.cloud.base import CloudAccessError, CloudProvider


class DummyCloudProvider(CloudProvider):
    """In-memory provider for unit tests."""

    def __init__(self):
        self.clear()

    def clear(self):
        """Reset internal state."""
        self.apis_enabled = set()
        self.buckets = {} # name -> metadata
        self.project_labels = {} # project_id -> labels_dict
        self.builds = {} # build_id -> metadata
        self.tf_outputs = {} # (bucket, prefix) -> outputs_dict
        self.iam_bindings = [] # list of dicts
        self.last_tfvars = None
        self.verified_domains = set() # domains the identity has verified
        self.domain_mappings = {} # (project_id, domain) -> mapping resource dict
        self.unreadable_state = set()  # (bucket, prefix) whose Terraform state cannot be read
        self.services = {}  # (project_id, service_name) -> labels
        self.label_writes = []  # ("project" | "bucket", target, labels) in call order

    def host(self, project_id: str, deployment: str, version: str = "4", service: bool = True,
             hosted: bool = True) -> str:
        """Test setup: a deployment as gapp leaves it, on a project marked
        gapp=hosted (unless hosted=False). Returns the bucket name."""
        labels = self.project_labels.setdefault(project_id, {})
        if hosted:
            labels["gapp"] = "hosted"
        dep_labels = {"gapp-deployment": deployment, "gapp-version": version}
        bucket = f"gapp-{deployment}-{project_id}".lower()
        self.buckets[bucket] = {"project": project_id, "labels": dict(dep_labels)}
        if service:
            self.services[(project_id, deployment)] = dict(dep_labels)
        return bucket

    # -- GCP Foundation --

    def enable_api(self, project_id: str, api: str) -> None:
        self.apis_enabled.add((project_id, api))

    def bucket_exists(self, project_id: str, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def create_bucket(self, project_id: str, bucket_name: str) -> None:
        self.buckets[bucket_name] = {"project": project_id, "labels": {}}

    def ensure_build_permissions(self, project_id: str) -> None:
        self.iam_bindings.append({"project": project_id, "roles": ["viewer", "writer"]})

    def get_project_labels(self, project_id: str) -> Dict[str, str]:
        return self.project_labels.get(project_id, {})

    def set_project_labels(self, project_id: str, labels: Dict[str, str]) -> None:
        self.label_writes.append(("project", project_id, dict(labels)))
        self.project_labels[project_id] = labels

    def list_projects(self, filter_query: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """Mimic the subset of gcloud filter syntax gapp uses.

        Supported forms:
          labels.<key>=<value>     — exact key/value match
          labels:<key>             — key exists (any value)
          labels:<prefix>*         — at least one key starts with prefix
        """
        def matches(labels: Dict[str, str]) -> bool:
            if not filter_query:
                return True
            q = filter_query.strip()
            # labels.<key>=<value>
            if q.startswith("labels.") and "=" in q:
                key, _, val = q[len("labels."):].partition("=")
                val = val.strip("'\"")
                return labels.get(key) == val
            # labels:<rest>
            if q.startswith("labels:"):
                rest = q[len("labels:"):]
                if rest.endswith("*"):
                    prefix = rest[:-1]
                    return any(k.startswith(prefix) for k in labels.keys())
                # exact key existence
                return rest in labels
            return True

        results = []
        for pid, labels in self.project_labels.items():
            if not matches(labels):
                continue
            results.append({"projectId": pid, "labels": labels})
            if limit and len(results) >= limit:
                break
        return results

    # -- Deployment labels on buckets and services --

    def list_bucket_labels(self, project_id: str) -> Dict[str, Dict[str, str]]:
        return {name: dict(meta.get("labels") or {}) for name, meta in self.buckets.items()
                if meta.get("project") == project_id}

    def set_bucket_labels(self, project_id: str, bucket_name: str, labels: Dict[str, str]) -> None:
        self.label_writes.append(("bucket", bucket_name, dict(labels)))
        meta = self.buckets.setdefault(bucket_name, {"project": project_id, "labels": {}})
        meta.setdefault("labels", {}).update(labels)

    def get_service_labels(self, project_id: str, region: str, service_name: str) -> Optional[Dict[str, str]]:
        labels = self.services.get((project_id, service_name))
        return dict(labels) if labels is not None else None

    # -- Custom domains --

    def list_verified_domains(self) -> List[str]:
        return sorted(self.verified_domains)

    def get_domain_mapping(self, project_id: str, region: str, domain: str) -> Optional[Dict]:
        return self.domain_mappings.get((project_id, domain))

    # -- Cloud Build & Artifact Registry --

    def ensure_artifact_registry(self, project_id: str, region: str) -> None:
        pass

    def image_exists(self, project_id: str, region: str, solution_name: str, tag: str) -> bool:
        return False

    def submit_build_sync(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> None:
        pass

    def submit_build_async(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> str:
        build_id = f"build-{len(self.builds)}"
        self.builds[build_id] = {"status": "queued", "image": image}
        return build_id

    def check_build(self, project_id: str, build_id: str) -> Dict:
        return self.builds.get(build_id, {"status": "SUCCESS", "results": {"images": [{"name": "mock-image"}]}})

    # -- Terraform --

    def apply_infrastructure(self, staging_dir: Path, bucket_name: str, state_prefix: str, auto_approve: bool, tfvars: Dict) -> Dict:
        self.last_tfvars = tfvars
        self.services[(tfvars["project_id"], tfvars["service_name"])] = dict(tfvars.get("labels") or {})
        outputs = {
            "service_url": f"https://{tfvars['service_name']}.a.run.app",
            "custom_domain": tfvars.get("custom_domain", ""),
        }
        self.tf_outputs[(bucket_name, state_prefix)] = outputs
        return outputs

    def get_infrastructure_outputs(self, staging_dir: Path, bucket_name: str, state_prefix: str,
                                   strict: bool = False) -> Optional[Dict]:
        if (bucket_name, state_prefix) in self.unreadable_state:
            if strict:
                raise CloudAccessError(f"terraform init against gs://{bucket_name}/{state_prefix} failed: denied")
            return None
        return self.tf_outputs.get((bucket_name, state_prefix))

    # -- Miscellaneous --

    def get_auth_token(self) -> str:
        return "mock-token"

    def check_http_health(self, url: str) -> bool:
        return True
