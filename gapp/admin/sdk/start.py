"""How an app starts: a Dockerfile, or a Procfile with one `web:` line.

gapp reads these files and never writes them. It knows no frameworks and
detects nothing: an app declares how it starts in a file it owns, in a
format other builders read too. gapp.yaml has no start settings.

One check (`start_method`) and one guidance text (`START_METHOD_HELP`)
are shared by init, status, setup and deploy, so every command says the
same thing when an app has no start method.
"""

from __future__ import annotations

from pathlib import Path

START_METHOD_HELP = (
    "Add a Procfile with a web: line that starts the app and serves HTTP on $PORT, for example:\n"
    "  echo 'web: my-app serve' > Procfile\n"
    "  echo 'web: uvicorn my_app.server:app --host 0.0.0.0 --port $PORT' > Procfile\n"
    "or add a Dockerfile."
)


def _procfile_web(path: Path) -> tuple[str | None, str | None]:
    """The Procfile's single `web:` command, or why there is none.

    Blank lines and `#` comments are skipped and other process types are
    ignored. No `web:` line, an empty one, or more than one is no start
    method: there must be exactly one command to run.
    """
    webs = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        kind, _, command = line.partition(":")
        if kind.strip() == "web":
            webs.append(command.strip())
    if len(webs) > 1:
        return None, "its Procfile has more than one web: line"
    if not webs or not webs[0]:
        return None, "its Procfile has no web: line"
    return webs[0], None


def start_method(root: Path | str) -> dict:
    """How the app in `root` starts.

    Returns {"method": "dockerfile" | "procfile" | None, "web": <command>
    or None, "ignored": "Procfile" or None, "reason": why there is none, or
    None}. A Dockerfile wins over a Procfile; the Procfile is then reported
    as ignored.
    """
    root = Path(root)
    procfile = root / "Procfile"
    if (root / "Dockerfile").is_file():
        return {"method": "dockerfile", "web": None,
                "ignored": "Procfile" if procfile.is_file() else None, "reason": None}
    if procfile.is_file():
        web, reason = _procfile_web(procfile)
        if web:
            return {"method": "procfile", "web": web, "ignored": None, "reason": None}
        return {"method": None, "web": None, "ignored": None, "reason": reason}
    return {"method": None, "web": None, "ignored": None, "reason": None}


def describe(start: dict) -> str:
    """The start method in words, for "Starts with: ..."."""
    if start["method"] == "dockerfile":
        return "the Dockerfile" + (" (the Procfile is ignored)" if start.get("ignored") else "")
    if start["method"] == "procfile":
        return f"web: {start['web']} (Procfile)"
    return "nothing yet"


def missing(name: str, start: dict, *, service: str | None = None, consequence: str = "") -> str:
    """The one message for an app with no start method.

    `consequence` is what this means for the command that found it, e.g.
    ", so nothing was built or deployed".
    """
    who = f"{name} service '{service}'" if service else name
    why = f": {start['reason']}" if start.get("reason") else ""
    return f"{who} has no start method{why}{consequence}.\n{START_METHOD_HELP}"
