"""Visible-cursor overlay for demos and watch-along sessions.

The agent drives the real OS cursor, which to a viewer (or on a remote screen) can be hard to
follow: it teleports and gives no click feedback. This overlay makes the agent's actions
legible the way the Perplexity / Claude / OpenAI computer-use demo videos are: a crisp pointer
with a soft accent halo follows the cursor, clicks emit a ripple, and a label narrates the step.

It renders with PIL at 4x supersample and presents through a per-pixel-alpha **layered window**
(UpdateLayeredWindow), so edges are smooth, not the aliased shapes a tk canvas produces. The
window is click-through (WS_EX_TRANSPARENT) and never activates, so it can't intercept the
input the agent is sending.

Design (product register, restrained + one accent):
- pointer: near-white arrow + slate outline + soft drop shadow -> reads on any background.
- halo: calm azure radial glow with a gentle pulse (not neon; not decorative blur).
- ripple: azure ring, expands and fades with an ease-out curve.
- label: slate translucent pill, antialiased text, pinned top-centre.

Windows-only (user32/gdi32). start() raises if Pillow is unavailable.
"""

import ctypes
import math
import queue
import threading
import time
from ctypes import wintypes

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
except Exception:                       # pragma: no cover
    Image = None

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_gdi32 = ctypes.windll.gdi32 if hasattr(ctypes, "windll") else None
_kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

# --- palette (sRGB, from the OKLCH choices above) --------------------------------------
POINTER_FILL = (247, 248, 250)
POINTER_EDGE = (38, 42, 52)
ACCENT = (96, 150, 246)                 # azure  oklch(0.70 0.14 250)
ACCENT_BRIGHT = (150, 188, 255)
LABEL_BG = (22, 25, 32)
LABEL_FG = (236, 240, 248)

SS = 4                                  # supersample factor for antialiasing
SPRITE = 300                            # cursor sprite window size (px)
HOT = SPRITE // 2                       # hotspot (arrow tip) at sprite centre
RIPPLE_MS = 520


# --- Win32 plumbing --------------------------------------------------------------------
if _user32:
    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _user32.DefWindowProcW.restype = ctypes.c_ssize_t
    _user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]

    class WNDCLASS(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROCTYPE),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _user32.GetDC.restype = wintypes.HDC
    _user32.GetDC.argtypes = [wintypes.HWND]
    _gdi32.CreateCompatibleDC.restype = wintypes.HDC
    _gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    _gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    _gdi32.SelectObject.restype = wintypes.HGDIOBJ
    _gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
        ctypes.POINTER(wintypes.POINT), ctypes.POINTER(ctypes.c_long), wintypes.HDC,
        ctypes.POINTER(wintypes.POINT), wintypes.COLORREF, ctypes.POINTER(BLENDFUNCTION),
        wintypes.DWORD]


