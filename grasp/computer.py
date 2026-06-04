"""Grasp computer-control surface for Windows.

DPI awareness MUST be set before pyautogui/pydirectinput import, or every coordinate is
wrong on a scaled display. We set per-monitor-v2 awareness via ctypes at module import,
then bring in the input backend.

Screenshots are captured with mss (multi-monitor + DPI-correct, unlike pyautogui's
GDI grab) and downscaled into the model's coordinate space (see scale.Scaler). The model
sees and clicks in scaled space; we map back to physical pixels on execute.

Best-of-both action surface, synthesized from the public Anthropic computer_20250124
action enum, OpenAI computer_use_preview's call/output loop (path drag, scroll deltas),
and Perplexity's governance verbs (confirm before destructive, verify by screenshot)."""

import base64
import ctypes
import io
import subprocess
import time

# --- DPI awareness BEFORE any input/screenshot lib -------------------------------------
def _set_dpi_aware():
    try:
        # per-monitor-aware v2 (Win10 1703+); falls back through older APIs
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return "per-monitor-v2"
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return "per-monitor"
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


DPI_MODE = _set_dpi_aware()

import mss  # noqa: E402
from PIL import Image  # noqa: E402

# pyautogui drives clicks/typing; pydirectinput (SendInput, scancode) for games/RDP that
# ignore the WM_* events pyautogui posts. Both optional-import so the module loads even if
# one is missing.
try:
    import pyautogui
    pyautogui.FAILSAFE = False        # we gate destructive actions ourselves; corner-abort off
    pyautogui.PAUSE = 0.0             # we manage our own settle delays
except Exception:                     # pragma: no cover
    pyautogui = None

try:
    import pydirectinput
    pydirectinput.FAILSAFE = False
    pydirectinput.PAUSE = 0.0
except Exception:
    pydirectinput = None

try:
    import pyperclip
except Exception:
    pyperclip = None

from .scale import Scaler

SETTLE = 1.0          # default seconds to let UI settle after an action before next screenshot
TYPE_INTERVAL = 0.022 # ~22ms/char: fast but slow enough that focused apps don't drop chars
BIG_TEXT = 200        # type_text over this length goes via clipboard paste


def text_candidates(words, query, whole=False):
    """Find on-screen-text candidates for `query` among OCR `words` (each a dict with
    t,l,tp,w,h,key,wn). Multi-word queries match a consecutive run of words on one OCR line
    (key=block/par/line). Returns candidates sorted in reading order as
    [(top, left, (x0,y0,x1,y1), text)]. Pure, so it is unit-testable without a screen."""
    qt = query.strip().lower().split()
    cands = []
    if len(qt) <= 1:
        ql = query.strip().lower()
        for wd in words:
            wl = wd["t"].lower()
            if (whole and wl == ql) or (not whole and ql and ql in wl):
                cands.append((wd["tp"], wd["l"],
                              (wd["l"], wd["tp"], wd["l"] + wd["w"], wd["tp"] + wd["h"]), wd["t"]))
    else:
        from collections import defaultdict
        lines = defaultdict(list)
        for wd in words:
            lines[wd["key"]].append(wd)
        for ws in lines.values():
            ws.sort(key=lambda d: d["wn"])
            toks = [d["t"].lower() for d in ws]
            for s in range(0, len(ws) - len(qt) + 1):
                seg = toks[s:s + len(qt)]
                ok = all((qt[j] == seg[j]) if whole else (qt[j] in seg[j]) for j in range(len(qt)))
                if ok:
                    g = ws[s:s + len(qt)]
                    x0 = min(d["l"] for d in g); y0 = min(d["tp"] for d in g)
                    x1 = max(d["l"] + d["w"] for d in g); y1 = max(d["tp"] + d["h"] for d in g)
                    cands.append((y0, x0, (x0, y0, x1, y1), " ".join(d["t"] for d in g)))
    cands.sort(key=lambda c: (c[0], c[1]))   # reading order: top-to-bottom, left-to-right
    return cands


class SafetyError(Exception):
    """Raised when a destructive action is attempted without confirm=True."""


