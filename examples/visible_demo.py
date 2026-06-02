"""Watch-along demo: the cursor glides with a glowing ring + click ripples + step labels,
the way the Perplexity / Claude / OpenAI computer-use demo videos look. Run it and watch.

    set PYTHONPATH=..\\        (so `import grasp` resolves)
    python -P visible_demo.py  (-P keeps the script dir off sys.path)

Opens Notepad and types a short meeting note. Safe: closes Notepad at the end.
"""
import base64, os, subprocess, time
from grasp import Computer
from grasp.overlay import Overlay

ov = Overlay(color="#33ffd0").start()
c = Computer(settle=0.35, human=True, move_dur=0.8)
c.on_click = ov.ripple

try:
    ov.label("Grasp - visible computer use (watch the cursor)")
    for x, y in [(180, 160), (1100, 160), (1100, 700), (180, 700), (640, 430)]:
        c.move(x, y); time.sleep(0.25)

    ov.label("Opening Notepad")
    c.open_app("notepad"); time.sleep(2.2)
    try:
        import pygetwindow as gw
        w = [x for x in gw.getWindowsWithTitle("Notepad") if x.visible]
        if w: w[0].maximize(); w[0].activate()
    except Exception:
        pass
    time.sleep(0.8)

    ov.label("Clicking into the document"); c.click(640, 360); time.sleep(0.4)
    ov.label("Typing the meeting note")
    c.type_text("Team sync - 3:00 PM today\r\nAgenda: Q2 deck review\r\n")
    ov.label("Select all (Ctrl+A)"); c.key("ctrl+a"); time.sleep(0.8)
    ov.label("Done"); c.move(640, 430); time.sleep(0.4)

    s = c.screenshot()
    open(os.path.join(os.environ.get("TEMP", "."), "grasp_visible.png"), "wb").write(
        base64.b64decode(s["image_b64"]))
finally:
    ov.stop()
    subprocess.run(["taskkill", "/im", "notepad.exe", "/f"], capture_output=True)
print("DONE")
os._exit(0)   # skip tkinter's noisy cross-thread GC at interpreter shutdown