def _cursor_pos():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# --- sprite rendering (PIL, supersampled) ----------------------------------------------
def _arrow_sprite():
    """Antialiased white arrow with slate outline + soft drop shadow, tip at (HOT, HOT)."""
    s = SS
    big = Image.new("RGBA", (SPRITE * s, SPRITE * s), (0, 0, 0, 0))
    # classic pointer polygon, tip at origin, scaled up
    scale = 2.1
    pts = [(0, 0), (0, 18.5), (4.3, 14.3), (7.0, 20.6), (9.9, 19.4),
           (7.1, 13.2), (12.8, 13.2)]
    tip = (HOT * s, HOT * s)
    poly = [(tip[0] + x * scale * s, tip[1] + y * scale * s) for x, y in pts]

    # drop shadow: filled silhouette, blurred, offset, low alpha
    sh = Image.new("RGBA", big.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon(poly, fill=(8, 10, 16, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(2.4 * s))
    big = ImageChops.offset(sh, int(1.6 * s), int(2.4 * s))

    d = ImageDraw.Draw(big)
    d.polygon(poly, fill=POINTER_FILL + (255,), outline=POINTER_EDGE + (255,),
              width=max(1, int(1.4 * s)))
    return big.resize((SPRITE, SPRITE), Image.LANCZOS)


def _halo_sprite():
    """Soft azure radial glow centred at (HOT, HOT)."""
    s = SS
    big = Image.new("RGBA", (SPRITE * s, SPRITE * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    rmax = 58 * s
    cx = cy = HOT * s
    steps = 60
    for i in range(steps, 0, -1):
        r = rmax * i / steps
        a = int(70 * (1 - i / steps) ** 1.7)            # brighter toward centre
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT + (a,))
    big = big.filter(ImageFilter.GaussianBlur(6 * s))
    return big.resize((SPRITE, SPRITE), Image.LANCZOS)


def _ring_unit():
    """A unit soft ring (azure), resized per-frame for ripples. Diameter = full sprite."""
    s = 3
    n = SPRITE * s
    big = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    c = n / 2
    r = n / 2 - 6 * s
    d.ellipse([c - r, c - r, c + r, c + r], outline=ACCENT_BRIGHT + (255,), width=5 * s)
    big = big.filter(ImageFilter.GaussianBlur(1.5 * s))
    return big.resize((SPRITE, SPRITE), Image.LANCZOS)


def _premult_bgra(img):
    """RGBA -> premultiplied BGRA bytes (what UpdateLayeredWindow's AC_SRC_ALPHA expects)."""
    r, g, b, a = img.split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes("raw", "RGBA")


class _Layered:
    """A click-through, top-most, per-pixel-alpha window driven by PIL frames."""

    def __init__(self, w, h, wndproc, hinst, cls):
        WS_EX = (0x00080000 | 0x00000020 | 0x00000080 | 0x08000000 | 0x00000008)  # LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE|TOPMOST
        self.w, self.h = w, h
        self.hwnd = _user32.CreateWindowExW(WS_EX, cls, "", 0x80000000,  # WS_POPUP
                                            0, 0, w, h, None, None, hinst, None)
        _user32.ShowWindow(self.hwnd, 8)             # SW_SHOWNOACTIVATE

    def update(self, img, x, y):
        screen = _user32.GetDC(None)
        memdc = _gdi32.CreateCompatibleDC(screen)
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = self.w
        bmi.biHeight = -self.h                        # top-down
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0                         # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = _gdi32.CreateDIBSection(screen, ctypes.byref(bmi), 0,
                                       ctypes.byref(bits), None, 0)
        data = _premult_bgra(img)
        ctypes.memmove(bits, data, len(data))
        old = _gdi32.SelectObject(memdc, hbmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)           # AC_SRC_OVER, AC_SRC_ALPHA=1
        ptdst = wintypes.POINT(int(x), int(y))
        ptsrc = wintypes.POINT(0, 0)
        size = (ctypes.c_long * 2)(self.w, self.h)
        _user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(ptdst),
                                    ctypes.cast(size, ctypes.POINTER(ctypes.c_long)),
                                    memdc, ctypes.byref(ptsrc), 0, ctypes.byref(blend), 2)
        _gdi32.SelectObject(memdc, old)
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(memdc)
        _user32.ReleaseDC(None, screen)

    def destroy(self):
        if self.hwnd:
            _user32.DestroyWindow(self.hwnd)
            self.hwnd = None


class Overlay:
    """Crisp visible-cursor overlay. start() then ripple()/label(); stop() to remove.

    `color` kept for API compatibility; the design palette above drives the look."""

    def __init__(self, color=None, ring_px=None, virtual_origin=(0, 0)):
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None
        self._ripples = []
        self._label = ""

    # ---- public API (action thread) --------------------------------------------------
    def start(self, timeout=5.0):
        if _user32 is None or Image is None:
            raise RuntimeError("overlay needs Windows + Pillow")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self

    def ripple(self, x=None, y=None):
        if x is None:
            x, y = _cursor_pos()
        self._q.put(("ripple", x, y, time.perf_counter()))

    def label(self, text):
        self._q.put(("label", text or ""))

    def stop(self, wait=True, timeout=2.0):
        self._stop.set()
        if wait and self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    # ---- overlay thread --------------------------------------------------------------
    def _run(self):
        hinst = _kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROCTYPE(_user32.DefWindowProcW)   # keep ref alive
        cls = WNDCLASS()
        cls.lpfnWndProc = self._wndproc
        cls.hInstance = hinst
        cls.lpszClassName = "GraspOverlayWnd"
        _user32.RegisterClassW(ctypes.byref(cls))

        pointer = _arrow_sprite()
        halo = _halo_sprite()
        ring = _ring_unit()

        cursor_win = _Layered(SPRITE, SPRITE, self._wndproc, hinst, "GraspOverlayWnd")
        LW, LH = 900, 64
        label_win = _Layered(LW, LH, self._wndproc, hinst, "GraspOverlayWnd")
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
        except Exception:
            font = ImageFont.load_default()

        self._ready.set()
        msg = wintypes.MSG()
        last_label = None
        t0 = time.perf_counter()

        while not self._stop.is_set():
            # drain commands
            try:
                while True:
                    m = self._q.get_nowait()
                    if m[0] == "ripple":
                        self._ripples.append([m[1], m[2], m[3]])
                    elif m[0] == "label":
                        self._label = m[1]
            except queue.Empty:
                pass

            now = time.perf_counter()
            cx, cy = _cursor_pos()

            frame = Image.new("RGBA", (SPRITE, SPRITE), (0, 0, 0, 0))
            # halo with a gentle pulse (calm, eased)
            pulse = 0.82 + 0.18 * (0.5 + 0.5 * math.sin((now - t0) * 3.6))
            h = halo.copy()
            h.putalpha(h.split()[3].point(lambda a: int(a * pulse)))
            frame = Image.alpha_composite(frame, h)
            # ripples (expand + fade, ease-out)
            keep = []
            for rx, ry, rt in self._ripples:
                age = (now - rt) * 1000
                if age > RIPPLE_MS:
                    continue
                p = age / RIPPLE_MS
                eased = 1 - (1 - p) ** 3                     # ease-out-cubic
                diam = int(24 + eased * 168)
                alpha = int(235 * (1 - p) ** 1.6)
                rs = ring.resize((diam, diam), Image.LANCZOS)
                rs.putalpha(rs.split()[3].point(lambda a: int(a * alpha / 255)))
                frame.alpha_composite(rs, (HOT - diam // 2, HOT - diam // 2))
                keep.append([rx, ry, rt])
            self._ripples = keep
            # pointer on top
            frame = Image.alpha_composite(frame, pointer)
            cursor_win.update(frame, cx - HOT, cy - HOT)

            # label pill (only re-render when text changes)
            if self._label != last_label:
                last_label = self._label
                lab = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
                if self._label:
                    d = ImageDraw.Draw(lab)
                    tb = d.textbbox((0, 0), self._label, font=font)
                    tw, th = tb[2] - tb[0], tb[3] - tb[1]
                    pad = 18
                    pw, ph = tw + pad * 2, th + pad
                    x0 = (LW - pw) // 2
                    y0 = (LH - ph) // 2
                    d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=ph // 2,
                                        fill=LABEL_BG + (225,))
                    d.text((x0 + pad, y0 + (ph - th) // 2 - tb[1]), self._label,
                           font=font, fill=LABEL_FG + (255,))
                vx = _user32.GetSystemMetrics(76)
                vw = _user32.GetSystemMetrics(78)
                label_win.update(lab, vx + (vw - LW) // 2, 26)

            # pump messages so the windows stay healthy
            while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

            time.sleep(0.016)

        cursor_win.destroy()
        label_win.destroy()
