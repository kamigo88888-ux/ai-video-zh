"""Generate a synthetic English e-commerce short (1080x1920, 30fps, with audio).

Contains:
 * static post-production captions (single & two-line)
 * a caption that moves and scales (dynamic)
 * product/brand text printed ON the product (scene text; must NOT be translated)
"""
import math
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, DUR = 1080, 1920, 30, 10
out = sys.argv[1] if len(sys.argv) > 1 else "test_input.mp4"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def font(sz):
    return ImageFont.truetype(FONT, sz)


CAPS = [
    # (start, end, text, kind)
    (0.0, 2.5, "This gadget changes everything", "top"),
    (2.5, 5.0, "No more tangled\ncables ever again!", "bottom"),
    (5.0, 8.0, "Only $19.99 today", "move"),
    (8.0, 10.0, "Link in bio, grab yours now", "top"),
]

proc = subprocess.Popen([
    "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
    "-i", "-", "-f", "lavfi", "-i", f"sine=frequency=440:duration={DUR}",
    "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out,
], stdin=subprocess.PIPE)

yy = np.linspace(0, 1, H)[:, None]
for i in range(FPS * DUR):
    t = i / FPS
    base = np.zeros((H, W, 3), np.float32)
    base[..., 0] = 40 + 60 * yy + 30 * math.sin(t)
    base[..., 1] = 70 + 40 * (1 - yy)
    base[..., 2] = 110 + 50 * yy
    img = Image.fromarray(base.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    # the "product": a box that slowly drifts, with brand text printed on it
    px = 290 + 40 * math.sin(t * 0.8)
    py = 700 + 30 * math.cos(t * 0.6)
    d.rounded_rectangle([px, py, px + 500, py + 520], 40, fill=(235, 235, 240), outline=(30, 30, 30), width=6)
    d.text((px + 250, py + 180), "ACME", font=font(110), fill=(200, 30, 30), anchor="mm")
    d.text((px + 250, py + 300), "PowerCord Pro", font=font(44), fill=(40, 40, 40), anchor="mm")
    d.text((px + 250, py + 380), "USB-C  65W", font=font(36), fill=(90, 90, 90), anchor="mm")
    for s, e, text, kind in CAPS:
        if not (s <= t < e):
            continue
        if kind == "top":
            pos, sz = (W / 2, 300), 56
        elif kind == "bottom":
            pos, sz = (W / 2, 1450), 70
        else:
            p = (t - s) / (e - s)
            pos, sz = (300 + 480 * p, 1350 - 200 * p), int(56 + 40 * p)
        d.multiline_text(pos, text, font=font(sz), fill=(255, 255, 255), anchor="mm", align="center",
                         stroke_width=max(3, sz // 14), stroke_fill=(0, 0, 0), spacing=12)
    proc.stdin.write(np.asarray(img).tobytes())
proc.stdin.close()
proc.wait()
print("wrote", out)
