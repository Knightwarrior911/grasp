"""Voice, notifications, media/volume/brightness, and monitors for Grasp.

These use native Windows facilities (SAPI TTS, Action Center toasts, the audio
endpoint, media-key virtual keys, the brightness API, and mss for monitors).
Each imports its backend lazily and reports cleanly if a library is missing.
"""

from __future__ import annotations

import base64
import ctypes
import io
import os
import subprocess


# --- text to speech (pyttsx3 / SAPI, offline) ---------------------------------
def speak(text: str, rate: int | None = None) -> dict:
    import pyttsx3
    engine = pyttsx3.init()
    if rate:
        engine.setProperty("rate", int(rate))
    engine.say(text)
    engine.runAndWait()
    try:
        engine.stop()
    except Exception:
        pass
    return {"spoke": text[:160]}


# --- desktop notification (Action Center toast, no extra dependency) ----------
_TOAST_PS = (
    "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,"
    "ContentType=WindowsRuntime]>$null;"
    "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
    "$x=$t.GetElementsByTagName('text');"
    "$x.Item(0).AppendChild($t.CreateTextNode($env:GRASP_TITLE))>$null;"
    "$x.Item(1).AppendChild($t.CreateTextNode($env:GRASP_MSG))>$null;"
    "$id='{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
    "$n=[Windows.UI.Notifications.ToastNotification]::new($t);"
    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show($n);"
)


def notify(title: str, message: str) -> dict:
    env = dict(os.environ, GRASP_TITLE=title, GRASP_MSG=message)
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                           env=env, capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return {"notified": True, "title": title}
        return {"notified": False, "error": (r.stderr or "").strip()[:200]}
    except Exception as e:  # noqa: BLE001
        return {"notified": False, "error": str(e)}


# --- system volume (pycaw) ----------------------------------------------------
def volume(level: float | None = None, mute: bool | None = None) -> dict:
    from pycaw.pycaw import AudioUtilities
    vol = AudioUtilities.GetSpeakers().EndpointVolume  # IAudioEndpointVolume
    if mute is not None:
        vol.SetMute(1 if mute else 0, None)
    if level is not None:
        vol.SetMasterVolumeLevelScalar(max(0.0, min(float(level), 1.0)), None)
    return {"level": round(vol.GetMasterVolumeLevelScalar(), 3),
            "muted": bool(vol.GetMute())}


# --- media keys (no dependency) -----------------------------------------------
_MEDIA_VK = {"play_pause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2,
             "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD}


def media(action: str) -> dict:
    vk = _MEDIA_VK.get(action)
    if vk is None:
        return {"ok": False, "error": f"unknown action '{action}'",
                "choices": sorted(_MEDIA_VK)}
    u32 = ctypes.windll.user32
    u32.keybd_event(vk, 0, 0, 0)      # key down
    u32.keybd_event(vk, 0, 2, 0)      # key up (KEYEVENTF_KEYUP = 2)
    return {"ok": True, "action": action}


# --- screen brightness (screen-brightness-control) ----------------------------
def brightness(level: int | None = None) -> dict:
    import screen_brightness_control as sbc
    if level is not None:
        sbc.set_brightness(int(max(0, min(level, 100))))
    try:
        cur = sbc.get_brightness()
    except Exception:
        cur = None
    return {"brightness": cur}


# --- monitors (mss) -----------------------------------------------------------
def list_monitors() -> dict:
    import mss
    with mss.mss() as sct:
        mons = sct.monitors
    out = [{"index": i, "left": m["left"], "top": m["top"],
            "width": m["width"], "height": m["height"]}
           for i, m in enumerate(mons)]
    return {"count": len(mons) - 1, "monitors": out}  # index 0 is the virtual desktop


def screenshot_monitor(index: int, max_side: int = 1280) -> dict:
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mons = sct.monitors
        if index < 1 or index >= len(mons):
            return {"ok": False, "error": f"bad monitor index (1..{len(mons) - 1})"}
        raw = sct.grab(mons[index])
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    scale = min(1.0, max_side / max(img.width, img.height))
    if scale < 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return {"index": index, "width": img.width, "height": img.height,
            "image_b64": base64.b64encode(buf.getvalue()).decode()}
