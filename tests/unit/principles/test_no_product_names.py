import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Spelled in pieces so this file does not hold them either.
NAMES = ("SIGNING" + "_KEY", "signing" + "-key")


def test_one_products_secret_name_appears_only_where_the_rule_is_explained():
    """Commitment: gapp is not tied to any app or framework. The secret
    name one framework uses appears nowhere in this repo except the
    section of CONTRIBUTING.md that explains the rule.

    Why: an example that names one product's variable teaches every
    reader that gapp is for that product (CONTRIBUTING.md, "Avoiding
    Product Coupling").
    """
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    found = []
    for name in tracked:
        path = ROOT / name
        if name == "CONTRIBUTING.md" or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        found += [f"{name}: {word}" for word in NAMES if word in text]
    assert not found, found
