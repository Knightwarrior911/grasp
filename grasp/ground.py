"""Local, token-free element grounding for locate().

Tier order (orchestrated in server.locate): UI Automation tree (exact, instant,
free) -> OCR (on-screen text, free, offline) -> MiniMax VLM (off-cap cloud) as a
last resort. UIA and OCR are optional: if their libraries are missing the tier is
skipped and locate() falls through to the next one.

- locate_uia(target) -> (cx, cy) in PHYSICAL screen pixels (map with
  Computer.real_to_model), or None.
- locate_ocr(model_png, target) -> (x, y) already in MODEL space (it reads the
  downscaled model-space screenshot), or None.
"""

from __future__ import annotations

import ctypes
import io

# UIA control types worth preferring when several names match.
_CLICKABLE = {
    "ButtonControl", "MenuItemControl", "HyperlinkControl", "ListItemControl",
    "CheckBoxControl", "RadioButtonControl", "TabItemControl", "SplitButtonControl",
    "TreeItemControl", "ComboBoxControl", "EditControl", "TextControl", "ImageControl",
}


def _foreground_hwnd():
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def locate_uia(target: str):
    """Best control whose Name/Value contains `target` in the foreground window."""
    try:
        import uiautomation as auto
    except Exception:
        return None
    t = target.strip().lower()
    if not t:
        return None
    hwnd = _foreground_hwnd()
    try:
        root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetRootControl()
    except Exception:
        root = None
    if root is None:
        return None

    best = None  # (score, -area, (cx, cy))
    stack = [(root, 0)]
    seen = 0
    while stack and seen < 4000:
        ctrl, depth = stack.pop()
        seen += 1
        try:
            children = ctrl.GetChildren() if depth < 18 else []
        except Exception:
            children = []
        for ch in children:
            stack.append((ch, depth + 1))
        try:
            name = ctrl.Name or ""
            ctype = ctrl.ControlTypeName
        except Exception:
            continue
        value = ""
        try:
            getvp = getattr(ctrl, "GetValuePattern", None)
            if getvp:
                vp = getvp()
                value = (vp.Value or "") if vp else ""
        except Exception:
            value = ""
        hay = f"{name} {value}".lower()
        if t not in hay:
            continue
        try:
            r = ctrl.BoundingRectangle
            w, h = r.right - r.left, r.bottom - r.top
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue
        nl = name.lower()
        score = 0
        if nl == t:
            score += 100
        elif nl.startswith(t):
            score += 50
        if ctype in _CLICKABLE:
            score += 20
        cand = (score, -(w * h), ((r.left + r.right) // 2, (r.top + r.bottom) // 2))
        if best is None or cand[:2] > best[:2]:
            best = cand
    return best[2] if best else None


# --- element control: snapshot the accessibility tree, act on elements by ref -------
_REGISTRY = {}  # ref -> uiautomation Control (valid until the next snapshot)
_INTERESTING = _CLICKABLE | {
    "ComboBoxControl", "RadioButtonControl", "MenuItemControl", "SliderControl",
    "TreeItemControl", "DataItemControl", "HeaderItemControl", "WindowControl",
}


def _value_of(ctrl) -> str:
    try:
        g = getattr(ctrl, "GetValuePattern", None)
        if g:
            vp = g()
            return (vp.Value or "") if vp else ""
    except Exception:
        pass
    return ""


def snapshot_uia(max_nodes: int = 700):
    """Walk the foreground window's UIA tree -> list of interactive elements.

    Returns [{ref, name, role, enabled, value, _rect(physical px)}] and stashes the
    live controls in a registry keyed by ref. None if uiautomation is unavailable.
    """
    try:
        import uiautomation as auto
    except Exception:
        return None
    hwnd = _foreground_hwnd()
    try:
        root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetRootControl()
    except Exception:
        root = None
    if root is None:
        return []
    _REGISTRY.clear()
    items, stack, seen, seq = [], [(root, 0)], 0, 0
    while stack and seen < max_nodes:
        ctrl, depth = stack.pop()
        seen += 1
        try:
            children = ctrl.GetChildren() if depth < 20 else []
        except Exception:
            children = []
        for ch in reversed(children):
            stack.append((ch, depth + 1))
        try:
            name = ctrl.Name or ""
            ctype = ctrl.ControlTypeName
            r = ctrl.BoundingRectangle
        except Exception:
            continue
        if (r.right - r.left) <= 0 or (r.bottom - r.top) <= 0:
            continue
        if ctype not in _INTERESTING and not name.strip():
            continue
        seq += 1
        ref = f"e{seq}"
        _REGISTRY[ref] = ctrl
        items.append({"ref": ref, "name": name[:100],
                      "role": ctype.replace("Control", ""),
                      "enabled": bool(getattr(ctrl, "IsEnabled", True)),
                      "value": _value_of(ctrl)[:100],
                      "_rect": [r.left, r.top, r.right, r.bottom]})
    return items


def registry_get(ref: str):
    return _REGISTRY.get(ref)


def find_ctrl(name: str):
    """First registry control whose name contains `name` (case-insensitive)."""
    t = (name or "").strip().lower()
    if not t:
        return None
    for ctrl in _REGISTRY.values():
        try:
            if t in (ctrl.Name or "").lower():
                return ctrl
        except Exception:
            continue
    return None


def invoke_ctrl(ctrl) -> bool:
    """Fire the most appropriate accessibility action; True if one succeeded."""
    for getter, method in (("GetInvokePattern", "Invoke"),
                           ("GetTogglePattern", "Toggle"),
                           ("GetExpandCollapsePattern", "Expand"),
                           ("GetSelectionItemPattern", "Select")):
        try:
            g = getattr(ctrl, getter, None)
            if g:
                p = g()
                if p:
                    getattr(p, method)()
                    return True
        except Exception:
            continue
    return False


def set_value_ctrl(ctrl, text: str) -> bool:
    try:
        g = getattr(ctrl, "GetValuePattern", None)
        if g:
            p = g()
            if p:
                p.SetValue(text)
                return True
    except Exception:
        pass
    return False


def ctrl_center_real(ctrl):
    try:
        r = ctrl.BoundingRectangle
        return ((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    except Exception:
        return None


def ocr_text(model_png: bytes) -> str:
    """Full OCR text of an image, in rough reading order (top-to-bottom)."""
    try:
        import numpy as np
        from PIL import Image
        engine = _ocr()
    except Exception:
        return ""
    try:
        img = Image.open(io.BytesIO(model_png)).convert("RGB")
        result, _ = engine(np.array(img)[:, :, ::-1])
    except Exception:
        return ""
    if not result:
        return ""
    rows = []
    for box, text, _conf in result:
        ys = [p[1] for p in box]
        rows.append((min(ys), text))
    rows.sort(key=lambda r: r[0])
    return "\n".join(t for _, t in rows)


_ocr_engine = None


def _ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    return _ocr_engine


def locate_ocr(model_png: bytes, target: str):
    """On-screen text matching `target` -> (x, y) in model coords, or None."""
    t = target.strip().lower()
    if not t:
        return None
    try:
        import numpy as np
        from PIL import Image
        engine = _ocr()
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(model_png)).convert("RGB")
        arr = np.array(img)[:, :, ::-1]  # RGB -> BGR for RapidOCR
        result, _ = engine(arr)
    except Exception:
        return None
    if not result:
        return None
    best = None  # (score, (x, y))
    for box, text, _conf in result:
        tl = (text or "").strip().lower()
        if t not in tl:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx = int(sum(xs) / len(xs))
        cy = int(sum(ys) / len(ys))
        score = 100 if tl == t else (50 if tl.startswith(t) else 10)
        if best is None or score > best[0]:
            best = (score, (cx, cy))
    return best[1] if best else None
