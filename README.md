# Grasp

**Exhaustive computer-use for Windows, over MCP. One screenshot-act-verify loop, every action a real agent needs.**

Grasp is a Python MCP server that gives an AI agent hands and eyes on your Windows desktop:
it screenshots the screen, the agent reasons about what it sees, and Grasp executes the
click / type / drag / scroll / keypress / shell command, then screenshots again to verify.

It's a *best-of-both* synthesis of the three public computer-use designs, built to be
**exhaustive rather than partial**:

- **Anthropic `computer_20250124`** — the full action enum (left/right/middle click, double &
  triple click, mouse down/up, click-with-held-keys, drag, scroll, key chords, hold-key,
  cursor position, wait) and the **XGA coordinate discipline**: vision models click far more
  accurately at <= ~1280px, so Grasp works the model in a downscaled coordinate space and
  maps back to physical pixels on execute.
- **OpenAI `computer_use_preview`** — the screenshot -> `computer_call` -> screenshot-output
  loop, **freeform path drag** (`drag_path`), and raw scroll deltas.
- **Perplexity computer** — the **governance verbs**: confirm before destructive actions,
  verify by screenshot, and first-class **app launch + shell** as actions, not afterthoughts.

## Install (let your AI agent do it)

Point Claude Code (or any coding agent) at this repo and say **"set this up"** — it reads
[`AGENTS.md`](AGENTS.md) and installs + registers everything. Or run it yourself:

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
# macOS / Linux (experimental — Grasp targets Windows)
bash scripts/install.sh
```

Then restart Claude Code and ask it to "take a screenshot of my screen with Grasp." Grasp
operates the **real, visible** desktop — you see the cursor move and actions happen live;
there is no headless mode.

## Why a separate tool (and why Windows-native)

Most computer-use stacks are POSIX-first and lean on `pyautogui` directly, which on Windows
silently mis-clicks on any scaled display and can't see secondary monitors. Grasp fixes the
Windows reality up front:

- **DPI awareness is set (per-monitor-v2) via ctypes _before_ any input/screenshot library
  imports** — otherwise every coordinate is wrong on a HiDPI/scaled screen (most modern
  laptops, including the Surface this was built on at 2880x1920).
- **Screenshots via `mss`**, not GDI grab: multi-monitor, DPI-correct, fast. Captured across
  the whole virtual desktop and downscaled into model space.
- **Multi-monitor aware**: coordinates are offset by the virtual-desktop origin, so the agent
  can act on any monitor with one coordinate space.
- **Two input backends**: `pyautogui` (posts window messages, works for normal apps) and
  optional `pydirectinput` (SendInput + scancodes) via `direct=True` for games / RDP /
  fullscreen surfaces that ignore posted events.
- **Big text pastes via the clipboard** instead of 5,000 keystrokes; normal text types at
  ~12ms/char so inputs don't drop characters.

## The coordinate contract

Call `screen_size` first. You get back the real size, the **model space** you must click in,
and the scale factor:

```json
{ "real": [2880, 1920], "model_space": [1280, 853], "scale_x": 0.444, "dpi_mode": "per-monitor-v2", "monitors": 1 }
```

`screenshot` returns a PNG already downscaled to `model_space`. Every coordinate you pass to
`click`, `move`, `drag`, etc. is in that model space; Grasp scales it back to physical pixels.
Aspect ratio is always preserved (4:3 -> XGA 1024x768, 16:10 -> WXGA 1280x800, 16:9 ->
FWXGA 1366x768, anything else -> long side capped at 1280).

## Tools

**Perceive:** `screenshot` (whole screen or a region) · `screen_size` · `cursor_position`

**See without spending agent vision:** `see` (ask a question about the current screen) ·
`locate` (get click coordinates for a described element). The screenshot never enters the
calling agent's context, so it costs no agent vision tokens; the agent just calls
`click(x, y)`.

`locate` resolves a target through three tiers, cheapest first:
1. **Windows UI Automation tree** (`uiautomation`) — exact element rect, instant, free, offline.
2. **OCR** of on-screen text (`rapidocr-onnxruntime`) — free, offline, no Tesseract needed.
3. **MiniMax VLM** — off the Anthropic 5h cap, as a last resort; set `MINIMAX_API_KEY`
   (or the Claude-NIM auth file).

The reply includes `method` (`uia` / `ocr` / `minimax`) so you can see which tier hit.
Each tier is optional and skipped cleanly if its library/key is absent. `see` uses the
MiniMax tier.

**Act on elements, not pixels (free, exact):** `snapshot` (the foreground window's UIA
tree -> elements with stable `ref`, name, role, value, model-space center) · `click_element`
(`ref` or `name`, via the accessibility Invoke action, pixel-click fallback) · `set_value`
(`ref`/`name` + text, via ValuePattern, type fallback) · `read_screen` (OCR the screen or a
region to text). This is the reliable path: target controls by identity, not coordinates.

**Point:** `move` · `click` · `double_click` · `triple_click` · `right_click` · `middle_click`
· `mouse_down` · `mouse_up` · `drag` · `drag_path` (freeform) · `scroll` (direction+amount or raw dx/dy)

**Type:** `type_text` (auto-paste for long text) · `key` (chords & sequences: `ctrl+s`,
`['ctrl+a','delete']`) · `hold_key`

**System:** `open_app` · `run` (powershell/cmd, **destructive commands gated**) ·
`get_active_window` · `list_windows` · `focus_window` · `clipboard_get` · `clipboard_set`

**Find:** `find_text` (OCR -> model-space coordinates, best-effort) · `wait`

Every click/type takes optional held modifier `keys` and a `direct` SendInput flag. Click
coordinates are optional — omit them to act at the current cursor position.

## Watch-along (visible) mode

By default Grasp moves the real cursor instantly - fine for automation, but hard for a human
to follow (and on a remote screen it looks disconnected). `watch_mode(on=true)` switches to
the look of the Perplexity / Claude / OpenAI computer-use demo videos: the cursor **glides
smoothly** between points, a **glowing ring + crosshair** follows it, **click ripples** mark
every click, and `narrate("...")` shows an on-screen label of the current step. It's a
transparent, click-through, always-on-top overlay, so it never intercepts the input Grasp is
sending. Turn it off to go back to fast motion.

```python
from grasp import Computer
from grasp.overlay import Overlay
ov = Overlay().start()
c = Computer(human=True, move_dur=0.8)   # cursor glides; brief hover before each click
c.on_click = ov.ripple                   # ripple on every click
ov.label("Opening the deck")
c.move(640, 400); c.click(300, 220)      # watch the ring glide + ripple
ov.stop()
```

Over MCP it's two tools: `watch_mode(on=true, glide_seconds=0.6)` then `narrate("step")`
before each action. See `examples/visible_demo.py`.

## Safety gates

Grasp is built to be trusted on a real, working machine. Actions that can destroy work or
launch arbitrary code are **refused unless explicitly confirmed**:

- `run(...)` rejects destructive-looking commands (`rm`/`del`/`format`/`shutdown`/`reg delete`/
  `diskpart`/...) unless `confirm=True`.
- `key(...)` refuses `Win+*` chords (Run dialog, power menu) — use `run()` / `open_app()`
  explicitly instead.
- Construct `Computer(allow_destructive=True)` to lift the gates globally for a trusted session.

The agent pattern is **act, then screenshot to verify** before the next step — the
Perplexity-style discipline that keeps a long task from drifting.

## Setup

```bash
pip install -r requirements.txt
```

`pydirectinput-rgx` (the `direct=True` backend) and `pytesseract` (+ a Tesseract install) are
optional — Grasp loads and runs without them, and the affected tools report clearly if called.

## Connect to Claude Code

```bash
claude mcp add grasp -s user -e PYTHONPATH=C:\Users\vinit\grasp -- python -m grasp
```

Equivalent JSON for other MCP clients:

```json
{ "mcpServers": { "grasp": { "command": "python", "args": ["-m", "grasp"],
    "env": { "PYTHONPATH": "C:\\Users\\vinit\\grasp" } } } }
```

## Use it directly (no MCP)

```python
from grasp import Computer
c = Computer()
print(c.screen_size())
shot = c.screenshot()          # base64 PNG in model space
c.click(640, 400)              # model-space coordinate
c.type_text("hello")
c.key("ctrl+s")
c.run("Get-Process | Select -First 3")   # safe; destructive needs confirm=True
```

## Status

v0.1: full action surface, XGA scaling, multi-monitor + DPI-correct capture, dual input
backend, safety gates, OCR fallback. Tested live on Windows 11 (Surface Pro 9, 2880x1920
per-monitor-v2). Roadmap: structured UI-tree perception via UI Automation (so the agent can
target controls by name, not just pixels), an in-loop screenshot diff to auto-detect "did my
action do anything", and a record/replay action log.
