"""A deployment's settings: the one shape used under `settings:` in a
definition and as the file `gapp apply` takes.

    public: true
    domain: app.example.com
    start: my-app serve --port 8080
    memory: 1Gi
    cpu: "1"
    max_instances: 2
    apis: [drive.googleapis.com]
    env:
      LOG_LEVEL: {value: info}
      APP_KEY: {secret: app-key, generate: true}
      APP_DATA: {value: "{{DATA_PATH}}/users"}

Everything here is checked before anything is written, and a refusal
names the key and what it takes.
"""

from __future__ import annotations

import re

DATA_PATH = "{{DATA_PATH}}"

DEFAULTS = {"public": False, "memory": "512Mi", "cpu": "1", "max_instances": 1}
SCALARS = ("public", "domain", "start", "memory", "cpu", "max_instances")
KEYS = SCALARS + ("apis", "env")

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
_MEMORY = re.compile(r"^\d+(Mi|Gi)$")
_CPU = re.compile(r"^\d+(\.\d+)?$|^\d+m$")
_API = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*" + re.escape(".googleapis.com") + "$")


class SettingsRefusal(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def parse_scalar(key: str, text: str):
    """A value typed on a command line, as the type its key takes."""
    if key not in SCALARS:
        raise SettingsRefusal(_unknown(key))
    text = (text or "").strip()
    if key == "public":
        if text.lower() not in ("true", "false"):
            raise SettingsRefusal("'public' is true or false.")
        return text.lower() == "true"
    if key == "max_instances":
        if not text.isdigit() or int(text) < 1:
            raise SettingsRefusal("'max_instances' is a whole number, 1 or more.")
        return int(text)
    return _scalar(key, text)


def _scalar(key: str, value):
    if key == "public":
        if not isinstance(value, bool):
            raise SettingsRefusal("'public' is true or false.")
        return value
    if key == "max_instances":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SettingsRefusal("'max_instances' is a whole number, 1 or more.")
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and key == "cpu":
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise SettingsRefusal(f"'{key}' needs a value.")
    value = value.strip()
    if key == "domain":
        value = value.lower()
        if not _DOMAIN.match(value):
            raise SettingsRefusal(f"'{value}' is not a domain name (such as app.example.com, with no https://).")
    elif key == "memory" and not _MEMORY.match(value):
        raise SettingsRefusal(f"'{value}' is not a memory size (such as 512Mi or 1Gi).")
    elif key == "cpu" and not _CPU.match(value):
        raise SettingsRefusal(f"'{value}' is not a CPU count (such as 1, 2 or 500m).")
    return value


def api(name: str) -> str:
    name = (name or "").strip().lower()
    if not _API.match(name):
        raise SettingsRefusal(f"'{name}' is not a Google Cloud API name (such as drive.googleapis.com).")
    return name


def env_name(name: str) -> str:
    name = (name or "").strip()
    if not _ENV_NAME.match(name):
        raise SettingsRefusal(f"'{name}' is not a variable name: letters, digits and underscores, not starting with a digit.")
    return name


def default_secret_name(variable: str) -> str:
    """`APP_KEY` reads the secret `app-key` unless another is named."""
    return variable.lower().replace("_", "-").strip("-")


def env_entry(variable: str, *, value: str | None = None, secret: str | None = None,
              generate: bool = False, data_path: str | None = None) -> dict:
    """One variable's entry. Exactly one kind: a plain `value`; `secret`
    (a name, or "" for the default one); or `data_path` (a subfolder of the
    deployment's data folder, or "" for the folder itself)."""
    kinds = sum(x is not None for x in (value, secret, data_path))
    if kinds == 0:
        raise SettingsRefusal("Give a value, --secret [NAME], or --data-path [SUBFOLDER].")
    if kinds > 1:
        raise SettingsRefusal("Pick one: a value, --secret, or --data-path.")
    if generate and secret is None:
        raise SettingsRefusal("--generate goes with --secret.")
    if secret is not None:
        name = secret.strip() or default_secret_name(variable)
        if not _SECRET_NAME.match(name):
            raise SettingsRefusal(f"'{name}' is not a secret name: lowercase letters, digits and hyphens.")
        return {"secret": name, **({"generate": True} if generate else {})}
    if data_path is not None:
        sub = data_path.strip().strip("/")
        return {"value": f"{DATA_PATH}/{sub}" if sub else DATA_PATH}
    return {"value": value}


def _env(env) -> dict:
    if not isinstance(env, dict):
        raise SettingsRefusal("'env' is a mapping of variable names to {value: ...} or {secret: ...}.")
    checked = {}
    for name, entry in env.items():
        name = env_name(str(name))
        if not isinstance(entry, dict) or not entry:
            raise SettingsRefusal(f"env.{name} is {{value: ...}} or {{secret: ..., generate: true}}.")
        extra = sorted(set(entry) - {"value", "secret", "generate"})
        if extra:
            raise SettingsRefusal(f"env.{name} has '{extra[0]}', which is not a key a variable takes (value, secret, generate).")
        if ("value" in entry) == ("secret" in entry):
            raise SettingsRefusal(f"env.{name} takes exactly one of 'value' and 'secret'.")
        if "value" in entry:
            if "generate" in entry:
                raise SettingsRefusal(f"env.{name}: 'generate' goes with 'secret'.")
            if isinstance(entry["value"], (dict, list)) or entry["value"] is None:
                raise SettingsRefusal(f"env.{name}: 'value' is text.")
            checked[name] = {"value": str(entry["value"]) if not isinstance(entry["value"], bool)
                             else str(entry["value"]).lower()}
        else:
            if not isinstance(entry["secret"], str) or not _SECRET_NAME.match(entry["secret"]):
                raise SettingsRefusal(f"env.{name}: '{entry['secret']}' is not a secret name: lowercase letters, digits and hyphens.")
            if not isinstance(entry.get("generate", False), bool):
                raise SettingsRefusal(f"env.{name}: 'generate' is true or false.")
            checked[name] = {"secret": entry["secret"], **({"generate": True} if entry.get("generate") else {})}
    return checked


def validate(settings) -> dict:
    """The settings, checked and in a fixed key order. Empty lists and maps are left out."""
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise SettingsRefusal("Settings are a mapping of keys such as public, domain and env.")
    unknown = sorted(str(k) for k in settings if k not in KEYS)
    if unknown:
        raise SettingsRefusal(_unknown(unknown[0]))
    checked = {}
    for key in SCALARS:
        if settings.get(key) is not None:
            checked[key] = _scalar(key, settings[key])
    if settings.get("apis"):
        if not isinstance(settings["apis"], list):
            raise SettingsRefusal("'apis' is a list of Google Cloud API names.")
        checked["apis"] = sorted({api(str(a)) for a in settings["apis"]})
    if settings.get("env"):
        checked["env"] = _env(settings["env"])
    return checked


def _unknown(key: str) -> str:
    return f"'{key}' is not a setting. The settings are: {', '.join(KEYS)}."


def describe(key: str, value) -> str:
    """A value as a person reads it."""
    if key == "apis":
        return ", ".join(value) if value else "none"
    if value is None:
        return "not set"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict) and ("value" in value or "secret" in value):
        if "secret" in value:
            return f"the secret '{value['secret']}'" + (", generated" if value.get("generate") else "")
        text = value["value"]
        if text == DATA_PATH:
            return "the data folder"
        if text.startswith(DATA_PATH + "/"):
            return f"the data folder, {text[len(DATA_PATH) + 1:]}"
        return text
    if isinstance(value, list):
        return ", ".join(value) if value else "none"
    return str(value)


