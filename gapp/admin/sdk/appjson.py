"""What an app's `app.json` says about the variables it reads.

`app.json` is Heroku's published format, and gapp reads its `env` block
and nothing else. It is read in one place: a deploy, from the commit the
deploy builds. Nothing from it is kept. The rule is one line: the
definition wins, and `app.json` fills in what the definition leaves out.

Per variable the definition does not set:

- a `value` is the default, used as it is;
- no `value`, and required (the default) or `"generator": "secret"`:
  the deploy is refused until the definition supplies it;
- no `value` and `"required": false`: left unset.

gapp does not carry out `generator`. It is a hint about what kind of
value the variable wants, so the refusal suggests a generated secret.

When `app.json` has an `env` block, a variable the definition sets that
the block does not list is reported, never refused: the app may not read
it. An app with no `app.json`, or one with no `env` block, has said
nothing, so nothing is checked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from gapp.admin.sdk import settings as settings_mod

FILE_NAME = "app.json"


class AppJsonRefusal(Exception):
    """The app's `app.json` cannot be used as it is. The message says why."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse(text: str) -> dict | None:
    """The `env` block as {NAME: {"value", "required", "generated", "description"}},
    or None when the file has no `env` block. Every other key is ignored."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise AppJsonRefusal(f"{FILE_NAME} is not valid JSON: {e.msg} at line {e.lineno}, column {e.colno}.") from e
    if not isinstance(doc, dict):
        raise AppJsonRefusal(f"{FILE_NAME} is not an object at its top level.")
    if "env" not in doc or doc["env"] is None:
        return None
    env = doc["env"]
    if not isinstance(env, dict):
        raise AppJsonRefusal(f"{FILE_NAME}: 'env' is not an object of variable names.")
    declared = {}
    for name, entry in env.items():
        try:
            name = settings_mod.env_name(str(name))
        except settings_mod.SettingsRefusal as e:
            raise AppJsonRefusal(f"{FILE_NAME}: {e.message}") from e
        if isinstance(entry, dict):
            value = entry.get("value")
            description = entry.get("description")
            required = entry.get("required", True) is not False
            generated = entry.get("generator") == "secret"
        elif isinstance(entry, (list,)) or entry is None:
            raise AppJsonRefusal(f"{FILE_NAME}: env.{name} is neither a value nor an object.")
        else:
            value, description, required, generated = entry, None, True, False
        if isinstance(value, (dict, list)):
            raise AppJsonRefusal(f"{FILE_NAME}: env.{name}: 'value' is text.")
        if isinstance(value, bool):
            value = str(value).lower()
        declared[name] = {
            "value": None if value is None else str(value),
            "required": required, "generated": generated,
            "description": description.strip() if isinstance(description, str) and description.strip() else None,
        }
    return declared


def read(root: Path, rev: str = "HEAD") -> dict | None:
    """What the commit a deploy builds declares, or None when it has no
    `app.json` or the file has no `env` block. Read from the commit, never
    from the working tree, because the commit is what gets built."""
    shown = subprocess.run(["git", "show", f"{rev}:{FILE_NAME}"], capture_output=True, text=True, cwd=root)
    if shown.returncode != 0:
        return None
    return parse(shown.stdout)


def fill(settings: dict | None, declared: dict | None) -> dict:
    """The settings a deploy uses: the definition's, with `app.json`'s
    defaults for the variables it leaves out.

    Returns `settings` (the filled ones), `defaults` (the variables taken
    from `app.json`), `missing` (those the definition must supply, each
    with its description and whether a generated secret is suggested)
    and `unlisted` (those the definition sets that `app.json` does not
    list).
    """
    own = settings_mod.validate(settings)
    if declared is None:
        return {"settings": own, "defaults": [], "missing": [], "unlisted": []}
    env = dict(own.get("env") or {})
    defaults, missing = [], []
    for name, entry in declared.items():
        if name in env:
            continue
        if entry["value"] is not None:
            env[name] = {"value": entry["value"]}
            defaults.append(name)
        elif entry["generated"] or entry["required"]:
            missing.append({"name": name, "description": entry["description"], "generated": entry["generated"]})
    unlisted = [name for name in (own.get("env") or {}) if name not in declared]
    filled = dict(own)
    if env:
        filled["env"] = env
    return {"settings": settings_mod.validate(filled), "defaults": defaults, "missing": missing, "unlisted": unlisted}


def missing_text(name: str, missing: list[dict]) -> str:
    """The refusal for variables `app.json` needs and the definition does not supply."""
    lines = [f"'{name}' cannot be deployed yet: its {FILE_NAME} needs "
             f"{'a variable' if len(missing) == 1 else f'{len(missing)} variables'} the definition does not set. "
             f"Nothing was built or changed."]
    for item in missing:
        about = f" ({item['description']})" if item["description"] else ""
        lines.append(f"  {item['name']}{about}")
        if item["generated"]:
            lines.append(f"    a generated secret:  gapp env set {item['name']} --secret --generate --deployment {name}")
        else:
            lines.append(f"    a plain value:       gapp env set {item['name']} <value> --deployment {name}")
            lines.append(f"    a secret:            gapp env set {item['name']} --secret --deployment {name}")
    return "\n".join(lines)


def unlisted_lines(name: str, unlisted: list[str]) -> list[str]:
    """One line per variable the definition sets that `app.json` does not list."""
    return [f"'{name}' sets {variable}, and this version's {FILE_NAME} does not list it. "
            f"The app may not read it." for variable in unlisted]
