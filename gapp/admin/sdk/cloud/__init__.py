"""Cloud provider abstraction layer for gapp."""

from gapp.admin.sdk.cloud.base import CloudProvider


def get_provider() -> CloudProvider:
    """Return the appropriate CloudProvider based on environment.
    
    If GAPP_MOCK_PROVIDER is set, returns a DummyCloudProvider.
    Otherwise returns the real GCPProvider.
    """
    import os
    if os.environ.get("GAPP_MOCK_PROVIDER"):
        from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
        return DummyCloudProvider()
    
    from gapp.admin.sdk.cloud.gcp import GCPProvider
    return GCPProvider()
