"""Grasp MCP server. Exposes the Computer surface as MCP tools over stdio.

Every tool returns a small JSON dict. Screenshots return base64 PNG plus the model-space
dimensions, so the calling agent knows the coordinate space it must click in. The agent
loop is: screenshot -> reason in model space -> act -> screenshot to verify."""

import functools
import json

from mcp.server.fastmcp import FastMCP

from .computer import Computer, SafetyError

mcp = FastMCP("grasp")
_pc = None
_overlay = None


def pc():
    global _pc
    if _pc is None:
        _pc = Computer()
    return _pc


def tool(fn):
    """Wrap a tool so it returns a uniform {ok, ...} / {error} envelope as JSON text."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            res = fn(*a, **k)
            return json.dumps({"ok": True, **(res if isinstance(res, dict) else {"result": res})})
        except SafetyError as e:
            return json.dumps({"ok": False, "safety": str(e),
                               "hint": "re-issue with confirm=True if you intend this"})
        except Exception as e:
            return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    return wrapper


# --- perception ------------------------------------------------------------------------
@mcp.tool()
@tool
def screenshot(region: list | None = None):
    """Capture the screen (whole virtual desktop, or a model-space [x,y,w,h] region),
    downscaled into the model coordinate space. Returns base64 PNG + its width/height.
    Click using coordinates in THIS space."""
    return pc().screenshot(tuple(region) if region else None)


@mcp.tool()
@tool
def screen_size():
    """Report the real screen size, the model coordinate space, the scale factor, DPI mode,
    and monitor count. Call this first to understand the coordinate space."""
    return pc().screen_size()


@mcp.tool()
@tool
def cursor_position():
    """Current mouse position in both model and real pixel coordinates."""
    return pc().cursor_position()


# --- pointer ---------------------------------------------------------------------------
@mcp.tool()
@tool
def move(x: int, y: int, direct: bool = False):
    """Move the cursor to model-space (x, y)."""
    return pc().move(x, y, direct=direct)


@mcp.tool()
@tool
def click(x: int | None = None, y: int | None = None, button: str = "left",
          clicks: int = 1, keys: list | None = None, direct: bool = False):
    """Click at model-space (x, y) (or at the current position if omitted). button =
    left|right|middle. keys = held modifiers e.g. ['ctrl','shift']. direct = use SendInput
    backend (for games/RDP that ignore posted messages)."""
    return pc().click(x, y, button=button, clicks=clicks, keys=keys, direct=direct)


@mcp.tool()
@tool
def double_click(x: int | None = None, y: int | None = None, keys: list | None = None):
    """Double-click at model-space (x, y)."""
    return pc().double_click(x, y, keys=keys)


@mcp.tool()
@tool
def triple_click(x: int | None = None, y: int | None = None, keys: list | None = None):
    """Triple-click at model-space (x, y) (selects a line/paragraph)."""
    return pc().triple_click(x, y, keys=keys)


@mcp.tool()
@tool
def right_click(x: int | None = None, y: int | None = None):
    """Right-click (context menu) at model-space (x, y)."""
    return pc().right_click(x, y)


@mcp.tool()
@tool
def middle_click(x: int | None = None, y: int | None = None):
    """Middle-click at model-space (x, y)."""
    return pc().middle_click(x, y)


@mcp.tool()
@tool
def mouse_down(x: int | None = None, y: int | None = None, button: str = "left"):
    """Press and hold a mouse button at model-space (x, y) (pair with mouse_up)."""
    return pc().mouse_down(x, y, button=button)


@mcp.tool()
@tool
def mouse_up(x: int | None = None, y: int | None = None, button: str = "left"):
    """Release a held mouse button at model-space (x, y)."""
    return pc().mouse_up(x, y, button=button)


@mcp.tool()
@tool
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.4):
    """Drag from model-space (x1, y1) to (x2, y2)."""
    return pc().drag(x1, y1, x2, y2, button=button, duration=duration)


@mcp.tool()
@tool
def drag_path(points: list, button: str = "left", hold: float = 0.0):
    """Freeform drag through a list of model-space points [[x,y],[x,y],...]
    (e.g. signatures, lasso selection, drawing)."""
    return pc().drag_path(points, button=button, hold=hold)


@mcp.tool()
@tool
def scroll(amount: int = 3, direction: str = "down", x: int | None = None,
           y: int | None = None, dx: int | None = None, dy: int | None = None):
    """Scroll. Either direction (up|down|left|right) + amount, or raw dx/dy deltas.
    Optionally move to (x, y) first to scroll over a specific pane."""
    return pc().scroll(amount=amount, direction=direction, x=x, y=y, dx=dx, dy=dy)


# --- keyboard --------------------------------------------------------------------------
@mcp.tool()
@tool
def type_text(text: str, interval: float = 0.022):
    """Type a string at the focused input. Long text auto-routes via clipboard paste."""
    return pc().type_text(text, interval=interval)


@mcp.tool()
@tool
def key(keys, direct: bool = False):
    """Press a key chord. Accepts 'enter', 'ctrl+s', ['ctrl','a'], or a sequence
    ['ctrl+a','delete']. Win+* chords are gated (use run() for launching)."""
    return pc().key(keys, direct=direct)


@mcp.tool()
@tool
def hold_key(key: str, duration: float = 1.0):
    """Hold a single key down for a duration in seconds (e.g. arrow-key in a game)."""
    return pc().hold_key(key, duration=duration)


# --- apps / shell / windows ------------------------------------------------------------
@mcp.tool()
@tool
def open_app(name: str):
    """Launch an app by name or path via the Windows shell (e.g. 'notepad', 'msedge',
    'C:/path/app.exe')."""
    return pc().open_app(name)


@mcp.tool()
@tool
def run(command: str, shell: str = "powershell", timeout: int = 60, confirm: bool = False):
    """Run a shell command (powershell|cmd) and return exit/stdout/stderr. Destructive-
    looking commands (rm/del/format/shutdown/reg delete/...) require confirm=True."""
    return pc().run(command, shell=shell, timeout=timeout, confirm=confirm)


@mcp.tool()
@tool
def get_active_window():
    """Title + rect of the foreground window."""
    return pc().get_active_window()


@mcp.tool()
@tool
def list_windows():
    """Titles + rects of all visible top-level windows."""
    return pc().list_windows()


@mcp.tool()
@tool
def focus_window(title: str):
    """Bring a window (matched by title substring) to the foreground, restoring if minimized."""
    return pc().focus_window(title)


# --- clipboard / ocr / wait ------------------------------------------------------------
@mcp.tool()
@tool
def clipboard_get():
    """Read the current clipboard text."""
    return pc().clipboard_get()


@mcp.tool()
@tool
def clipboard_set(text: str):
    """Set the clipboard text."""
    return pc().clipboard_set(text)


@mcp.tool()
@tool
def find_text(query: str | None = None):
    """OCR the screen and return matched words with model-space center coordinates.
    Best-effort: requires pytesseract + a Tesseract install; reports clearly if missing.
    Use as a fallback to locate a label when you can't fix a coordinate from the screenshot."""
    return pc().find_text(query)


