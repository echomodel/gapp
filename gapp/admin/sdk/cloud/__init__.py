"""Cloud provider abstraction layer for gapp."""

from gapp.admin.sdk.cloud.base import CloudProvider

_provider_cache = None

def get_provider() -> CloudProvider:
    """Return the appropriate CloudProvider based on environment (singleton)."""
    global _provider_cache
    if _provider_cache:
        return _provider_cache

    import os
    if os.environ.get("GAPP_MOCK_PROVIDER"):
        from gapp.admin.sdk.cloud.dummy import DummyCloudProvider
        _provider_cache = DummyCloudProvider()
    else:
        from gapp.admin.sdk.cloud.gcp import GCPProvider
        _provider_cache = GCPProvider()
        
    return _provider_cache

def reset_provider() -> None:
    """Reset the provider cache (used for tests)."""
    global _provider_cache
    _provider_cache = None
