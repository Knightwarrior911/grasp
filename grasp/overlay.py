"""Visible-cursor overlay for demos and watch-along sessions.

The agent drives the real OS cursor, which on a remote screen (or to a non-technical viewer)
can be hard to follow - it teleports, and there's no click feedback. This overlay makes the
agent's actions legible the way the Perplexity / Claude / OpenAI computer-use demo videos do:
a glowing ring follows the cursor, clicks emit an expanding ripple, and an action label shows
what's happening.

It's a transparent, always-on-top, CLICK-THROUGH window (WS_EX_TRANSPARENT) so it never
intercepts the input the agent is sending. It runs on its own thread with a small command
queue; the main thread just calls ripple()/label()/stop(). The ring position is read straight
from GetCursorPos every frame, so it always sits exactly where the action is really happening.

Windows-only (uses user32). Safe no-op import elsewhere; start() raises if tkinter is absent.
"""

import ctypes
import queue
import threading
import time
from ctypes import wintypes

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


def _cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


class Overlay:
    """Glowing-ring cursor overlay. start() then ripple()/label(); stop() to remove."""

    def __init__(self, color="#33ffd0", ring_px=26, virtual_origin=(0, 0)):
        self.color = color
        self.ring = ring_px
        self.ox, self.oy = virtual_origin       # subtract to map physical -> window coords
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._ripples = []                       # active click ripples: [x, y, t0]
        self._label = ""

    # ---- public API (called from the action thread) ----------------------------------
    def start(self, timeout=5.0):
        if _user32 is None:
            raise RuntimeError("overlay is Windows-only")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self

    def ripple(self, x=None, y=None):
        """Emit a click ripple at (physical x, y) or at the current cursor."""
        if x is None:
            x, y = _cursor_pos()
        self._q.put(("ripple", x, y, time.perf_counter()))

    def label(self, text):
        self._q.put(("label", text))

    def stop(self, wait=True, timeout=2.0):
        self._stop.set()
        if wait and self._thread and self._thread.is_alive():
            self._thread.join(timeout)        # let tk tear down on its OWN thread

    # ---- overlay thread --------------------------------------------------------------
    def _run(self):
        import tkinter as tk
        root = tk.Tk()
        self._root = root
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        # cover the whole virtual desktop
        vx = _user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
        vy = _user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
        vw = _user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        vh = _user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        self.ox, self.oy = vx, vy
        root.geometry(f"{vw}x{vh}+{vx}+{vy}")
        root.config(bg="black")
        cv = tk.Canvas(root, width=vw, height=vh, bg="black", highlightthickness=0)
        cv.pack()
        root.update_idletasks()

        # layered + click-through + not focusable, then key out black so the desktop shows
        # through. SetLayeredWindowAttributes must come AFTER the exstyle change or the color
        # key is cleared and the window renders as opaque black.
        hwnd = _user32.GetParent(cv.winfo_id()) or root.winfo_id()
        ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                               ex | WS_EX_LAYERED | WS_EX_TRANSPARENT
                               | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        LWA_COLORKEY = 0x1
        _user32.SetLayeredWindowAttributes(hwnd, 0x00000000, 0, LWA_COLORKEY)  # black -> transparent

        self._ready.set()

        def frame():
            if self._stop.is_set():
                root.destroy()
                return
            # drain commands
            try:
                while True:
                    msg = self._q.get_nowait()
                    if msg[0] == "ripple":
                        self._ripples.append([msg[1], msg[2], msg[3]])
                    elif msg[0] == "label":
                        self._label = msg[1]
            except queue.Empty:
                pass

            cv.delete("all")
            cx, cy = _cursor_pos()
            x, y = cx - self.ox, cy - self.oy

            # glow ring: a few concentric circles for a soft halo
            for i, w in ((14, 1), (9, 2), (self.ring, 2)):
                cv.create_oval(x - i, y - i, x + i, y + i, outline=self.color, width=w)
            cv.create_oval(x - 3, y - 3, x + 3, y + 3, fill=self.color, outline=self.color)
            # short crosshair so the exact point is unambiguous
            cv.create_line(x - self.ring - 6, y, x - self.ring + 2, y, fill=self.color)
            cv.create_line(x + self.ring - 2, y, x + self.ring + 6, y, fill=self.color)

            # click ripples: expanding, fading
            now = time.perf_counter()
            keep = []
            for rx, ry, t0 in self._ripples:
                age = now - t0
                if age > 0.6:
                    continue
                r = 12 + age * 140
                ex_, ey_ = rx - self.ox, ry - self.oy
                cv.create_oval(ex_ - r, ey_ - r, ex_ + r, ey_ + r,
                               outline="#ffd23f", width=max(1, int(3 * (1 - age / 0.6))))
                keep.append([rx, ry, t0])
            self._ripples = keep

            if self._label:
                cv.create_text(vw // 2, 28, text=self._label, fill=self.color,
                               font=("Segoe UI", 16, "bold"))

            root.after(16, frame)   # ~60 fps

        frame()
        root.mainloop()