@mcp.tool()
@tool
def wait(seconds: float = 1.0):
    """Sleep for N seconds (let an app/page settle before the next screenshot)."""
    return pc().wait(seconds)


@mcp.tool()
@tool
def watch_mode(on: bool = True, glide_seconds: float = 0.6, label: str | None = None):
    """Turn on/off VISIBLE mode for watch-along / demos / screen recordings. When on, the
    cursor GLIDES smoothly between points (instead of teleporting) and a glowing ring +
    click ripples + an optional action label appear on screen so a human can follow every
    step - like the Perplexity/Claude computer-use demo videos. Off restores fast motion.
    Set `label` any time to narrate the current step."""
    global _overlay
    p = pc()
    if on:
        p.human = True
        p.move_dur = glide_seconds
        if _overlay is None:
            from .overlay import Overlay
            _overlay = Overlay().start()
            p.on_click = lambda rx, ry: _overlay.ripple(rx, ry)
        if label:
            _overlay.label(label)
        return {"watch_mode": "on", "glide_seconds": glide_seconds}
    else:
        p.human = False
        p.on_click = None
        if _overlay is not None:
            _overlay.stop()
            _overlay = None
        return {"watch_mode": "off"}


@mcp.tool()
@tool
def narrate(label: str):
    """Show a short on-screen label of what the agent is about to do (only visible when
    watch_mode is on). Use it to caption each step during a watch-along session."""
    if _overlay is not None:
        _overlay.label(label)
    return {"label": label}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
