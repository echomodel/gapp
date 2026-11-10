"""Abstract base class for cloud operations."""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Dict


class CloudAccessError(RuntimeError):
    """The cloud refused or could not answer a read or write.

    Raised for an expired or missing login, a wrong account, a denied
    permission, or an API failure. Distinct from the plain RuntimeError
    the resolver raises for "this solution is not deployed here", so that
    callers never report an auth failure as a missing deployment.
    """


class ProjectNotFoundError(CloudAccessError):
    """The project does not exist, or the current identity cannot see it."""


def on_ci_runner() -> bool:
    """True on a GitHub Actions runner, where gapp acts as the deploy identity."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def ci_identity_hint(account: Optional[str] = None, project_id: Optional[str] = None) -> str:
    """What to do about a refused call on a CI runner: signing in does not apply there."""
    who = account or "the deploy identity"
    project = project_id or "<project>"
    return (
        f"This is a CI run as {who}, so signing in again does not apply: the error above names what "
        f"it lacks. `gapp ci project setup {project}` grants the deploy identity its roles; run it as "
        f"a project Owner, then deploy again."
    )


class CloudProvider(ABC):
    """Abstract interface for cloud and infrastructure operations.

    Decouples business logic from gcloud, terraform, and other external CLI tools.
    """

    # -- GCP Foundation --
    
    @abstractmethod
    def enable_api(self, project_id: str, api: str) -> None:
        """Enable a Google Cloud API service."""
        pass

    @abstractmethod
    def bucket_exists(self, project_id: str, bucket_name: str) -> bool:
        """Check if a GCS bucket exists."""
        pass

    @abstractmethod
    def create_bucket(self, project_id: str, bucket_name: str) -> None:
        """Create a new GCS bucket."""
        pass

    @abstractmethod
    def ensure_build_permissions(self, project_id: str) -> None:
        """Ensure the Cloud Build service account has necessary roles."""
        pass

    @abstractmethod
    def get_project_labels(self, project_id: str) -> Dict[str, str]:
        """Retrieve labels for a GCP project.

        Returns the labels, possibly empty, of a project the identity can
        read. Raises ProjectNotFoundError when the project does not exist
        or is invisible to this identity, and CloudAccessError for any
        other failure (expired login, wrong account, denied permission).
        Never returns an empty dict to paper over a failure.
        """
        pass

    @abstractmethod
    def set_project_labels(self, project_id: str, labels: Dict[str, str]) -> None:
        """Replace or update labels on a GCP project."""
        pass

    @abstractmethod
    def list_projects(self, filter_query: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """Find projects matching a filter.

        Returns a list of dicts with 'projectId' and 'labels' keys. An
        empty list means no project matched; a failed listing raises
        CloudAccessError instead.
        """
        pass

    # -- Deployment labels on buckets and services --

    @abstractmethod
    def list_bucket_labels(self, project_id: str) -> Dict[str, Dict[str, str]]:
        """Every bucket of the project, by name, with its labels (possibly empty).
        A failed listing raises CloudAccessError."""
        pass

    @abstractmethod
    def set_bucket_labels(self, project_id: str, bucket_name: str, labels: Dict[str, str]) -> None:
        """Add or update the given labels on a bucket, keeping its others."""
        pass

    @abstractmethod
    def get_service_labels(self, project_id: str, region: str, service_name: str) -> Optional[Dict[str, str]]:
        """A Cloud Run service's labels, or None when the service does not exist.
        Any other failure raises CloudAccessError."""
        pass

    # -- Custom domains --

    @abstractmethod
    def list_verified_domains(self) -> List[str]:
        """Domains whose ownership the current identity has verified.

        Cloud Run only maps a custom domain for an identity that verified
        the domain or one of its parents. Raises CloudAccessError when the
        list cannot be read.
        """
        pass

    @abstractmethod
    def get_domain_mapping(self, project_id: str, region: str, domain: str) -> Optional[Dict]:
        """Describe the Cloud Run domain mapping for `domain`.

        Returns the mapping resource as a dict (with `status.conditions`
        and `status.resourceRecords`), or None when no mapping exists.
        """
        pass

    # -- Cloud Build & Artifact Registry --

    @abstractmethod
    def ensure_artifact_registry(self, project_id: str, region: str) -> None:
        """Ensure a 'gapp' Docker repository exists in Artifact Registry."""
        pass

    @abstractmethod
    def image_exists(self, project_id: str, region: str, solution_name: str, tag: str) -> bool:
        """Check if a specific image:tag exists in Artifact Registry."""
        pass

    @abstractmethod
    def submit_build_sync(self, project_id: str, build_dir: Path, image: str, ref: str = "HEAD") -> None:
        """Run a Cloud Build and wait for completion (streaming output)."""
        pass

    @abstractmethod
    def submit_build_async(self, project_id: str, build_dir: Path, image: str, ref: str = "HEAD") -> str:
        """Submit a Cloud Build and return the build ID immediately."""
        pass

    @abstractmethod
    def check_build(self, project_id: str, build_id: str) -> Dict:
        """Retrieve status and metadata for a Cloud Build."""
        pass

    # -- Terraform --

    @abstractmethod
    def apply_infrastructure(self, staging_dir: Path, bucket_name: str, state_prefix: str, auto_approve: bool, tfvars: Dict) -> Dict:
        """Run terraform init and apply."""
        pass

    @abstractmethod
    def get_infrastructure_outputs(self, staging_dir: Path, bucket_name: str, state_prefix: str,
                                   strict: bool = False) -> Optional[Dict]:
        """Read output variables from existing Terraform state.

        An empty state reads as {}. A state that cannot be read returns None,
        or with strict raises CloudAccessError, for callers that must not take
        "unreadable" for "nothing deployed"."""
        pass

    # -- Miscellaneous --

    @abstractmethod
    def get_auth_token(self) -> str:
        """Return a fresh GCP access token."""
        pass
    
    @abstractmethod
    def check_http_health(self, url: str) -> bool:
        """Perform an HTTP health check (expecting 200 OK)."""
        pass
