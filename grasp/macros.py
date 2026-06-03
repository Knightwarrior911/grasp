"""Macros: save and replay action sequences, and record real input (pynput).

A macro is a list of steps `[{action, args}]` where action is a safe Computer
method (click, type_text, key, scroll, drag, open_app, ...) or "sleep". The agent
can compose a macro and replay it, or record one from real mouse/keyboard input.
Stored as JSON under %LOCALAPPDATA%/Grasp/macros (sibling to Roam's recipes).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Only these may be replayed, so a macro can never invoke run() (shell) etc.
ALLOWED = {
    "click", "double_click", "triple_click", "right_click", "middle_click",
    "move", "type_text", "key", "scroll", "drag", "drag_path", "open_app", "sleep",
}


def _dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "Grasp" / "macros"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(name: str) -> Path:
    safe = "".join(c for c in name if c.isalnum() or c in "-_ ").strip() or "macro"
    return _dir() / f"{safe}.json"


def save(name: str, steps: list) -> dict:
    p = _path(name)
    p.write_text(json.dumps({"name": name, "steps": steps}, indent=2), encoding="utf-8")
    return {"saved": name, "steps": len(steps), "path": str(p)}


def load(name: str):
    p = _path(name)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def list_macros() -> list:
    return sorted(f.stem for f in _dir().glob("*.json"))


def delete(name: str) -> bool:
    p = _path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def run(name: str, computer) -> dict:
    data = load(name)
    if data is None:
        return {"found": False, "hint": "no such macro; macro_list to see saved ones"}
    results = []
    for i, step in enumerate(data.get("steps", [])):
        action = step.get("action")
        args = step.get("args", {}) or {}
        if action == "sleep":
            time.sleep(min(float(args.get("seconds", 0.3)), 10.0))
            results.append({"step": i, "action": "sleep"})
            continue
        if action not in ALLOWED:
            results.append({"step": i, "action": action, "error": "action not allowed"})
            continue
        fn = getattr(computer, action, None)
        if not callable(fn):
            results.append({"step": i, "action": action, "error": "unknown action"})
            continue
        try:
            fn(**args)
            results.append({"step": i, "action": action, "ok": True})
        except Exception as e:  # noqa: BLE001
            results.append({"step": i, "action": action, "error": str(e)})
    return {"found": True, "ran": len(results), "results": results}


# --- live recording (pynput) --------------------------------------------------
_rec = {"active": False, "events": [], "name": None, "t0": 0.0, "listeners": [],
        "computer": None}

_SPECIAL = {"enter", "tab", "backspace", "delete", "esc", "escape", "space",
            "up", "down", "left", "right", "home", "end", "page_up", "page_down"}


def record_start(name: str, computer) -> dict:
    """Begin recording real mouse clicks + keystrokes into a named macro."""
    if _rec["active"]:
        return {"ok": False, "error": "already recording", "name": _rec["name"]}
    try:
        from pynput import mouse, keyboard
    except Exception:
        return {"ok": False, "error": "pynput not installed (pip install pynput)"}
    _rec.update(active=True, events=[], name=name, t0=time.monotonic(),
                listeners=[], computer=computer)

    def stamp():
        return round(time.monotonic() - _rec["t0"], 3)

    def on_click(x, y, button, pressed):
        if not pressed:
            return
        mx, my = computer.real_to_model(int(x), int(y))
        _rec["events"].append({"action": "click",
                               "args": {"x": int(mx), "y": int(my), "button": button.name},
                               "t": stamp()})

    def on_press(key):
        try:
            ch = key.char
        except AttributeError:
            ch = None
        if ch:
            _rec["events"].append({"action": "type_text", "args": {"text": ch}, "t": stamp()})
        else:
            name_ = str(key).replace("Key.", "")
            if name_ in _SPECIAL:
                _rec["events"].append({"action": "key", "args": {"keys": name_}, "t": stamp()})

    ml = mouse.Listener(on_click=on_click)
    kl = keyboard.Listener(on_press=on_press)
    ml.start()
    kl.start()
    _rec["listeners"] = [ml, kl]
    return {"ok": True, "recording": name}


def record_stop() -> dict:
    """Stop recording, fold inter-event gaps into sleeps, and save the macro."""
    if not _rec["active"]:
        return {"ok": False, "error": "not recording"}
    for listener in _rec["listeners"]:
        try:
            listener.stop()
        except Exception:
            pass
    steps, prev = [], 0.0
    for e in _rec["events"]:
        gap = e["t"] - prev
        if gap > 0.08:
            steps.append({"action": "sleep", "args": {"seconds": round(gap, 2)}})
        prev = e["t"]
        steps.append({"action": e["action"], "args": e["args"]})
    name = _rec["name"]
    _rec.update(active=False, listeners=[], computer=None)
    save(name, steps)
    return {"ok": True, "saved": name, "steps": len(steps)}
