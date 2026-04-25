"""Abstract base class for cloud operations."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, List, Dict


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
        """Retrieve labels for a GCP project."""
        pass

    @abstractmethod
    def set_project_labels(self, project_id: str, labels: Dict[str, str]) -> None:
        """Replace or update labels on a GCP project."""
        pass

    @abstractmethod
    def list_projects(self, filter_query: Optional[str] = None, limit: Optional[int] = None) -> List[Dict]:
        """Find projects matching a filter.
        
        Returns a list of dicts with 'projectId' and 'labels' keys.
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
    def submit_build_sync(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> None:
        """Run a Cloud Build and wait for completion (streaming output)."""
        pass

    @abstractmethod
    def submit_build_async(self, project_id: str, build_dir: Path, image: str, build_entrypoint: str, ref: str = "HEAD") -> str:
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
    def get_infrastructure_outputs(self, staging_dir: Path, bucket_name: str, state_prefix: str) -> Optional[Dict]:
        """Read output variables from existing Terraform state."""
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
