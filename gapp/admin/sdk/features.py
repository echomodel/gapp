"""Package-bundled feature flags.

Flags live in ``gapp/features.yaml`` and are loaded via
``importlib.resources`` so the file travels with the installed
package regardless of install method (pipx, editable, wheel).

Flags are release-gated: they are not read from environment
variables, user config, or CLI args. Toggling a flag means
editing the bundled yaml and cutting a new release.
"""

from functools import lru_cache
from importlib import resources

import yaml


@lru_cache(maxsize=1)
def _load() -> dict:
    with resources.files("gapp").joinpath("features.yaml").open("r") as f:
        return yaml.safe_load(f) or {}


def is_enabled(flag: str) -> bool:
    """Return the boolean state of a named feature flag.

    Unknown flags default to False so a missing entry fails safe
    (new behavior stays off until the flag is explicitly added).
    """
    return bool(_load().get(flag, False))