def as_manifest(id: str, settings) -> dict:
    """The settings in the shape the deploy code reads: one list of
    variables, sizing under `service`, extra APIs under `prerequisites`."""
    checked = validate(settings)
    manifest = {"name": id}
    for key in ("public", "domain", "start"):
        if key in checked:
            manifest[key] = checked[key]
    sizing = {k: checked[k] for k in ("memory", "cpu", "max_instances") if k in checked}
    if sizing:
        manifest["service"] = sizing
    if checked.get("apis"):
        manifest["prerequisites"] = {"apis": list(checked["apis"])}
    env = []
    for variable, entry in (checked.get("env") or {}).items():
        if "secret" in entry:
            env.append({"name": variable, "secret": {"name": entry["secret"],
                                                     **({"generate": True} if entry.get("generate") else {})}})
        else:
            value = entry["value"].replace("{{DATA_PATH}}", "{{SOLUTION_DATA_PATH}}").replace("{{NAME}}", "{{SOLUTION_NAME}}")
            env.append({"name": variable, "value": value})
    if env:
        manifest["env"] = env
    return manifest


def version_label(version: str) -> str:
    """A gapp version as a label value: labels take no dots."""
    return version.replace(".", "-")


def label_version(label: str | None) -> str | None:
    """The gapp version a label value names, or None when it names none.
    A bare major number, which earlier 4.0 release candidates wrote, reads
    as the first release candidate of that major."""
    if not label:
        return None
    if label.isdigit():
        return f"{label}.0.0rc0"
    return label.replace("-", ".")
