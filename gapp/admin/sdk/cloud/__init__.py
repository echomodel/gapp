"""Cloud provider abstraction layer for gapp."""

from gapp.admin.sdk.cloud.base import CloudProvider


def get_provider() -> CloudProvider:
    """Return the production GCPProvider.
    
    In a provider model, we dont use env var switches here. 
    The caller (CLI or Tests) decides which provider to inject.
    """
    from gapp.admin.sdk.cloud.gcp import GCPProvider
    return GCPProvider()
