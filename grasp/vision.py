"""Vision backends for Grasp.

Two backends available — pick with GRASP_VLM_BACKEND env var:

  minimax  (default) — cloud VLM via MiniMax API. Zero Anthropic tokens.
  liquid             — on-device LFM2.5-VL-Extract via HuggingFace transformers.

Both expose the same vlm(prompt, png_bytes) -> str interface so caller code
(see, locate tools) works with either backend unchanged.

Backend toggle:
  GRASP_VLM_BACKEND=minimax|liquid   (default: minimax)

MiniMax key resolution (unchanged):
  1. env MINIMAX_API_KEY
  2. env ANTHROPIC_AUTH_TOKEN
  3. Claude-NIM auth file (default C:\\Users\\vinit\\Claude-NIM\\settings minimax auth.json)
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io").rstrip("/")
VLM_PATH = os.environ.get("MINIMAX_VLM_PATH", "/v1/coding_plan/vlm")
AUTH_FILE = os.environ.get(
    "MINIMAX_AUTH_FILE", r"C:\Users\vinit\Claude-NIM\settings minimax auth.json")

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_RETRY = {429, 500, 502, 503, 529}


class VisionError(RuntimeError):
    """Raised for missing key / transport / API errors so the tool can report cleanly."""


def _key_from_file(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return None
    env = cfg.get("env", cfg) if isinstance(cfg, dict) else {}
    tok = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("MINIMAX_API_KEY")
    return tok.strip() if isinstance(tok, str) and tok.strip() else None


def resolve_key() -> str:
    k = os.environ.get("MINIMAX_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if k and k.strip():
        return k.strip()
    k = _key_from_file(AUTH_FILE)
    if k:
        return k
    raise VisionError(
        "no MiniMax key: set MINIMAX_API_KEY (or ANTHROPIC_AUTH_TOKEN), or add "
        f"ANTHROPIC_AUTH_TOKEN to {AUTH_FILE}")


def is_available() -> bool:
    try:
        resolve_key()
        return True
    except Exception:
        return False


def strip_think(text):
    return _THINK.sub("", text).strip() if isinstance(text, str) else text


def _post(url: str, body: dict, headers: dict, *, timeout: float, tries: int = 4):
    data = json.dumps(body).encode("utf-8")
    delay, last = 2.0, None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in _RETRY and attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 20.0)
                continue
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8")[:200]
            except Exception:
                pass
            raise VisionError(f"MiniMax HTTP {e.code}: {body_txt}")
        except urllib.error.URLError as e:
            last = e
            if attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 20.0)
                continue
            raise VisionError(f"MiniMax connection failed: {e}")
    raise VisionError(f"MiniMax failed: {last}")


def _extract(j) -> str:
    if isinstance(j, dict):
        br = j.get("base_resp")
        if isinstance(br, dict) and br.get("status_code") not in (0, None):
            raise VisionError(
                f"MiniMax vlm error {br.get('status_code')}: {br.get('status_msg')}")
        for k in ("response", "text", "content", "output", "result", "answer"):
            v = j.get(k)
            if isinstance(v, str) and v.strip():
                return v
        try:
            return j["choices"][0]["message"]["content"]
        except Exception:
            pass
        data = j.get("data")
        if isinstance(data, dict):
            for k in ("text", "content", "response"):
                if isinstance(data.get(k), str):
                    return data[k]
    return json.dumps(j, ensure_ascii=False)


def _minimax_vlm(prompt: str, png_bytes: bytes, *, timeout: float = 120.0) -> str:
    """Send a PNG screenshot + prompt to MiniMax's vision service; return its text."""
    uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    headers = {"Authorization": f"Bearer {resolve_key()}",
               "Content-Type": "application/json"}
    j = _post(f"{BASE}{VLM_PATH}", {"prompt": prompt, "image_url": uri},
              headers, timeout=timeout)
    return strip_think(_extract(j))


def _liquid_vlm(prompt: str, png_bytes: bytes, *, timeout: float = None) -> str:
    """Send a PNG screenshot + prompt to Liquid on-device VLM; return its text."""
    from .vision_liquid import vlm as _liquid_vlm_fn
    return _liquid_vlm_fn(png_bytes, prompt)


# ---------------------------------------------------------------------------
# Active backend — selected by GRASP_VLM_BACKEND env var (default: minimax)
# ---------------------------------------------------------------------------
_BACKEND = os.environ.get("GRASP_VLM_BACKEND", "minimax").strip().lower()


def vlm(prompt: str, png_bytes: bytes, *, timeout: float = 120.0) -> str:
    """Send a PNG screenshot + prompt to the active VLM backend; return its text.
    Backend chosen by GRASP_VLM_BACKEND env var (minimax | liquid)."""
    if _BACKEND == "liquid":
        return _liquid_vlm(prompt, png_bytes, timeout=timeout)
    return _minimax_vlm(prompt, png_bytes, timeout=timeout)


def active_backend() -> str:
    """Return the name of the currently active VLM backend."""
    return _BACKEND
