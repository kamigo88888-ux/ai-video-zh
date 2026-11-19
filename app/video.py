"""Video probing and frame decoding helpers (ffmpeg based).

All analysis and rendering use the SAME decode pipeline (constant frame rate,
auto-rotation applied) so frame indices always line up between the OCR pass,
the browser preview and the final render.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from fractions import Fraction
from typing import Iterator, Optional

import numpy as np


@dataclass
class VideoInfo:
    width: int            # display width (after rotation)
    height: int           # display height (after rotation)
    fps_num: int
    fps_den: int
    duration: float
    frame_count: int
    has_audio: bool
    audio_codec: Optional[str]
    video_codec: str
    bit_rate: Optional[int]
    pix_fmt: str
    color_primaries: Optional[str] = None
    color_transfer: Optional[str] = None
    color_space: Optional[str] = None

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den

    def to_dict(self):
        d = asdict(self)
        d["fps"] = self.fps
        return d


def _rational(s: str) -> Fraction:
    try:
        n, d = s.split("/")
        if int(d) == 0:
            return Fraction(0)
        return Fraction(int(n), int(d))
    except Exception:
        return Fraction(0)


def probe(path: str) -> VideoInfo:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    vs = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if vs is None:
        raise ValueError("檔案中找不到影片串流")
    aus = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)

    w, h = int(vs["width"]), int(vs["height"])
    rotation = 0
    for sd in vs.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    if "tags" in vs and "rotate" in vs["tags"]:
        rotation = int(vs["tags"]["rotate"])
    if abs(rotation) % 180 == 90:
        w, h = h, w

    r = _rational(vs.get("r_frame_rate", "0/1"))
    a = _rational(vs.get("avg_frame_rate", "0/1"))
    fps = a if a > 0 else r
    if r > 0 and a > 0 and abs(float(r) - float(a)) / float(r) < 0.01:
        fps = r  # CFR: use exact nominal rate
    if fps <= 0 or fps > 240:
        fps = Fraction(30)
    fps = fps.limit_denominator(1001)

    duration = float(vs.get("duration") or data["format"].get("duration") or 0)
    frame_count = int(round(duration * float(fps)))
    br = vs.get("bit_rate") or data["format"].get("bit_rate")
    return VideoInfo(
        width=w, height=h, fps_num=fps.numerator, fps_den=fps.denominator,
        duration=duration, frame_count=max(frame_count, 1),
        has_audio=aus is not None, audio_codec=aus["codec_name"] if aus else None,
        video_codec=vs.get("codec_name", ""), bit_rate=int(br) if br else None,
        pix_fmt=vs.get("pix_fmt", ""),
        color_primaries=vs.get("color_primaries"), color_transfer=vs.get("color_transfer"),
        color_space=vs.get("color_space"),
    )


def decode_args(path: str, info: VideoInfo, scale: Optional[tuple[int, int]] = None) -> list[str]:
    vf = []
    if scale:
        vf.append(f"scale={scale[0]}:{scale[1]}:flags=area")
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
           "-fps_mode", "cfr", "-r", f"{info.fps_num}/{info.fps_den}"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    return cmd


def iter_frames(path: str, info: VideoInfo, scale: Optional[tuple[int, int]] = None) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (frame_index, BGR frame). Frames are produced at constant fps."""
    w, h = scale if scale else (info.width, info.height)
    size = w * h * 3
    proc = subprocess.Popen(decode_args(path, info, scale), stdout=subprocess.PIPE, bufsize=size * 2)
    idx = 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield idx, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            idx += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def extract_frame_jpeg(path: str, info: VideoInfo, frame_idx: int, out_path: str, max_side: int = 1280):
    t = frame_idx / info.fps
    scale = f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
                    "-vf", scale, "-q:v", "3", out_path], check=True)
