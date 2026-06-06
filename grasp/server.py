"""Grasp MCP server. Exposes the Computer surface as MCP tools over stdio.

Every tool returns a small JSON dict. Screenshots return base64 PNG plus the model-space
dimensions, so the calling agent knows the coordinate space it must click in. The agent
loop is: screenshot -> reason in model space -> act -> screenshot to verify."""

import base64
import functools
import json
import re
import time

from mcp.server.fastmcp import FastMCP

from . import extras, ground, macros, system, vision
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


def _parse_xy(raw: str, w: int, h: int):
    """Pull {found,x,y} out of a vision model's reply, clamped to the image."""
    def clamp(x, y):
        return {"found": True,
                "x": max(0, min(int(round(float(x))), w - 1)),
                "y": max(0, min(int(round(float(y))), h - 1))}
    m = re.search(r"\{[^{}]*\}", raw or "", re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if d.get("found") and d.get("x") is not None and d.get("y") is not None:
                return clamp(d["x"], d["y"])
            if d.get("found") is False:
                return {"found": False}
        except Exception:
            pass
    if raw and "false" not in raw.lower():
        nums = re.findall(r"-?\d+(?:\.\d+)?", raw)
        if len(nums) >= 2:
            return clamp(nums[0], nums[1])
    return {"found": False}


@mcp.tool()
@tool
def see(question: str):
    """Answer a question about the CURRENT screen WITHOUT sending the screenshot to the
    calling agent. Grasp captures the screen and an off-cap vision model (MiniMax)
    interprets it, so this costs no Anthropic tokens. Returns {answer, width, height}.
    Use this instead of screenshot() whenever you only need to KNOW something on screen."""
    backend = vision.active_backend()
    if backend == "minimax" and not vision.is_available():
        raise vision.VisionError(
            "MiniMax key not found; set MINIMAX_API_KEY or the Claude-NIM auth file.")
    if backend == "liquid":
        from . import vision_liquid
        if not vision_liquid.is_available():
            raise RuntimeError(
                "Liquid backend deps missing. pip install transformers accelerate torch torchvision.")
    shot = pc().screenshot()
    png = base64.b64decode(shot["image_b64"])
    prompt = (f"You are looking at a {shot['width']}x{shot['height']} screenshot of a "
              f"computer screen. {question}")
    return {"answer": vision.vlm(prompt, png),
            "width": shot["width"], "height": shot["height"], "backend": backend}


@mcp.tool()
@tool
def locate(target: str):
    """Find the on-screen element described by `target` and return click coordinates in
    MODEL space, cheaply and WITHOUT sending the screenshot to the calling agent. Tries,
    in order: the Windows accessibility tree (free, exact, instant), then OCR of on-screen
    text (free, offline), then an off-cap vision model (MiniMax) as a fallback. Returns
    {found, x, y, method}. Then call click(x, y). Prefer this over screenshot+reason."""
    shot = pc().screenshot()
    png = base64.b64decode(shot["image_b64"])
    w, h = shot["width"], shot["height"]

    def clamp(x, y, method):
        return {"target": target, "found": True,
                "x": max(0, min(int(x), w - 1)), "y": max(0, min(int(y), h - 1)),
                "method": method, "width": w, "height": h}

    # Tier 1: UI Automation tree (exact element rect, instant, $0)
    try:
        u = ground.locate_uia(target)
        if u:
            mx, my = pc().real_to_model(u[0], u[1])
            return clamp(mx, my, "uia")
    except Exception:
        pass

    # Tier 2: OCR on the model-space screenshot (free, offline)
    try:
        o = ground.locate_ocr(png, target)
        if o:
            return clamp(o[0], o[1], "ocr")
    except Exception:
        pass

    # Tier 3: off-cap VLM (MiniMax or Liquid, depending on active backend)
    if vision.is_available():
        prompt = (
            f"The image is a {w}x{h} pixel screenshot (origin top-left). "
            f'Find: "{target}". Reply with ONLY compact JSON and no other text: '
            f'{{"found": true, "x": <int>, "y": <int>}} giving the pixel coordinates of the '
            f'CENTER of the best-matching clickable element, or {{"found": false}} if it is '
            f"not visible.")
        raw = vision.vlm(prompt, png)
        return {"target": target, "method": f"vlm:{vision.active_backend()}", "width": w, "height": h,
                **_parse_xy(raw, w, h)}

    return {"target": target, "found": False, "method": "none", "width": w, "height": h}


@mcp.tool()
@tool
def snapshot():
    """List the interactive elements of the FOREGROUND window from the Windows
    accessibility (UIA) tree: each gets a stable `ref`, plus name, role, enabled, value,
    and its model-space center (x, y). Then act with click_element(ref) / set_value(ref,
    text) - element actions are far more reliable than blind pixel clicks, and free (no
    vision tokens). Re-run snapshot after the UI changes to refresh refs."""
    items = ground.snapshot_uia()
    if items is None:
        return {"available": False,
                "hint": "pip install uiautomation to enable element control"}
    out = []
    for it in items:
        left, top, right, bottom = it.pop("_rect")
        cx, cy = pc().real_to_model((left + right) // 2, (top + bottom) // 2)
        it["x"], it["y"] = int(cx), int(cy)
        out.append(it)
    return {"count": len(out), "elements": out}


def _resolve_ctrl(ref, name):
    if ref:
        return ground.registry_get(ref)
    if name:
        return ground.find_ctrl(name)
    return None


@mcp.tool()
@tool
def click_element(ref: str | None = None, name: str | None = None, button: str = "left"):
    """Click a UI element by `ref` (from snapshot) or by `name`, using the accessibility
    Invoke/Toggle action when available (more reliable than a pixel click), otherwise a
    center click. Run snapshot() first to get refs. Returns {found, method, x, y}."""
    ctrl = _resolve_ctrl(ref, name)
    if ctrl is None:
        return {"found": False, "hint": "run snapshot() first, or check ref/name"}
    if button == "left" and ground.invoke_ctrl(ctrl):
        return {"found": True, "method": "uia-invoke"}
    center = ground.ctrl_center_real(ctrl)
    if not center:
        return {"found": False}
    mx, my = pc().real_to_model(center[0], center[1])
    pc().click(int(mx), int(my), button=button)
    return {"found": True, "method": "click", "x": int(mx), "y": int(my)}


@mcp.tool()
@tool
def set_value(text: str, ref: str | None = None, name: str | None = None):
    """Set a text field / combo value by `ref` or `name` using the accessibility
    ValuePattern (atomic and reliable), falling back to click + type. Run snapshot()
    first. Returns {found, method}."""
    ctrl = _resolve_ctrl(ref, name)
    if ctrl is None:
        return {"found": False, "hint": "run snapshot() first, or check ref/name"}
    if ground.set_value_ctrl(ctrl, text):
        return {"found": True, "method": "uia-setvalue"}
    center = ground.ctrl_center_real(ctrl)
    if not center:
        return {"found": False}
    mx, my = pc().real_to_model(center[0], center[1])
    p = pc()
    p.click(int(mx), int(my))
    p.type_text(text)
    return {"found": True, "method": "type", "x": int(mx), "y": int(my)}


@mcp.tool()
@tool
def read_screen(region: list | None = None):
    """OCR the screen (or a model-space [x, y, w, h] region) to plain text, locally and
    free (no vision tokens). Use to READ content without sending a screenshot to the
    agent. Returns {text, width, height}."""
    shot = pc().screenshot(tuple(region) if region else None)
    png = base64.b64decode(shot["image_b64"])
    return {"text": ground.ocr_text(png), "width": shot["width"], "height": shot["height"]}


# --- Liquid VLM backend: structured extraction from images -----------------------------
@mcp.tool()
@tool
def extract_from_image(image_path: str, fields_yaml: str):
    """Extract structured fields from an image file using LFM2.5-VL-Extract (on-device).
    Pass a YAML list of fields to extract. Returns JSON with those fields.
    Uses GRASP_LIQUID_MODEL env var to pick model (default: LFM2.5-VL-450M-Extract).

    Example fields_yaml:
      color: dominant color in the image
      count: number of visible objects
      text: any text visible in the image"""
    from . import vision_liquid
    if not vision_liquid.is_available():
        raise RuntimeError(
            "Liquid backend not available. pip install transformers accelerate torch.")
    with open(image_path, "rb") as _f:
        png_bytes = _f.read()
    # Quick header check — accept PNG or JPEG
    if png_bytes[:4] not in (b"\x89PNG", b"\xff\xd8\xff") and png_bytes[:2] != b"\xff\xd8":
        raise ValueError(f"{image_path} does not look like a PNG or JPEG image")
    import logging
    logging.getLogger("transformers").setLevel(logging.ERROR)
    result = vision_liquid.extract(png_bytes, fields_yaml)
    return {"json": result, "image": image_path,
            "model": __import__("os").environ.get("GRASP_LIQUID_MODEL",
                                                   "LiquidAI/LFM2.5-VL-450M-Extract")}


@mcp.tool()
@tool
def vision_status():
    """Show the active VLM backend and availability of each option.
    Toggle with GRASP_VLM_BACKEND env var (minimax | liquid)."""
    from . import vision_liquid
    import os
    liquid_model = os.environ.get("GRASP_LIQUID_MODEL", "LiquidAI/LFM2.5-VL-1.6B-Extract")
    return {"active_backend": vision.active_backend(),
            "liquid_model": liquid_model,
            "liquid_models_available": {
                "450M": "LiquidAI/LFM2.5-VL-450M-Extract",
                "1.6B": "LiquidAI/LFM2.5-VL-1.6B-Extract",
            },
            "minimax_available": vision.is_available(),
            "liquid_available": vision_liquid.is_available(),
            "how_to_switch": {
                "backend": "set GRASP_VLM_BACKEND=liquid|minimax",
                "model": "set GRASP_LIQUID_MODEL=LiquidAI/LFM2.5-VL-450M-Extract|LiquidAI/LFM2.5-VL-1.6B-Extract",
            }}


# --- verify after acting (W2): wait for state instead of guessing -----------------------
@mcp.tool()
@tool
def assert_screen(text: str, region: list | None = None):
    """True if `text` is visible on screen right now (local OCR, no vision tokens).
    Use to branch instead of proceeding blindly. Returns {present}."""
    shot = pc().screenshot(tuple(region) if region else None)
    body = ground.ocr_text(base64.b64decode(shot["image_b64"]))
    return {"present": text.lower() in body.lower(), "text": text}


@mcp.tool()
@tool
def wait_for_text(text: str, timeout: float = 10.0, interval: float = 0.6,
                  region: list | None = None):
    """Poll the screen with OCR until `text` appears (or timeout). Use right after an
    action to wait for its result instead of sleeping a fixed time. Returns
    {found, waited_ms}."""
    deadline = time.monotonic() + timeout
    waited = 0.0
    while True:
        shot = pc().screenshot(tuple(region) if region else None)
        body = ground.ocr_text(base64.b64decode(shot["image_b64"]))
        if text.lower() in body.lower():
            return {"found": True, "waited_ms": int(waited * 1000), "text": text}
        if time.monotonic() >= deadline:
            return {"found": False, "waited_ms": int(waited * 1000), "text": text}
        time.sleep(interval)
        waited += interval


@mcp.tool()
@tool
def wait_for_window(title: str, timeout: float = 10.0, interval: float = 0.4):
    """Poll until a window whose title contains `title` exists (e.g. after open_app).
    Returns {found, waited_ms, match}."""
    deadline = time.monotonic() + timeout
    waited = 0.0
    t = title.lower()
    while True:
        match = [x for x in system.window_titles() if t in x.lower()]
        if match:
            return {"found": True, "waited_ms": int(waited * 1000), "match": match[0]}
        if time.monotonic() >= deadline:
            return {"found": False, "waited_ms": int(waited * 1000)}
        time.sleep(interval)
        waited += interval


@mcp.tool()
@tool
def wait_for_element(name: str, timeout: float = 10.0, interval: float = 0.5):
    """Poll the foreground accessibility tree until an element whose name contains `name`
    appears. Returns {found, waited_ms}."""
    deadline = time.monotonic() + timeout
    waited = 0.0
    t = name.lower()
    while True:
        items = ground.snapshot_uia() or []
        if any(t in (it.get("name") or "").lower() for it in items):
            return {"found": True, "waited_ms": int(waited * 1000)}
        if time.monotonic() >= deadline:
            return {"found": False, "waited_ms": int(waited * 1000)}
        time.sleep(interval)
        waited += interval


# --- system control (W3): native APIs beat pixels ---------------------------------------
@mcp.tool()
@tool
def window(title: str, action: str, x: int | None = None, y: int | None = None,
           w: int | None = None, h: int | None = None):
    """Manage a window by title: action = activate | minimize | maximize | restore |
    close | move | resize. `move` needs x,y; `resize` needs w,h."""
    return system.window_action(title, action, x=x, y=y, w=w, h=h)


@mcp.tool()
@tool
def list_processes(name: str | None = None):
    """List running processes (optionally filtered by name substring). [{pid, name}]."""
    return {"processes": system.list_processes(name)}


@mcp.tool()
@tool
def process_running(name: str):
    """True if a process whose name contains `name` is running."""
    return {"running": system.process_running(name)}


@mcp.tool()
@tool
def kill_process(pid: int | None = None, name: str | None = None, confirm: bool = False):
    """Terminate a process by pid or name. DESTRUCTIVE: re-issue with confirm=True."""
    return system.kill_process(pid=pid, name=name, confirm=confirm)


@mcp.tool()
@tool
def list_dir(path: str):
    """List a directory's entries (or report a file's size)."""
    return system.list_dir(path)


@mcp.tool()
@tool
def file_read(path: str, max_bytes: int = 20000):
    """Read a text file (truncated to max_bytes)."""
    return system.file_read(path, max_bytes)


@mcp.tool()
@tool
def file_write(path: str, content: str, confirm: bool = False, append: bool = False):
    """Write text to a file. Overwriting an existing file needs confirm=True (append=True
    to add to the end). DESTRUCTIVE when overwriting."""
    return system.file_write(path, content, confirm=confirm, append=append)


@mcp.tool()
@tool
def file_search(root: str, pattern: str):
    """Find files matching a glob `pattern` (e.g. *.pdf) under `root`."""
    return system.file_search(root, pattern)


# --- W4: voice, notifications, media, monitors, macros ----------------------------------
@mcp.tool()
@tool
def speak(text: str, rate: int | None = None):
    """Speak text aloud via offline TTS (SAPI). Pairs with voice input for a hands-free
    loop. `rate` is words-per-minute (default voice speed)."""
    return extras.speak(text, rate)


@mcp.tool()
@tool
def notify(title: str, message: str):
    """Show a Windows notification (Action Center toast). Use to flag completion or ask
    for attention without stealing focus."""
    return extras.notify(title, message)


@mcp.tool()
@tool
def volume(level: float | None = None, mute: bool | None = None):
    """Get or set the system volume. `level` is 0.0-1.0; `mute` toggles. Omit both to read
    the current state. Returns {level, muted}."""
    return extras.volume(level=level, mute=mute)


@mcp.tool()
@tool
def media(action: str):
    """Send a media key: play_pause | next | prev | stop | vol_up | vol_down | mute."""
    return extras.media(action)


@mcp.tool()
@tool
def brightness(level: int | None = None):
    """Get or set screen brightness (0-100). Omit `level` to read it."""
    return extras.brightness(level)


@mcp.tool()
@tool
def list_monitors():
    """List monitors with their position and size (index 1..N; 0 is the virtual desktop)."""
    return extras.list_monitors()


@mcp.tool()
@tool
def screenshot_monitor(index: int, max_side: int = 1280):
    """Capture one monitor (1-based) as a base64 PNG, downscaled to max_side."""
    return extras.screenshot_monitor(index, max_side)


@mcp.tool()
@tool
def macro_save(name: str, steps: list):
    """Save a replayable macro. steps = [{action, args}] where action is a Computer verb
    (click, type_text, key, scroll, drag, open_app) or "sleep" ({seconds}); coordinates
    are model-space. Replay with macro_run."""
    return macros.save(name, steps)


@mcp.tool()
@tool
def macro_run(name: str):
    """Replay a saved macro step by step. Returns per-step results."""
    return macros.run(name, pc())


@mcp.tool()
@tool
def macro_list():
    """List saved macro names."""
    return {"macros": macros.list_macros()}


@mcp.tool()
@tool
def macro_delete(name: str):
    """Delete a saved macro."""
    return {"deleted": macros.delete(name)}


@mcp.tool()
@tool
def macro_record_start(name: str):
    """Start recording real mouse clicks + keystrokes into a named macro (pynput).
    Stop with macro_record_stop. Coordinates are captured in model space."""
    return macros.record_start(name, pc())


@mcp.tool()
@tool
def macro_record_stop():
    """Stop recording and save the macro (inter-event gaps become sleeps)."""
    return macros.record_stop()


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
           y: int | None = None, dx: int | None = None, dy: int | None = None,
           keys: list | None = None):
    """Scroll. Either direction (up|down|left|right) + amount, or raw dx/dy deltas.
    Optionally move to (x, y) first to scroll over a specific pane. `keys` holds
    modifiers around the wheel, e.g. keys=['ctrl'] for zoom / pinch-to-zoom."""
    return pc().scroll(amount=amount, direction=direction, x=x, y=y, dx=dx, dy=dy, keys=keys)


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
def click_text(query: str, occurrence: int = 1, button: str = "left", clicks: int = 1,
               whole: bool = False, keys: list | None = None):
    """Click on-screen TEXT by its label instead of guessing pixel coordinates — the
    reliable way to hit small/crowded targets (toolbar buttons, sidebar tabs, menu items,
    list rows). OCRs the screen, finds `query` (multi-word queries match a run of words on
    one line, so 'Save copy' / 'Find all' work), and clicks the match's center. `occurrence`
    picks the Nth match in reading order; `whole` requires an exact word; `keys` holds
    modifiers. Prefer this over screenshot->estimate-coordinate->click."""
    return pc().click_text(query, occurrence=occurrence, button=button, clicks=clicks,
                           whole=whole, keys=keys)


@mcp.tool()
@tool
def locate_text(query: str, occurrence: int = 1, whole: bool = False):
    """Model-space center of on-screen text matching `query`, WITHOUT clicking (multi-word
    aware, `occurrence` = Nth match). Use to read a target's coordinates precisely."""
    return pc().locate_text(query, occurrence=occurrence, whole=whole)


@mcp.tool()
@tool
def hover(x: int, y: int):
    """Move the cursor to (x, y) and jiggle a pixel so a real OS mouse-move fires —
    triggers hover / mouseenter UI (tooltips, pop-up HUDs, menus) even in web views, where a
    plain move() (SetCursorPos) does not dispatch a DOM mousemove."""
    return pc().hover(x, y)


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
