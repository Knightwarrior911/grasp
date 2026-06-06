"""Live demo: Grasp MCP + Liquid AI LFM2.5-VL-Extract backend.

Demonstrates 3 capabilities of the Liquid backend:
1. Screenshot capture via Grasp
2. General VLM question-answering (image + question -> text)
3. Structured JSON extraction (image + YAML fields -> JSON)

Note: The Liquid backend runs LFM2.5-VL-Extract locally on your machine.
The 450M model is small — best for focused, simple extraction tasks.

Run:  python demo_liquid.py
"""

import json
import io
import base64
import logging
import os
import sys
import re

logging.basicConfig(level=logging.WARNING)
os.environ.setdefault("GRASP_VLM_BACKEND", "liquid")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grasp.computer import Computer
from grasp.vision_liquid import extract, vlm


def extract_single(png_bytes, field_name, field_desc):
    """Extract one field at a time — how the model is designed to be used."""
    fields = f"""{field_name}: {field_desc}"""
    raw = extract(png_bytes, fields)
    try:
        parsed = json.loads(raw)
        return parsed.get(field_name, raw)
    except Exception:
        return raw


def main():
    pc = Computer()

    # === STEP 1: Screenshot ===
    print("=" * 60)
    print("STEP 1: Screenshot via Grasp")
    print("=" * 60)
    shot = pc.screenshot()
    print(f"  Captured : {shot['width']}x{shot['height']} (model space)")
    png_bytes = base64.b64decode(shot["image_b64"])
    print(f"  PNG size : {len(png_bytes):,} bytes")
    print()

    # === STEP 2: VLM question ===
    print("=" * 60)
    print("STEP 2: Liquid VLM — question answering")
    print("=" * 60)
    print("  Question: What do you see in this screenshot?")
    print("  Model   : LiquidAI/LFM2.5-VL-450M-Extract")
    print("  Note    : 450M model is small — short answers work best")
    print()

    answer = vlm(png_bytes, "What application is visible? One word answer.")
    print(f"  Answer  : {answer}")
    print()

    # === STEP 3: Structured extraction — one field at a time ===
    print("=" * 60)
    print("STEP 3: Liquid Extract — structured extraction (one field at a time)")
    print("=" * 60)
    print("  (The model works best with simple, focused prompts)")
    print()

    single_fields = [
        ("app_name", "the name of the main application window"),
        ("theme", "light or dark"),
        ("content_type", "what content is shown: code, text, web, or other"),
    ]

    results = {}
    for field_name, field_desc in single_fields:
        print(f"  Extracting: {field_name} ({field_desc})")
        result = extract_single(png_bytes, field_name, field_desc)
        results[field_name] = result
        print(f"    -> {result}")
        print()

    print("  Combined JSON:")
    print(f"  {json.dumps(results, indent=4)}")
    print()

    # === STEP 4: Simple color extraction from a known image ===
    print("=" * 60)
    print("STEP 4: Liquid Extract — color detection (controlled test)")
    print("=" * 60)
    print("  Creating a test image with known colors to verify accuracy...")

    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (400, 200), color=(45, 45, 48))  # dark bg
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 199, 200], fill=(0, 120, 215))   # blue left
    draw.rectangle([200, 0, 399, 200], fill=(30, 30, 30))   # dark right
    draw.text((20, 90), "Hello World", fill=(255, 255, 255))
    draw.text((220, 90), "Test", fill=(0, 200, 100))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    test_png = buf.getvalue()
    print(f"  Test image: 400x200, blue left + dark right + text")
    print()

    color_result = extract_single(test_png, "left_panel_color", "the background color of the left half")
    print(f"  left_panel_color  -> {color_result}")

    text_result = extract_single(test_png, "text_visible", "any text visible in the image")
    print(f"  text_visible     -> {text_result}")

    theme_result = extract_single(test_png, "overall_theme", "is this light or dark?")
    print(f"  overall_theme    -> {theme_result}")
    print()

    print("=" * 60)
    print("Demo complete.")
    print()
    print("Summary:")
    print("  - Screenshot capture: works")
    print("  - VLM questions: works (keep questions simple)")
    print("  - Structured extraction: works best with one field at a time")
    print("  - For production use, consider the 1.6B model for better quality")
    print()
    print("Toggle backends:")
    print("  GRASP_VLM_BACKEND=liquid   -> use Liquid (local)")
    print("  GRASP_VLM_BACKEND=minimax  -> use MiniMax (cloud)")
    print("  Default: minimax")
    print("=" * 60)


if __name__ == "__main__":
    main()
