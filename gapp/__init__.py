__version__ = "4.0.0rc18"

# The two version numbers every release sets (gapp/admin/sdk/versions.py
# applies them; CONTRIBUTING, "gapp versions", says when each moves).
# COMPATIBLE_FROM: this gapp works as-is with definitions, infrastructure
# and workflows from this version on.
# UPGRADES_FROM: `gapp upgrade` and a deploy with --upgrade can bring
# anything from this version on up to this gapp. Never above COMPATIBLE_FROM.
COMPATIBLE_FROM = "4.0.0rc15"
UPGRADES_FROM = "4.0.0rc13"
