"""How an app starts: a Dockerfile, a Procfile with one `web:` line, or
gapp.yaml's `start`, in that order.

gapp reads these and never writes them. It knows no frameworks and detects
nothing. A repo you own should start from a Dockerfile or Procfile, which
other builders read too. `start` is the fallback for a repo you can't
change: a fleet deployment sets it in its fleet-side manifest.

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
    "or add a Dockerfile. For a repo you can't change, set the command in gapp.yaml instead:\n"
    "  gapp manifest set start 'my-app serve'"
)


def _procfile_web(text: str) -> tuple[str | None, str | None]:
    """The Procfile's single `web:` command, or why there is none.

    Blank lines and `#` comments are skipped and other process types are
    ignored. No `web:` line, an empty one, or more than one is no start
    method: there must be exactly one command to run.
    """
    webs = []
    for raw in text.splitlines():
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


def _manifest_start(root: Path) -> str | None:
    """gapp.yaml's `start` command, if the file sets one. A file gapp can't
    read gives none here; the commands that read it report the problem."""
    from gapp.admin.sdk.manifest import load_manifest
    from gapp.admin.sdk.schema import ManifestUnreadable
    try:
        value = load_manifest(root, strict=False).get("start")
    except ManifestUnreadable:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _ignored(*names: str | None) -> str | None:
    names = [n for n in names if n]
    return " and ".join(names) or None


def start_method(root: Path | str) -> dict:
    """How the app in `root` starts.

    Returns {"method": "dockerfile" | "procfile" | "start" | None, "web":
    <command> or None, "ignored": what is set but ignored ("Procfile",
    "gapp.yaml's start", or both) or None, "reason": why there is none, or
    None}. A Dockerfile wins over a Procfile, and a Procfile over gapp.yaml's
    `start`. A Procfile that is there but has no single web: line is no
    start method (its reason says why); `start` does not stand in for it.
    """
    root = Path(root)
    procfile = root / "Procfile"
    return start_method_of(dockerfile=(root / "Dockerfile").is_file(),
                           procfile=procfile.read_text() if procfile.is_file() else None,
                           start=_manifest_start(root))


def start_method_of(*, dockerfile: bool, procfile: str | None, start) -> dict:
    """`start_method` from what it reads: whether there is a Dockerfile, the
    Procfile's text (None when there is none) and gapp.yaml's `start`. Used
    where the files are not on disk (a read of a repo through GitHub)."""
    start = start.strip() if isinstance(start, str) and start.strip() else None
    start_label = "gapp.yaml's start" if start else None
    if dockerfile:
        return {"method": "dockerfile", "web": None,
                "ignored": _ignored("Procfile" if procfile is not None else None, start_label), "reason": None}
    if procfile is not None:
        web, reason = _procfile_web(procfile)
        if web:
            return {"method": "procfile", "web": web, "ignored": start_label, "reason": None}
        return {"method": None, "web": None, "ignored": start_label, "reason": reason}
    if start:
        return {"method": "start", "web": start, "ignored": None, "reason": None}
    return {"method": None, "web": None, "ignored": None, "reason": None}


def describe(start: dict) -> str:
    """The start method in words, for "Starts with: ..."."""
    ignored = start.get("ignored") or ""
    if ignored:
        ignored = ignored.replace("Procfile", "the Procfile", 1) if ignored.startswith("Procfile") else ignored
        ignored = f" ({ignored} {'are' if ' and ' in ignored else 'is'} ignored)"
    if start["method"] == "dockerfile":
        return "the Dockerfile" + ignored
    if start["method"] == "procfile":
        return f"web: {start['web']} (Procfile)" + ignored
    if start["method"] == "start":
        return f"{start['web']} (gapp.yaml's start)"
    return "nothing yet"


def missing(name: str, start: dict, *, service: str | None = None, consequence: str = "") -> str:
    """The one message for an app with no start method.

    `consequence` is what this means for the command that found it, e.g.
    ", so nothing was built or deployed".
    """
    who = f"{name} service '{service}'" if service else name
    why = f": {start['reason']}" if start.get("reason") else ""
    return f"{who} has no start method{why}{consequence}.\n{START_METHOD_HELP}"
