#!/usr/bin/env python3
"""Generate AIDE icon using DALL-E 3, then package as .icns."""
import os, sys, subprocess, shutil
from pathlib import Path
from urllib.request import urlretrieve

# ── 1. Generate with DALL-E 3 ─────────────────────────────────────────────────
try:
    import openai
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "openai"], check=True)
    import openai

client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY") or
    open(Path.home()/".aide"/"config.json").read() and
    __import__("json").loads(open(Path.home()/".aide"/"config.json").read())
    .get("env_overrides", {}).get("OPENAI_API_KEY", ""))

PROMPT = """
A macOS app icon for 'AIDE', an AI developer terminal app.
Style: flat, minimal, dark-themed macOS Big Sur icon.
Design: A sleek terminal window with a dark navy-black (#0d1117) background,
a thin title bar with three macOS traffic-light dots (red/yellow/green),
and a glowing bright-blue chevron prompt symbol '>' with a blinking cursor block
in the center. The glow and accent color is vivid electric blue (#58a6ff).
Clean, modern, professional. No text. Rounded square shape.
High-contrast, crisp edges, suitable for app icon use at all sizes.
"""

print("Generating icon with DALL-E 3…")
response = client.images.generate(
    model="dall-e-3",
    prompt=PROMPT.strip(),
    size="1024x1024",
    quality="hd",
    n=1,
)
url = response.data[0].url
print(f"Image URL: {url[:80]}…")

# ── 2. Download the PNG ───────────────────────────────────────────────────────
tmp_png = Path("/tmp/aide_icon_src.png")
urlretrieve(url, tmp_png)
print(f"Downloaded to {tmp_png}")

# ── 3. Build iconset at all required sizes ────────────────────────────────────
try:
    from PIL import Image
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "Pillow"], check=True)
    from PIL import Image

src = Image.open(tmp_png).convert("RGBA")

OUT = Path(__file__).parent / "AIDE.iconset"
OUT.mkdir(exist_ok=True)

SIZES = [
    (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
]

for px, name in SIZES:
    resized = src.resize((px, px), Image.LANCZOS)
    resized.save(OUT / name)
    print(f"  {name}")

# ── 4. Convert to .icns ───────────────────────────────────────────────────────
icns = Path(__file__).parent / "AIDE.app" / "Contents" / "Resources" / "AIDE.icns"
subprocess.run(["iconutil", "-c", "icns", str(OUT), "-o", str(icns)], check=True)
print(f"\nCreated {icns}")

# Save source PNG for reference
src.save(Path(__file__).parent / "icon_source.png")
print("Source PNG saved as icon_source.png")

shutil.rmtree(OUT)
print("Done.")
