"""Chinese subtitle layout + sprite rendering.

For every enabled caption track we:
 * wrap the Chinese text so it fits the frame (auto shrink when too long),
 * render it once as an RGBA "sprite" (white bold + black stroke by default),
 * compute key positions that follow the English block frame by frame
   (position under the English text, scale proportional to English text size).
The same sprite + keyframes drive both the browser preview and the final MP4,
so what you see in the editor is exactly what you get.
"""
from __future__ import annotations

import functools
import glob
import hashlib
import io
import json
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DEFAULT_STYLE = {
    "font": "bold",          # bold | black | regular
    "fill": "#FFFFFF",
    "stroke": "#000000",
    "stroke_ratio": 0.12,    # stroke width as fraction of font size
    "size_scale": 1.0,       # Chinese size relative to English line height
    "gap_ratio": 0.15,       # gap between English and Chinese (fraction of font size)
    "position": "below",     # below | above
    "max_lines": 2,
    "max_width_pct": 92,
    "shadow": True,
    "bg_box": False,
    "bg_color": "#000000",
    "bg_opacity": 0.55,
}

_FONT_FILES = {
    "bold": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"],
    "black": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"],
    "regular": ["/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
}
_FALLBACK = ["/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]


@functools.lru_cache(maxsize=None)
def _font_source(kind: str) -> tuple[str, int]:
    files = _FONT_FILES.get(kind, _FONT_FILES["bold"]) + sorted(glob.glob("/usr/share/fonts/**/NotoSansCJK*Bold*", recursive=True)) + _FALLBACK
    for path in files:
        try:
            for idx in range(10):
                f = ImageFont.truetype(path, 20, index=idx)
                fam, _ = f.getname()
                if "TC" in fam:
                    return path, idx
            ImageFont.truetype(path, 20)
            return path, 0
        except OSError:
            continue
    raise RuntimeError("找不到中文字型，請安裝 fonts-noto-cjk")


@functools.lru_cache(maxsize=256)
def get_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    path, idx = _font_source(kind)
    return ImageFont.truetype(path, max(8, int(size)), index=idx)


NO_LINE_START = set("，。、！？；：」』）》〉,.!?;:)]}…—~～")


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    out = []
    for para in text.replace("\\n", "\n").split("\n"):
        para = para.strip()
        if not para:
            continue
        total = font.getlength(para)
        if total <= max_w:
            out.append(para)
            continue
        n = int(np.ceil(total / max_w))
        target = min(max_w, total / n * 1.05)   # balanced lines
        tokens = _tokens(para)
        line = ""
        for tok in tokens:
            if line and font.getlength(line + tok) > target and tok[0] not in NO_LINE_START:
                out.append(line.rstrip())
                line = tok.lstrip()
            else:
                line += tok
        if line:
            out.append(line.rstrip())
    return out or [""]


def _tokens(s: str) -> list[str]:
    """CJK chars are single tokens; latin words/numbers stay together."""
    toks, buf = [], ""
    for ch in s:
        if ch.isascii() and (ch.isalnum() or ch in "$%.-'&+/#@"):
            buf += ch
        else:
            if buf:
                toks.append(buf)
                buf = ""
            toks.append(ch)
    if buf:
        toks.append(buf)
    return toks


def _hex(c: str, alpha: int = 255):
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


def render_sprite(lines: list[str], font_px: int, style: dict) -> tuple[Image.Image, int]:
    """Return (RGBA image, pad). The text block's top edge is at y=pad."""
    font = get_font(style["font"], font_px)
    stroke = max(1, int(round(font_px * style["stroke_ratio"]))) if style["stroke_ratio"] > 0 else 0
    spacing = int(font_px * 0.18)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    text = "\n".join(lines)
    bbox = tmp.multiline_textbbox((0, 0), text, font=font, spacing=spacing, align="center", stroke_width=stroke)
    bbox = [int(np.floor(bbox[0])), int(np.floor(bbox[1])), int(np.ceil(bbox[2])), int(np.ceil(bbox[3]))]
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = stroke + int(font_px * (0.35 if style.get("bg_box") else 0.12)) + 2
    W, H = int(tw + 2 * pad), int(th + 2 * pad)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    origin = (pad - bbox[0], pad - bbox[1])
    if style.get("bg_box"):
        bg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(bg).rounded_rectangle([0, 0, W - 1, H - 1], radius=int(font_px * 0.3),
                                             fill=_hex(style["bg_color"], int(255 * style["bg_opacity"])))
        img = Image.alpha_composite(img, bg)
    if style.get("shadow"):
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        off = max(1, int(font_px * 0.05))
        ImageDraw.Draw(sh).multiline_text((origin[0] + off, origin[1] + off), text, font=font, fill=(0, 0, 0, 150),
                                          spacing=spacing, align="center", stroke_width=stroke, stroke_fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(max(1, font_px * 0.04)))
        img = Image.alpha_composite(img, sh)
    d = ImageDraw.Draw(img)
    d.multiline_text(origin, text, font=font, fill=_hex(style["fill"]), spacing=spacing, align="center",
                     stroke_width=stroke, stroke_fill=_hex(style["stroke"]))
    return img, pad


def _smooth(vals: list[float], win: int = 3) -> list[float]:
    if len(vals) < 3:
        return vals
    a = np.array(vals, float)
    k = np.ones(win) / win
    padded = np.pad(a, (win // 2, win // 2), mode="edge")
    return list(np.convolve(padded, k, mode="valid"))


def _eng_keyframes(tr: dict) -> tuple[list[list[float]], bool]:
    """English box keyframes [f, x, y, w, h, lh]; static captions are locked to the median box."""
    kfs = [list(k) for k in tr["keyframes"]]
    static = tr["motion"] < 0.012 and tr["size_change"] < 1.15
    if static:
        med = np.median(np.array([k[1:] for k in kfs]), axis=0).tolist()
        return [[tr["start_frame"]] + med, [tr["end_frame"]] + med], True
    cols = list(zip(*kfs))
    sm = [list(cols[0])] + [_smooth(list(c)) for c in cols[1:]]
    kfs = [list(r) for r in zip(*sm)]
    if kfs[0][0] > tr["start_frame"]:
        kfs.insert(0, [tr["start_frame"]] + kfs[0][1:])
    if kfs[-1][0] < tr["end_frame"]:
        kfs.append([tr["end_frame"]] + kfs[-1][1:])
    return kfs, False


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def layout_track(tr: dict, style: dict, W: int, H: int, others: list[dict]) -> Optional[dict]:
    """Compute sprite + per-keyframe placement for one caption track."""
    text = (tr.get("translation") or "").strip()
    if not text or not tr.get("enabled", True):
        return None
    ov = tr.get("override") or {}
    st = {**style, **{k: v for k, v in ov.items() if k in style}}
    eng, static = _eng_keyframes(tr)
    lhs = [k[5] for k in eng]
    base_lh = float(np.median(lhs))
    font_px = base_lh * 0.9 * float(st["size_scale"])
    font_px = float(np.clip(font_px, 0.018 * H, 0.10 * H))
    eng_w = float(np.median([k[3] for k in eng]))
    max_w = min(W * st["max_width_pct"] / 100, max(eng_w * 1.1, W * 0.62)) - 2 * font_px * st["stroke_ratio"]
    min_px = 0.022 * H * min(1.0, float(st["size_scale"]))
    while True:
        lines = wrap_text(text, get_font(st["font"], int(font_px)), max_w)
        explicit = text.replace("\\n", "\n").count("\n") + 1
        if len(lines) <= max(int(st["max_lines"]), explicit) or font_px * 0.92 < min_px:
            break
        font_px *= 0.92
    font_px = int(round(font_px))
    sprite, pad = render_sprite(lines, font_px, st)
    sw, sh = sprite.size
    text_h = sh - 2 * pad
    gap = st["gap_ratio"] * font_px
    dx = float(ov.get("dx", 0)) / 100 * W
    dy = float(ov.get("dy", 0)) / 100 * H

    # English boxes of other captions visible at the same time (collision check)
    other_boxes = []
    for o in others:
        if o["id"] == tr["id"] or o["end_frame"] < tr["start_frame"] or o["start_frame"] > tr["end_frame"]:
            continue
        for k in o["keyframes"]:
            other_boxes.append((k[1], k[2], k[1] + k[3], k[2] + k[4]))

    def place(pos):
        res, bad = [], 0
        for f, x, y, w, h, lh in eng:
            s = (lh / base_lh) if not static else 1.0
            cx = x + w / 2 + dx
            if pos == "below":
                top = y + h + gap * s + dy
            else:
                top = y - gap * s - text_h * s + dy
            sx = sw * s
            left = min(max(cx - sx / 2, 0.01 * W), W - 0.01 * W - sx) if sx < W else (W - sx) / 2
            ty = top - pad * s
            box = (left, top, left + sx, top + text_h * s)
            if top < 0.005 * H or top + text_h * s > 0.99 * H:
                bad += 2
            if any(_overlap(box, b) for b in other_boxes):
                bad += 1
            ty = min(max(ty, -pad * s), H - sh * s + pad * s)
            res.append([int(f), round(float(left), 2), round(float(ty), 2), round(float(s), 4)])
        return res, bad

    first = st["position"] if st["position"] in ("below", "above") else "below"
    second = "above" if first == "below" else "below"
    kf, bad = place(first)
    if bad:
        kf2, bad2 = place(second)
        if bad2 < bad:
            kf, first = kf2, second
    return {
        "id": tr["id"],
        "start_frame": tr["start_frame"],
        "end_frame": tr["end_frame"],
        "lines": lines,
        "font_px": font_px,
        "sprite_w": sw,
        "sprite_h": sh,
        "position": first,
        "keyframes": kf,
        "static": static,
        "_sprite": sprite,
    }


def build_layout(tracks: list[dict], style: dict, W: int, H: int) -> list[dict]:
    style = {**DEFAULT_STYLE, **(style or {})}
    enabled = [t for t in tracks if t.get("enabled")]
    out = []
    for tr in enabled:
        lay = layout_track(tr, style, W, H, enabled)
        if lay:
            h = hashlib.md5(json.dumps([lay["lines"], lay["font_px"], style, tr.get("override")], sort_keys=True,
                                       ensure_ascii=False).encode()).hexdigest()[:10]
            lay["sprite_hash"] = h
            out.append(lay)
    return out


def sprite_png(lay: dict) -> bytes:
    buf = io.BytesIO()
    lay["_sprite"].save(buf, "PNG")
    return buf.getvalue()


def pos_at(lay: dict, f: int) -> Optional[tuple[float, float, float]]:
    if f < lay["start_frame"] or f > lay["end_frame"]:
        return None
    kf = lay["keyframes"]
    if f <= kf[0][0]:
        return tuple(kf[0][1:])
    if f >= kf[-1][0]:
        return tuple(kf[-1][1:])
    for a, b in zip(kf, kf[1:]):
        if a[0] <= f <= b[0]:
            u = (f - a[0]) / max(1, b[0] - a[0])
            return tuple(a[i] + (b[i] - a[i]) * u for i in (1, 2, 3))
    return tuple(kf[-1][1:])