class Computer:
    def __init__(self, settle=SETTLE, allow_destructive=False, human=False, move_dur=0.6):
        self.settle = settle
        self.allow_destructive = allow_destructive   # global override; per-call confirm also works
        # human=True makes the cursor GLIDE between points (smooth, watchable - like a screen
        # recording) instead of teleporting, with a brief hover before each click. Use it for
        # demos / watch-along sessions; leave off for fast headless-style automation.
        self.human = human
        self.move_dur = move_dur
        self.on_click = None          # optional callback(x_real, y_real) - e.g. overlay ripple
        self._sct = mss.mss()
        self._scaler = self._build_scaler()

    # --- smooth motion -----------------------------------------------------------------
    def _glide(self, rx, ry, dur=None):
        """Move the real cursor from its current spot to (rx, ry) along an eased path so a
        viewer can watch it travel. ~60 steps/sec, smoothstep ease-in-out."""
        be = self._backend(False)
        dur = self.move_dur if dur is None else dur
        sx, sy = pyautogui.position()
        steps = max(2, int(dur / 0.016))
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)               # smoothstep
            be.moveTo(round(sx + (rx - sx) * e), round(sy + (ry - sy) * e))
            time.sleep(dur / steps)

    def _goto(self, rx, ry, direct=False, glide=None):
        """Position the cursor at real (rx, ry): glide if in human mode, else teleport."""
        if (self.human if glide is None else glide):
            self._glide(rx, ry)
        else:
            self._backend(direct).moveTo(rx, ry)

    # --- geometry ----------------------------------------------------------------------
    def _virtual_rect(self):
        """Bounding rect of ALL monitors (mss monitor[0]) -> (left, top, width, height)."""
        m = self._sct.monitors[0]
        return m["left"], m["top"], m["width"], m["height"]

    def _build_scaler(self):
        _, _, w, h = self._virtual_rect()
        return Scaler(w, h)

    def screen_size(self):
        """Real virtual-desktop size, the model-space size, and the scale in effect."""
        left, top, w, h = self._virtual_rect()
        info = self._scaler.info()
        info.update({"origin": [left, top], "dpi_mode": DPI_MODE,
                     "monitors": len(self._sct.monitors) - 1})
        return info

    def _to_real(self, x, y):
        """model coord -> absolute physical screen coord (offset by virtual origin)."""
        left, top, _, _ = self._virtual_rect()
        rx, ry = self._scaler.to_real(x, y)
        return left + rx, top + ry

    # --- capture -----------------------------------------------------------------------
    def screenshot(self, region=None):
        """PNG (base64) of the whole virtual desktop (or a model-space region box),
        downscaled into model space. region = (x, y, w, h) in model coords."""
        if region:
            rx, ry = self._to_real(region[0], region[1])
            rw, rh = self._scaler.to_real(region[2], region[3])
            box = {"left": rx, "top": ry, "width": max(1, rw), "height": max(1, rh)}
            grab = self._sct.grab(box)
            img = Image.frombytes("RGB", grab.size, grab.rgb)
        else:
            left, top, w, h = self._virtual_rect()
            grab = self._sct.grab({"left": left, "top": top, "width": w, "height": h})
            img = Image.frombytes("RGB", grab.size, grab.rgb)
            tw, th = self._scaler.scaled
            if (img.width, img.height) != (tw, th):
                img = img.resize((tw, th), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return {"image_b64": base64.b64encode(buf.getvalue()).decode(),
                "width": img.width, "height": img.height,
                "scaled": region is None}

    def cursor_position(self):
        x, y = pyautogui.position()
        left, top, _, _ = self._virtual_rect()
        mx, my = self._scaler.to_model(x - left, y - top)
        return {"model": [mx, my], "real": [x, y]}

    def real_to_model(self, rx, ry):
        """Map an absolute physical-pixel point to model-space coordinates."""
        left, top, _, _ = self._virtual_rect()
        return self._scaler.to_model(rx - left, ry - top)

    # --- pointer -----------------------------------------------------------------------
    def _backend(self, direct):
        be = pydirectinput if (direct and pydirectinput) else pyautogui
        if be is None:
            raise RuntimeError("no input backend available (install pyautogui)")
        return be

    def move(self, x, y, direct=False, duration=None):
        rx, ry = self._to_real(x, y)
        if duration is not None:
            self._glide(rx, ry, duration)
        else:
            self._goto(rx, ry, direct)
        return self._settled({"moved": [x, y]})

    def _apply_keys(self, keys, down):
        """Hold/release modifier keys around a click (keys = list like ['ctrl','shift'])."""
        if not keys:
            return
        be = self._backend(False)
        for k in keys:
            (be.keyDown if down else be.keyUp)(k)

    def click(self, x=None, y=None, button="left", clicks=1, keys=None, direct=False):
        be = self._backend(direct)
        if x is not None:
            rx, ry = self._to_real(x, y)
            self._goto(rx, ry, direct)
            if self.human:
                time.sleep(0.18)                  # brief hover so a viewer sees the target
        self._apply_keys(keys, True)
        try:
            be.click(button=button, clicks=clicks)
        finally:
            self._apply_keys(keys, False)
        if self.on_click:
            try:
                rx, ry = pyautogui.position()
                self.on_click(rx, ry)
            except Exception:
                pass
        return self._settled({"clicked": [x, y], "button": button, "clicks": clicks})

    def double_click(self, x=None, y=None, button="left", keys=None, direct=False):
        return self.click(x, y, button=button, clicks=2, keys=keys, direct=direct)

    def triple_click(self, x=None, y=None, button="left", keys=None, direct=False):
        return self.click(x, y, button=button, clicks=3, keys=keys, direct=direct)

    def right_click(self, x=None, y=None, keys=None, direct=False):
        return self.click(x, y, button="right", keys=keys, direct=direct)

    def middle_click(self, x=None, y=None, keys=None, direct=False):
        return self.click(x, y, button="middle", keys=keys, direct=direct)

    def mouse_down(self, x=None, y=None, button="left", direct=False):
        be = self._backend(direct)
        if x is not None:
            rx, ry = self._to_real(x, y)
            be.moveTo(rx, ry)
        be.mouseDown(button=button)
        return self._settled({"mouse_down": [x, y], "button": button})

    def mouse_up(self, x=None, y=None, button="left", direct=False):
        be = self._backend(direct)
        if x is not None:
            rx, ry = self._to_real(x, y)
            be.moveTo(rx, ry)
        be.mouseUp(button=button)
        return self._settled({"mouse_up": [x, y], "button": button})

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4, direct=False):
        be = self._backend(direct)
        rx1, ry1 = self._to_real(x1, y1)
        rx2, ry2 = self._to_real(x2, y2)
        be.moveTo(rx1, ry1)
        be.mouseDown(button=button)
        try:
            if hasattr(be, "moveTo"):
                be.moveTo(rx2, ry2, duration=duration) if be is pyautogui else be.moveTo(rx2, ry2)
        finally:
            be.mouseUp(button=button)
        return self._settled({"dragged": [[x1, y1], [x2, y2]], "button": button})

    def _drag_seg(self, rx, ry, dur=0.05):
        """Interpolate the (button-held) cursor from its current spot to (rx, ry), so a drag
        is one continuous motion the app samples fully (and a viewer can watch)."""
        be = self._backend(False)
        sx, sy = pyautogui.position()
        steps = max(1, int(dur / 0.012))
        for i in range(1, steps + 1):
            t = i / steps
            be.moveTo(round(sx + (rx - sx) * t), round(sy + (ry - sy) * t))
            time.sleep(dur / steps)

    def drag_path(self, points, button="left", hold=0.0, direct=False, seg_dur=None):
        """Freeform drag through model-space [[x,y],...] (OpenAI-style path drag). In human
        mode (or with seg_dur) each segment is interpolated for a smooth, watchable stroke."""
        if len(points) < 2:
            raise ValueError("drag_path needs >= 2 points")
        be = self._backend(direct)
        rsx, rsy = self._to_real(*points[0])
        self._goto(rsx, rsy, direct)
        time.sleep(0.06)                          # settle so the button-down lands here
        be.mouseDown(button=button)
        time.sleep(0.05)                          # let the app register the stroke start
        smooth = self.human or seg_dur
        try:
            for px, py in points[1:]:
                rx, ry = self._to_real(px, py)
                if smooth:
                    self._drag_seg(rx, ry, seg_dur or 0.05)
                else:
                    be.moveTo(rx, ry)
                if hold:
                    time.sleep(hold)
        finally:
            time.sleep(0.05)
            be.mouseUp(button=button)
            time.sleep(0.05)                      # ensure the release registers (no connecting line)
        return self._settled({"drag_path": len(points), "button": button})

    def scroll(self, amount=3, direction="down", x=None, y=None, dx=None, dy=None,
               keys=None, direct=False):
        """Scroll by clicks. direction+amount OR raw dx/dy (dy>0 = up, like pyautogui).
        `keys` holds modifiers around the wheel (e.g. ['ctrl'] for zoom / pinch)."""
        be = self._backend(direct)
        if x is not None and y is not None:
            rx, ry = self._to_real(x, y)
            be.moveTo(rx, ry)
        self._apply_keys(keys, True)
        try:
            if dy is not None or dx is not None:
                if dy:
                    be.scroll(int(dy))
                if dx and hasattr(be, "hscroll"):
                    be.hscroll(int(dx))
                return self._settled({"scroll": {"dx": dx, "dy": dy}, "keys": keys})
            clicks = abs(int(amount))
            clicks = clicks if direction in ("up", "right") else -clicks
            if direction in ("up", "down"):
                be.scroll(clicks)
            elif hasattr(be, "hscroll"):
                be.hscroll(clicks)
            return self._settled({"scroll": {"direction": direction, "amount": amount}, "keys": keys})
        finally:
            self._apply_keys(keys, False)

    # --- keyboard ----------------------------------------------------------------------
    DESTRUCTIVE_KEYS = {"delete", "del"}

    def type_text(self, text, interval=TYPE_INTERVAL, direct=False, confirm=False):
        if not text:
            return {"typed": 0}
        # Route via clipboard paste for long text OR anything containing a space:
        # char-by-char typewrite silently drops spaces in some apps and web views
        # (e.g. QtWebEngine inputs), so "Stock Options" arrived as "StockOptions".
        # Paste is reliable; the prior clipboard is restored afterwards.
        if pyperclip is not None and (len(text) >= BIG_TEXT or " " in text):
            prev = None
            try:
                prev = pyperclip.paste()
            except Exception:
                pass
            pyperclip.copy(text)
            be = self._backend(False)
            be.hotkey("ctrl", "v")
            res = self._settled({"typed": len(text), "via": "paste"})
            if prev is not None:
                try:
                    time.sleep(0.05)
                    pyperclip.copy(prev)
                except Exception:
                    pass
            return res
        be = self._backend(direct)
        be.typewrite(text, interval=interval)
        return self._settled({"typed": len(text), "via": "keys"})

    def key(self, keys, direct=False):
        """Press a chord. keys = 'enter' | 'ctrl+s' | ['ctrl','s'] | ['ctrl+a','delete']."""
        be = self._backend(direct)
        seq = keys if isinstance(keys, list) else [keys]
        out = []
        for chord in seq:
            parts = [p.strip() for p in chord.replace("+", " ").split()] if isinstance(chord, str) else [chord]
            self._guard_keys(parts)
            be.hotkey(*parts) if len(parts) > 1 else be.press(parts[0])
            out.append(chord)
        return self._settled({"pressed": out})

    def hold_key(self, key, duration=1.0, direct=False):
        be = self._backend(direct)
        be.keyDown(key)
        time.sleep(max(0.0, duration))
        be.keyUp(key)
        return self._settled({"held": key, "duration": duration})

    def _guard_keys(self, parts):
        low = {p.lower() for p in parts}
        # Win+R (Run), Win+X power menu etc. flagged as risky launch surfaces upstream
        if "win" in low and not self.allow_destructive:
            raise SafetyError(f"chord {'+'.join(parts)} can launch arbitrary commands; "
                              "set allow_destructive or call run() explicitly")

    # --- apps / shell ------------------------------------------------------------------
    def open_app(self, name):
        """Launch an app by name/path via the shell ('start'), e.g. 'notepad', 'msedge'."""
        subprocess.Popen(["cmd", "/c", "start", "", name], shell=False)
        time.sleep(self.settle)
        return {"opened": name}

    RISKY_SHELL = ("rm ", "del ", "rmdir", "format", "reg delete", "shutdown",
                   "diskpart", "rd /s", "remove-item")

    def run(self, command, shell="powershell", timeout=60, confirm=False):
        """Run a shell command. Destructive-looking commands require confirm=True."""
        low = command.lower()
        if any(tok in low for tok in self.RISKY_SHELL) and not (confirm or self.allow_destructive):
            raise SafetyError(f"command looks destructive: {command!r}; pass confirm=True to run")
        if shell == "powershell":
            argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = ["cmd", "/c", command]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return {"exit": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-4000:]}

    # --- windows -----------------------------------------------------------------------
    def get_active_window(self):
        try:
            import pygetwindow as gw
            w = gw.getActiveWindow()
            if not w:
                return {"active": None}
            return {"title": w.title, "rect": [w.left, w.top, w.width, w.height]}
        except Exception as e:
            return {"error": str(e)}

    def list_windows(self):
        try:
            import pygetwindow as gw
            out = []
            for w in gw.getAllWindows():
                if w.title and w.visible:
                    out.append({"title": w.title, "rect": [w.left, w.top, w.width, w.height]})
            return {"windows": out}
        except Exception as e:
            return {"error": str(e)}

    def focus_window(self, title):
        try:
            import pygetwindow as gw
            wins = gw.getWindowsWithTitle(title)
            if not wins:
                return {"focused": None, "error": f"no window matching {title!r}"}
            w = wins[0]
            if w.isMinimized:
                w.restore()
            w.activate()
            return self._settled({"focused": w.title})
        except Exception as e:
            return {"error": str(e)}

    # --- clipboard ---------------------------------------------------------------------
    def clipboard_get(self):
        return {"text": pyperclip.paste() if pyperclip else None}

    def clipboard_set(self, text):
        if pyperclip:
            pyperclip.copy(text)
        return {"set": len(text or "")}

    # --- ocr (best-effort) -------------------------------------------------------------
    def find_text(self, query=None):
        """OCR the screen, return matched words with model-space center coords.
        Best-effort: needs pytesseract + a Tesseract install; reports clearly if absent."""
        try:
            import pytesseract
        except Exception:
            return {"error": "pytesseract not installed", "matches": []}
        left, top, w, h = self._virtual_rect()
        grab = self._sct.grab({"left": left, "top": top, "width": w, "height": h})
        img = Image.frombytes("RGB", grab.size, grab.rgb)
        try:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        except Exception as e:
            return {"error": f"tesseract not available: {e}", "matches": []}
        matches = []
        q = (query or "").lower()
        for i, word in enumerate(data["text"]):
            if not word.strip():
                continue
            if q and q not in word.lower():
                continue
            cx = data["left"][i] + data["width"][i] / 2
            cy = data["top"][i] + data["height"][i] / 2
            mx, my = self._scaler.to_model(cx, cy)
            matches.append({"text": word, "model": [mx, my], "conf": data["conf"][i]})
        return {"matches": matches, "count": len(matches)}

    def locate_text(self, query, occurrence=1, whole=False):
        """Model-space center of on-screen text matching `query`. Multi-word queries are
        matched as a consecutive run of OCR words on one line (so 'Save copy' / 'Find all'
        resolve), `occurrence` picks the Nth match in reading order, `whole` requires an
        exact word match. Returns {found, model:[x,y], text, matches} (no clicking)."""
        try:
            import pytesseract
        except Exception:
            return {"found": False, "error": "pytesseract not installed"}
        left, top, w, h = self._virtual_rect()
        grab = self._sct.grab({"left": left, "top": top, "width": w, "height": h})
        img = Image.frombytes("RGB", grab.size, grab.rgb)
        try:
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        except Exception as e:
            return {"found": False, "error": f"tesseract not available: {e}"}
        words = []
        for i in range(len(data["text"])):
            if not data["text"][i].strip():
                continue
            words.append({"t": data["text"][i], "l": data["left"][i], "tp": data["top"][i],
                          "w": data["width"][i], "h": data["height"][i],
                          "key": (data["block_num"][i], data["par_num"][i], data["line_num"][i]),
                          "wn": data["word_num"][i]})
        cands = text_candidates(words, query, whole)
        if not cands:
            return {"found": False, "query": query, "matches": 0}
        occ = min(max(1, int(occurrence)), len(cands))
        _, _, box, text = cands[occ - 1]
        mx, my = self._scaler.to_model((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        return {"found": True, "model": [mx, my], "text": text, "matches": len(cands)}

    def click_text(self, query, occurrence=1, button="left", clicks=1, whole=False,
                   keys=None, direct=False):
        """Find on-screen text and click its center — deterministic, no guessing pixel
        coordinates from a screenshot. Resolves the crowded-toolbar / small-target problem."""
        loc = self.locate_text(query, occurrence=occurrence, whole=whole)
        if not loc.get("found"):
            return {"clicked": False, **loc}
        x, y = loc["model"]
        r = self.click(x, y, button=button, clicks=clicks, keys=keys, direct=direct)
        return {**r, "matched": loc["text"], "matches": loc["matches"], "model": [x, y]}

    def hover(self, x, y, direct=False):
        """Move to (x, y) and jiggle a pixel to emit a real OS mouse-move, so hover /
        mouseenter handlers (tooltips, HUDs) fire even in web views where a bare SetCursorPos
        doesn't dispatch a DOM mousemove."""
        rx, ry = self._to_real(x, y)
        be = self._backend(direct)
        be.moveTo(rx, ry); time.sleep(0.03)
        be.moveTo(rx + 1, ry + 1); time.sleep(0.02)
        be.moveTo(rx, ry)
        return self._settled({"hover": [x, y]})

    # --- misc --------------------------------------------------------------------------
    def wait(self, seconds=1.0):
        time.sleep(max(0.0, float(seconds)))
        return {"waited": seconds}

    def _settled(self, payload):
        time.sleep(self.settle)
        return payload
