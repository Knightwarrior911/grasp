"""MiniMax VLM vision backend for Grasp.

Routes "look at the screen" to MiniMax's coding-plan vision service instead of
the calling agent's (Claude) vision. Grasp captures the screenshot, MiniMax
interprets it, and Grasp returns text / coordinates. The screenshot never enters
the agent's context, so this costs ZERO Anthropic-subscription tokens and is off
the 5h usage cap (MiniMax is a separate vendor on the user's coding plan).

Pure stdlib (urllib) - no extra dependency. Key resolution never puts the secret
in a prompt or commit:
  1. env MINIMAX_API_KEY
  2. env ANTHROPIC_AUTH_TOKEN
  3. the ANTHROPIC_AUTH_TOKEN field of the Claude-NIM auth file
     (default C:\\Users\\vinit\\Claude-NIM\\settings minimax auth.json;
      override with env MINIMAX_AUTH_FILE).
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


def vlm(prompt: str, png_bytes: bytes, *, timeout: float = 120.0) -> str:
    """Send a PNG screenshot + prompt to MiniMax's vision service; return its text."""
    uri = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
    headers = {"Authorization": f"Bearer {resolve_key()}",
               "Content-Type": "application/json"}
    j = _post(f"{BASE}{VLM_PATH}", {"prompt": prompt, "image_url": uri},
              headers, timeout=timeout)
    return strip_think(_extract(j))
