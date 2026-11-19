"""English on-screen text detection + temporal tracking.

Pipeline
1. Decode the video at a reduced "OCR resolution" and run RapidOCR (PP-OCR,
   onnxruntime, CPU) on frames sampled at ~SAMPLE_FPS.
2. Merge OCR line boxes of the same frame into caption *blocks*
   (multi-line titles stay one block).
3. Link blocks across samples into *tracks* (same caption over time) using
   text similarity + box overlap, so moving / scaling captions keep one id.
4. Refine every track's first/last frame to exact frame accuracy by OCR-ing
   the caption area on the frames between samples.

Whether a track is a post-production caption or product / scene text is
decided afterwards (AI vision classification, with a heuristic fallback).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np
from rapidfuzz import fuzz

from .video import VideoInfo, iter_frames

SAMPLE_FPS = 5.0
OCR_MAX_SIDE = 1280
MIN_SCORE = 0.55

_ocr = None


def get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    return _ocr


LATIN = re.compile(r"[A-Za-z]")


def norm_text(t: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", t.upper()).strip()


@dataclass
class Line:
    x: float
    y: float
    w: float
    h: float
    text: str
    score: float
    angle: float
    lh: float = 0.0  # glyph line height (perpendicular to baseline)

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2


@dataclass
class Block:
    lines: list[Line]
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0

    def __post_init__(self):
        xs = [l.x for l in self.lines] + [l.x + l.w for l in self.lines]
        ys = [l.y for l in self.lines] + [l.y + l.h for l in self.lines]
        self.x, self.y = min(xs), min(ys)
        self.w, self.h = max(xs) - self.x, max(ys) - self.y

    @property
    def text(self) -> str:
        return "\n".join(self.rows_text())

    def rows_text(self) -> list[str]:
        rows: list[list[Line]] = []
        for l in sorted(self.lines, key=lambda l: l.cy):
            if rows and abs(rows[-1][0].cy - l.cy) < 0.5 * min(rows[-1][0].h, l.h):
                rows[-1].append(l)
            else:
                rows.append([l])
        return [" ".join(x.text for x in sorted(r, key=lambda l: l.x)) for r in rows]

    @property
    def line_h(self) -> float:
        return float(np.median([l.lh or l.h for l in self.lines]))

    @property
    def score(self) -> float:
        return float(np.mean([l.score for l in self.lines]))

    @property
    def angle(self) -> float:
        return float(np.median([l.angle for l in self.lines]))


@dataclass
class Obs:
    frame: int
    block: Block


@dataclass
class Track:
    id: int
    obs: list[Obs] = field(default_factory=list)
    missed: int = 0
    closed: bool = False
    start_frame: int = 0
    end_frame: int = 0

    @property
    def last(self) -> Obs:
        return self.obs[-1]

    def best_text(self) -> tuple[str, list[str]]:
        c = Counter()
        best_raw = {}
        for o in self.obs:
            n = norm_text(o.block.text)
            c[n] += o.block.score * (1 + 0.02 * len(n))
            if n not in best_raw or o.block.score > best_raw[n][0]:
                best_raw[n] = (o.block.score, o.block)
        top = c.most_common(1)[0][0]
        blk = best_raw[top][1]
        return " ".join(blk.rows_text()), blk.rows_text()


# ---------------------------------------------------------------- OCR utils

def run_ocr(img: np.ndarray, scale_back: float) -> list[Line]:
    res, _ = get_ocr()(img)
    lines: list[Line] = []
    for box, text, score in res or []:
        score = float(score)
        text = text.strip()
        if score < MIN_SCORE or len(LATIN.findall(text)) < 2:
            continue
        pts = np.array(box, dtype=np.float32) * scale_back
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        dx, dy = pts[1] - pts[0]
        angle = math.degrees(math.atan2(dy, dx))
        # box height measured perpendicular to the baseline
        h = float(np.linalg.norm(pts[3] - pts[0]))
        bh = float(max(y1 - y0, 1.0)) if abs(angle) < 3 else max(h, 1.0)
        lines.append(Line(float(x0), float(y0), float(x1 - x0), float(max(y1 - y0, 1.0)), text, score, angle))
        lines[-1].lh = bh
    return lines


def group_blocks(lines: list[Line]) -> list[Block]:
    n = len(lines)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            a, b = lines[i], lines[j]
            if abs(a.angle - b.angle) > 6:
                continue
            hmin, hmax = min(a.h, b.h), max(a.h, b.h)
            if hmax / hmin > 1.8:
                continue
            v_overlap = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
            h_gap = max(a.x, b.x) - min(a.x + a.w, b.x + b.w)
            same_row = v_overlap > 0.5 * hmin and h_gap < 1.2 * hmax
            v_gap = max(a.y, b.y) - min(a.y + a.h, b.y + b.h)
            x_overlap = -h_gap
            stacked = (v_gap < 0.75 * hmin and v_overlap < 0.5 * hmin and
                       (x_overlap > 0.2 * min(a.w, b.w) or abs(a.cx - b.cx) < 0.25 * max(a.w, b.w)))
            if same_row or stacked:
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(lines[i])
    return [Block(g) for g in groups.values()]


def iou(a: Block, b: Block) -> float:
    x0, y0 = max(a.x, b.x), max(a.y, b.y)
    x1, y1 = min(a.x + a.w, b.x + b.w), min(a.y + a.h, b.y + b.h)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    u = a.w * a.h + b.w * b.h - inter
    return inter / u if u > 0 else 0.0


def match_score(t: Track, b: Block, diag: float) -> float:
    a = t.last.block
    na, nb = norm_text(a.text), norm_text(b.text)
    sim = fuzz.ratio(na, nb)
    part = fuzz.partial_ratio(na, nb) if min(len(na), len(nb)) >= 4 else 0
    ov = iou(a, b)
    dist = math.hypot(a.x + a.w / 2 - b.x - b.w / 2, a.y + a.h / 2 - b.y - b.h / 2) / diag
    ok = ((sim >= 75 and (ov > 0.05 or dist < 0.2)) or
          (sim >= 55 and ov > 0.35) or
          (part >= 92 and ov > 0.25))   # word-by-word reveal / partially hidden
    if not ok:
        return -1
    return sim / 100 + ov - dist


# ---------------------------------------------------------------- main entry

def analyze(path: str, info: VideoInfo, progress: Callable[[float, str], None] = lambda p, m: None,
            sample_fps: float = SAMPLE_FPS) -> dict:
    W, H = info.width, info.height
    s = min(1.0, OCR_MAX_SIDE / max(W, H))
    sw, sh = int(round(W * s / 2) * 2), int(round(H * s / 2) * 2)
    scale_back = W / sw
    step = max(1, int(round(info.fps / sample_fps)))
    diag = math.hypot(W, H)

    tracks: list[Track] = []
    sample_jpgs: dict[int, bytes] = {}
    sample_frames: list[int] = []
    total = max(info.frame_count, 1)
    last_frame = 0

    for idx, frame in iter_frames(path, info, (sw, sh)):
        last_frame = idx
        if idx % step:
            continue
        sample_frames.append(idx)
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        sample_jpgs[idx] = jpg.tobytes()
        blocks = group_blocks(run_ocr(frame, scale_back))

        active = [t for t in tracks if not t.closed]
        pairs = []
        for ti, t in enumerate(active):
            for bi, b in enumerate(blocks):
                sc = match_score(t, b, diag)
                if sc > 0:
                    pairs.append((sc, ti, bi))
        pairs.sort(reverse=True)
        used_t, used_b = set(), set()
        for sc, ti, bi in pairs:
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti); used_b.add(bi)
            active[ti].obs.append(Obs(idx, blocks[bi]))
            active[ti].missed = 0
        for ti, t in enumerate(active):
            if ti not in used_t:
                t.missed += 1
                if t.missed > 1:
                    t.closed = True
        for bi, b in enumerate(blocks):
            if bi not in used_b:
                tracks.append(Track(id=len(tracks) + 1, obs=[Obs(idx, b)]))
        progress(0.75 * idx / total, f"文字辨識中 {idx}/{total} 幀")

    frame_count = last_frame + 1
    info.frame_count = frame_count

    # drop flicker: single-observation tracks with weak OCR or tiny boxes
    keep = []
    for t in tracks:
        b = t.obs[0].block
        if len(t.obs) == 1 and (b.score < 0.8 or b.line_h < 0.012 * H):
            continue
        keep.append(t)
    tracks = keep

    # ------------------------------------------------ frame accurate boundaries
    progress(0.76, "校正字幕出現/消失時間")
    need: dict[int, list[tuple[Track, str]]] = defaultdict(list)
    for t in tracks:
        f0, f1 = t.obs[0].frame, t.obs[-1].frame
        t.start_frame, t.end_frame = f0, f1
        for f in range(max(0, f0 - step + 1), f0):
            need[f].append((t, "s"))
        for f in range(f1 + 1, min(frame_count, f1 + step)):
            need[f].append((t, "e"))
    present: dict[tuple[int, int], bool] = {}
    if need:
        for idx, frame in iter_frames(path, info, (sw, sh)):
            if idx not in need:
                continue
            for t, _ in need[idx]:
                present[(t.id, idx)] = _present_in_crop(frame, t, idx, scale_back)
            progress(0.76 + 0.14 * idx / frame_count, "校正字幕出現/消失時間")
    for t in tracks:
        f0, f1 = t.obs[0].frame, t.obs[-1].frame
        s0 = f0
        for f in range(f0 - 1, max(-1, f0 - step), -1):
            if present.get((t.id, f)):
                s0 = f
            else:
                break
        e1 = f1
        for f in range(f1 + 1, min(frame_count, f1 + step)):
            if present.get((t.id, f)):
                e1 = f
            else:
                break
        t.start_frame, t.end_frame = s0, e1

    # ------------------------------------------------ serialise
    out_tracks = []
    for t in sorted(tracks, key=lambda t: (t.start_frame, t.obs[0].block.y)):
        text, rows = t.best_text()
        kfs = [[o.frame, round(o.block.x, 1), round(o.block.y, 1), round(o.block.w, 1),
                round(o.block.h, 1), round(o.block.line_h, 1)] for o in t.obs]
        cxs = [k[1] + k[3] / 2 for k in kfs]
        cys = [k[2] + k[4] / 2 for k in kfs]
        hs = [k[5] for k in kfs]
        motion = float(max(max(cxs) - min(cxs), max(cys) - min(cys)) / max(W, H))
        size_change = float(max(hs) / max(min(hs), 1))
        out_tracks.append({
            "id": 0,
            "text": text,
            "rows": rows,
            "start_frame": int(t.start_frame),
            "end_frame": int(t.end_frame),
            "keyframes": kfs,
            "angle": round(float(np.median([o.block.angle for o in t.obs])), 1),
            "score": round(float(np.mean([o.block.score for o in t.obs])), 3),
            "n_obs": len(t.obs),
            "motion": round(motion, 4),
            "size_change": round(size_change, 3),
        })
    for i, tr in enumerate(out_tracks):
        tr["id"] = i + 1
    progress(0.9, f"偵測到 {len(out_tracks)} 段英文文字")
    return {
        "sample_step": step,
        "ocr_scale": s,
        "frame_count": frame_count,
        "tracks": out_tracks,
        "_sample_jpgs": sample_jpgs,
        "_scale_back": scale_back,
    }


def _rect_at(t: Track, idx: int) -> tuple[float, float, float, float, float]:
    """Box of the track at frame idx, linearly inter/extrapolated from observations."""
    obs = t.obs
    if len(obs) == 1:
        b = obs[0].block
        return b.x, b.y, b.w, b.h, b.line_h
    if idx <= obs[0].frame:
        a, c = obs[0], obs[1]
    elif idx >= obs[-1].frame:
        a, c = obs[-2], obs[-1]
    else:
        k = next(i for i in range(1, len(obs)) if obs[i].frame >= idx)
        a, c = obs[k - 1], obs[k]
    u = (idx - a.frame) / max(1, c.frame - a.frame)
    A, C = a.block, c.block
    lerp = lambda p, q: p + (q - p) * u
    return lerp(A.x, C.x), lerp(A.y, C.y), lerp(A.w, C.w), lerp(A.h, C.h), lerp(A.line_h, C.line_h)


def _present_in_crop(frame: np.ndarray, t: Track, idx: int, scale_back: float) -> bool:
    x, y, w, h, lh = _rect_at(t, idx)
    m = 1.5 * lh
    x0, y0 = (x - m) / scale_back, (y - m) / scale_back
    x1, y1 = (x + w + m) / scale_back, (y + h + m) / scale_back
    hh, ww = frame.shape[:2]
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(ww, int(x1)), min(hh, int(y1))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    crop = cv2.copyMakeBorder(frame[y0:y1, x0:x1], 24, 24, 24, 24, cv2.BORDER_REPLICATE)
    lines = run_ocr(crop, 1.0)
    if not lines:
        return False
    blocks = group_blocks(lines)
    ref = norm_text(t.best_text()[0])
    best = 0
    for b in blocks:
        got = norm_text(" ".join(b.rows_text()))
        sc = fuzz.ratio(got, ref)
        if len(got) >= 0.5 * len(ref):
            sc = max(sc, fuzz.partial_ratio(got, ref) - 5)
        best = max(best, sc)
    return best >= 65
