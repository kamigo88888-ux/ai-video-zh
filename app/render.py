"""Burn the Chinese sprites into the video frame-accurately and encode a high quality MP4.

Video: decoded with the same CFR pipeline as analysis, composited in numpy,
encoded with libx264 (CRF, same resolution and fps).  Audio: original stream
copied untouched when MP4-compatible, otherwise re-encoded to 320k AAC.
"""
from __future__ import annotations

import subprocess
from typing import Callable

import cv2
import numpy as np

from .layout import pos_at
from .video import VideoInfo, iter_frames

MP4_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3"}


class SpriteCache:
    def __init__(self, sprite_rgba: np.ndarray):
        self.base = sprite_rgba  # premultiplied float later
        self.cache: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, s: float):
        key = int(round(s * 200))  # 0.5% scale steps
        if key not in self.cache:
            if len(self.cache) > 400:
                self.cache.clear()
            img = self.base
            if key != 200:
                sc = key / 200
                h, w = img.shape[:2]
                nw, nh = max(1, int(round(w * sc))), max(1, int(round(h * sc)))
                img = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_CUBIC)
            a = img[..., 3:4].astype(np.float32) / 255.0
            rgb = img[..., :3].astype(np.float32) * a          # premultiplied
            self.cache[key] = (rgb, a)
        return self.cache[key]


def composite(frame: np.ndarray, rgb: np.ndarray, a: np.ndarray, x: float, y: float):
    H, W = frame.shape[:2]
    h, w = a.shape[:2]
    x0, y0 = int(round(x)), int(round(y))
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sx0, sy0 = fx0 - x0, fy0 - y0
    region = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    aa = a[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0]
    cc = rgb[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0]
    frame[fy0:fy1, fx0:fx1] = np.clip(cc + region * (1 - aa) + 0.5, 0, 255).astype(np.uint8)


def render(src: str, dst: str, info: VideoInfo, layouts: list[dict], crf: int = 16, preset: str = "medium",
           progress: Callable[[float, str], None] = lambda p, m: None):
    W, H = info.width, info.height
    sprites = {}
    for lay in layouts:
        rgba = np.array(lay["_sprite"].convert("RGBA"))
        bgra = rgba[..., [2, 1, 0, 3]].copy()
        sprites[lay["id"]] = SpriteCache(bgra)

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", f"{info.fps_num}/{info.fps_den}", "-i", "-",
           "-i", src, "-map", "0:v:0"]
    if info.has_audio:
        cmd += ["-map", "1:a:0?"]
        cmd += ["-c:a", "copy"] if info.audio_codec in MP4_AUDIO else ["-c:a", "aac", "-b:a", "320k"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-movflags", "+faststart"]
    for opt, val in (("-color_primaries", info.color_primaries), ("-color_trc", info.color_transfer),
                     ("-colorspace", info.color_space)):
        if val and val != "unknown" and "smpte2084" not in val and "arib" not in val:
            cmd += [opt, val]
    cmd += [dst]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    total = max(1, info.frame_count)
    try:
        for idx, frame in iter_frames(src, info):
            frame = frame.copy()
            for lay in layouts:
                p = pos_at(lay, idx)
                if p is None:
                    continue
                x, y, s = p
                rgb, a = sprites[lay["id"]].get(s)
                composite(frame, rgb, a, x, y)
            proc.stdin.write(frame.tobytes())
            if idx % 10 == 0:
                progress(min(0.99, idx / total), f"輸出影片 {idx}/{total} 幀")
        proc.stdin.close()
    except BrokenPipeError:
        pass
    err = proc.stderr.read().decode(errors="ignore")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg 編碼失敗：{err[-800:]}")
    progress(1.0, "完成")
