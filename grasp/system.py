"""High-level system control for Grasp: windows, processes, files.

Native APIs beat pixel-pushing for these. Destructive operations (kill a
process, overwrite a file) raise SafetyError unless confirm=True, mirroring
Computer.run's gate so the @tool wrapper reports them uniformly.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from .computer import SafetyError


# --- windows (pygetwindow) ----------------------------------------------------
def window_titles():
    import pygetwindow as gw
    return [t for t in gw.getAllTitles() if t and t.strip()]


def window_action(title: str, action: str, x=None, y=None, w=None, h=None):
    import pygetwindow as gw
    wins = gw.getWindowsWithTitle(title)
    if not wins:
        return {"found": False, "hint": f"no window matching '{title}'"}
    win = wins[0]
    a = action.lower()
    try:
        if a == "activate":
            win.activate()
        elif a == "minimize":
            win.minimize()
        elif a == "maximize":
            win.maximize()
        elif a == "restore":
            win.restore()
        elif a == "close":
            win.close()
        elif a == "move":
            win.moveTo(int(x), int(y))
        elif a == "resize":
            win.resizeTo(int(w), int(h))
        else:
            return {"found": True, "ok": False, "error": f"unknown action '{action}'"}
    except Exception as e:  # noqa: BLE001
        return {"found": True, "ok": False, "error": str(e)}
    return {"found": True, "ok": True, "action": a, "title": win.title,
            "rect": [win.left, win.top, win.width, win.height]}


# --- processes (psutil) -------------------------------------------------------
def list_processes(name_filter: str | None = None, limit: int = 300):
    import psutil
    f = (name_filter or "").lower()
    out = []
    for p in psutil.process_iter(["pid", "name"]):
        n = p.info.get("name") or ""
        if not f or f in n.lower():
            out.append({"pid": p.info["pid"], "name": n})
            if len(out) >= limit:
                break
    return out


def process_running(name: str) -> bool:
    import psutil
    n = name.lower()
    return any(n in (p.info.get("name") or "").lower()
               for p in psutil.process_iter(["name"]))


def kill_process(pid=None, name=None, confirm=False):
    import psutil
    if not confirm:
        raise SafetyError(f"killing process {pid or name!r} is destructive")
    killed = []
    if pid is not None:
        psutil.Process(int(pid)).terminate()
        killed.append(int(pid))
    elif name:
        for p in psutil.process_iter(["pid", "name"]):
            if name.lower() in (p.info.get("name") or "").lower():
                try:
                    p.terminate()
                    killed.append(p.info["pid"])
                except Exception:
                    pass
    else:
        raise SafetyError("provide pid or name")
    return {"killed": killed}


# --- files (stdlib) -----------------------------------------------------------
def list_dir(path: str):
    p = Path(path).expanduser()
    if not p.exists():
        return {"exists": False}
    if p.is_file():
        return {"exists": True, "is_file": True, "size": p.stat().st_size}
    entries = []
    for c in sorted(p.iterdir()):
        try:
            entries.append({"name": c.name, "dir": c.is_dir(),
                            "size": c.stat().st_size if c.is_file() else None})
        except Exception:
            continue
    return {"exists": True, "path": str(p), "count": len(entries),
            "entries": entries[:500]}


def file_read(path: str, max_bytes: int = 20000):
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"exists": False}
    data = p.read_bytes()[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", "replace")
    return {"exists": True, "path": str(p), "bytes": p.stat().st_size,
            "truncated": p.stat().st_size > max_bytes, "text": text}


def file_write(path: str, content: str, confirm=False, append=False):
    p = Path(path).expanduser()
    if p.exists() and not confirm and not append:
        raise SafetyError(f"{p} exists; re-issue with confirm=True to overwrite")
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": str(p), "bytes": len(content), "appended": append}


def file_search(root: str, pattern: str, max_results: int = 200):
    rp = Path(root).expanduser()
    if not rp.exists():
        return {"exists": False}
    hits = []
    for dirpath, _dirs, files in os.walk(rp):
        for name in files:
            if fnmatch.fnmatch(name.lower(), pattern.lower()):
                hits.append(str(Path(dirpath) / name))
                if len(hits) >= max_results:
                    return {"root": str(rp), "pattern": pattern,
                            "matches": hits, "truncated": True}
    return {"root": str(rp), "pattern": pattern, "matches": hits, "truncated": False}
