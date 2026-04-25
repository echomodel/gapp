__version__ = "3.0.0"

# Oldest contract major this build can manage. Setup/deploy on projects
# stamped below this floor are refused; list/status still report them.
# 3.0.0 is a hard cutover — no v-2 carry-forward. Migrate v-2 labels
# manually with `gcloud projects update --update-labels=`.
MIN_SUPPORTED_MAJOR = 3
