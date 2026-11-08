"""Cloud provider abstraction layer for gapp."""

from gapp.admin.sdk.cloud.base import CloudProvider


def get_provider() -> CloudProvider:
    """Return the production GCPProvider, bound to the configured account.

    `gapp config account <email>` is honoured here: every gcloud call the
    provider makes runs as that account (CLOUDSDK_CORE_ACCOUNT), never as
    whatever account happens to be active in gcloud's own config. Tests
    inject their own provider instead of calling this.
    """
    from gapp.admin.sdk.cloud.gcp import GCPProvider
    from gapp.admin.sdk.config import get_active_config
    return GCPProvider(account=get_active_config().get("account"))
