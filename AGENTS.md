# Setup instructions for the coding agent

**You are an AI coding agent (Claude Code / similar). The user wants Grasp installed and
connected so they can have you SEE and CONTROL their Windows desktop over MCP. Do it for
them — run the steps below, verify, then tell them to restart and try it.**

Grasp drives the **real, visible** desktop (moves the actual cursor, types on the real
keyboard, screenshots the real screen). There is no headless mode — the user watches every
action. It needs Python 3.10+ and a few pip deps; the installer does the rest.

## Step 0 — make sure the repo is here

If running inside the cloned `grasp/` folder, skip. Otherwise:

```bash
git clone https://github.com/Knightwarrior911/grasp
cd grasp
```

## Step 1 — run the installer

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**macOS / Linux** (experimental — Grasp is built for Windows):
```bash
bash scripts/install.sh
```

It auto-detects Python, installs deps, runs a smoke test, and registers the `grasp` MCP
server with Claude Code at this folder's absolute path. Prints `GRASP INSTALL OK` or a
clear `FAILED:` line. Idempotent — safe to re-run.

## Step 2 — verify + tell the user

```bash
claude mcp list   # should show 'grasp'
```

Then tell the user, verbatim:

> Grasp is installed. **Restart Claude Code**, then ask me things like "take a screenshot
> of my screen", "open Notepad and type my address", or "click the Save button." You'll see
> the cursor move and actions happen live on your screen.

## If the installer fails, do it manually

From the repo root, same python for every step:

1. `python -m pip install -r requirements.txt`
2. Smoke: `python -c "import grasp, grasp.server; print('ok')"` and `python -m pytest -q tests`
3. Register (use the ABSOLUTE repo path + the SAME python):
   - Windows: `claude mcp add grasp -s user -e PYTHONPATH=C:\path\to\grasp -- python -m grasp`
     **In PowerShell the bare `--` is swallowed** — run that line via `cmd /c "..."`, or use
     a variable: `$sep="--"; claude mcp add grasp -s user -e "PYTHONPATH=$repo" $sep python -m grasp`.
   - mac/linux: `claude mcp add grasp -s user -e PYTHONPATH=/path/to/grasp -- python3 -m grasp`

## Optional extras

- `pip install pydirectinput-rgx` — adds the SendInput backend (`direct=true`) for games /
  RDP / fullscreen apps that ignore normal clicks.
- `pip install pytesseract` + install Tesseract-OCR — enables `find_text` (locate on-screen
  text and get its coordinates). Grasp runs fine without these; the tools report if missing.

## What Grasp is (so you can use it after install)

A computer-use MCP server: it screenshots the screen (downscaled into a model coordinate
space), you reason about what you see, and Grasp executes the click/type/drag/scroll/key/
shell action, then you screenshot again to verify. 26 tools (see `README.md`). Coordinates
you pass are in the screenshot's space; Grasp scales them to physical pixels. Destructive
shell commands and `Win+*` chords are refused unless `confirm=true`.
