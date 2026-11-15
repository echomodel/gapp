"""Which gapp versions this gapp works with, and the only code that compares them.

A deployment carries two versions: its definition's (`gapp` in the fleet,
the gapp that deploys it) and its infrastructure's (the gapp of its last
successful deploy, in the deploy record). This gapp works with either as
it is when it is at least COMPATIBLE_FROM and no newer than this gapp.
Below that, `gapp upgrade` (for the definition) and a deploy with
`--upgrade` (for the infrastructure) bring it up, from UPGRADES_FROM on.
Both numbers are in `gapp/__init__.py`.

A command checks the version of what it touches. Commands that touch only
the definition get it through `loading.load_definition`; commands that
touch the infrastructure get both through `loading.load_deployment`.
"""

from __future__ import annotations

from gapp import COMPATIBLE_FROM, UPGRADES_FROM, __version__
from gapp.admin.sdk.entryfile import EntryFileRefusal, version_key

# Settings added after COMPATIBLE_FROM, with the version that added each.
# A definition on an older (compatible) gapp cannot use one until it is
# upgraded: the gapp a runner installs for it would not know it.
SETTINGS_SINCE: dict[str, str] = {}


# What `gapp list` and `gapp show` say about a definition's version.
UPGRADE_WORDS = {"compatible": "available", "upgrade-required": "required",
                 "too-old": "too old for this gapp", "newer": "install a newer gapp"}


def _key(version: str) -> tuple:
    return version_key(version)


def _definition_version(definition: dict, name: str) -> str:
    recorded = definition.get("gapp")
    if recorded is None:
        raise EntryFileRefusal(
            "no-version",
            f"Deployment '{name}' does not record the gapp version that wrote it, so this gapp "
            f"cannot tell how to treat it. Nothing was changed.")
    return recorded


def standing(version: str) -> str:
    """How a version stands against this gapp: "current", "compatible"
    (older, works as-is), "upgrade-required" (older than COMPATIBLE_FROM,
    within reach of `gapp upgrade`), "too-old" or "newer"."""
    v, mine = _key(version), _key(__version__)
    if v == mine:
        return "current"
    if v > mine:
        return "newer"
    if v >= _key(COMPATIBLE_FROM):
        return "compatible"
    return "upgrade-required" if v >= _key(UPGRADES_FROM) else "too-old"


def _too_old(name: str, what: str, version: str, to_do: str) -> EntryFileRefusal:
    if _key(version) >= _key(UPGRADES_FROM):
        return EntryFileRefusal("upgrade-required", (
            f"{what} gapp {version}, and gapp {__version__} works as-is only with {COMPATIBLE_FROM} or later. "
            f"Upgrade it first, then {to_do}: gapp upgrade --deployment {name}, then deploy it with --upgrade."))
    return EntryFileRefusal("too-old", (
        f"{what} gapp {version}, which gapp {__version__} cannot upgrade from (the oldest it can is "
        f"{UPGRADES_FROM}). Install gapp {UPGRADES_FROM}, upgrade and deploy '{name}' with it, then come back."))


def check_definition(definition: dict, name: str, to_do: str) -> None:
    """Refuse `to_do` unless this gapp works with the definition as it is."""
    version = _definition_version(definition, name)
    stands = standing(version)
    if stands == "newer":
        raise EntryFileRefusal("newer-definition", (
            f"Deployment '{name}' was written by gapp {version}; this is gapp {__version__}. "
            f"Install gapp {version} or later to {to_do}. Nothing was changed."))
    if stands in ("upgrade-required", "too-old"):
        raise _too_old(name, f"Deployment '{name}' is on", version, to_do)


def check_readable(definition: dict, name: str, to_do: str) -> None:
    """Refuse only a definition written by a newer gapp: one that may hold
    what this gapp cannot read."""
    version = _definition_version(definition, name)
    if standing(version) == "newer":
        check_definition(definition, name, to_do)


def check_setting(definition: dict, key: str, name: str) -> None:
    """Refuse a setting the definition's gapp version does not know."""
    since = SETTINGS_SINCE.get(key)
    version = _definition_version(definition, name)
    if since and _key(version) < _key(since):
        raise EntryFileRefusal("setting-too-new", (
            f"'{key}' is a setting from gapp {since}, and '{name}' is on gapp {version}, which a runner "
            f"would deploy it with. Upgrade it first: gapp upgrade --deployment {name}"))


def check_deploys(definition: dict, name: str) -> None:
    """A deploy builds with the definition's version and no other: refuse
    a deploy by this gapp of a definition on another version."""
    version = _definition_version(definition, name)
    stands = standing(version)
    if stands == "newer":
        check_definition(definition, name, "deploy it")
    elif stands != "current":
        raise EntryFileRefusal("other-version", (
            f"'{name}' is deployed with gapp {version}, its definition's version, and this is gapp {__version__}. "
            f"Deploy it on a runner, which installs {version} (gapp ci deploy --deployment {name}), or bring it "
            f"to this version first: gapp upgrade --deployment {name}, then deploy it with --upgrade."))


def check_infrastructure(last: str | None, name: str, to_do: str, *, upgrading: bool = False) -> None:
    """Refuse `to_do` unless this gapp works with the infrastructure the
    gapp `last` deployed (None: nothing deployed yet). `upgrading` is a
    deploy with --upgrade, which carries it up from UPGRADES_FROM."""
    if last is None:
        return
    stands = standing(last)
    if stands == "newer":
        raise EntryFileRefusal("newer-infrastructure", (
            f"'{name}' was last deployed by gapp {last}; this is gapp {__version__}. "
            f"Install gapp {last} or later to {to_do}. Nothing was changed."))
    if stands == "too-old" or (stands == "upgrade-required" and not upgrading):
        raise _too_old(name, f"'{name}' was last deployed by", last, to_do)


def upgrade_pending(definition: dict, last: str | None) -> bool:
    """The definition is on a newer gapp than its infrastructure: the next
    deploy applies that, and must be given --upgrade."""
    return last is not None and _key(definition["gapp"]) > _key(last)
