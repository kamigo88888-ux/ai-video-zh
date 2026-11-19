"""Job persistence + background worker queue (analysis, AI translation, rendering)."""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

from . import ai, settings
from .detect import analyze
from .layout import DEFAULT_STYLE, build_layout
from .render import render
from .video import VideoInfo, probe, extract_frame_jpeg

_lock = threading.RLock()
_jobs: dict[str, dict] = {}
_queue: deque[tuple[str, str, dict]] = deque()
_cv = threading.Condition(_lock)
_layout_cache: dict[str, tuple[int, list]] = {}


def job_dir(jid: str) -> Path:
    return settings.JOBS / jid


def _json_default(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"not serializable: {type(o)}")


def _save(job: dict):
    d = job_dir(job["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, default=_json_default))
    tmp.replace(d / "job.json")


def load_all():
    for p in sorted(settings.JOBS.glob("*/job.json")):
        try:
            job = json.loads(p.read_text())
        except Exception:
            continue
        # interrupted work -> requeue
        if job["status"] in ("queued", "analyzing", "translating"):
            job["status"] = "queued"
            _queue.append((job["id"], "analyze", {}))
        elif job["status"] == "rendering":
            job["status"] = "ready"
        _jobs[job["id"]] = job


def info_of(job) -> VideoInfo:
    i = dict(job["info"])
    i.pop("fps", None)
    return VideoInfo(**i)


def get(jid: str) -> Optional[dict]:
    return _jobs.get(jid)


def list_jobs() -> list[dict]:
    with _lock:
        out = []
        for j in sorted(_jobs.values(), key=lambda j: j["created"], reverse=True):
            out.append({k: j.get(k) for k in ("id", "name", "created", "status", "progress", "message", "error",
                                               "has_output", "auto_render", "ai_used")} |
                       {"n_captions": sum(1 for t in j.get("tracks", []) if t.get("enabled")),
                        "info": j.get("info")})
        return out


def create(src_path: Path, name: str, tone: str, auto_render: bool) -> dict:
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = job_dir(jid)
    d.mkdir(parents=True)
    ext = Path(name).suffix.lower() or ".mp4"
    dst = d / f"input{ext}"
    shutil.move(str(src_path), dst)
    job = {"id": jid, "name": name, "created": time.time(), "status": "queued", "progress": 0.0,
           "message": "排隊中", "error": None, "input": dst.name, "tone": tone, "auto_render": auto_render,
           "tracks": [], "style": dict(DEFAULT_STYLE), "has_output": False, "version": 0, "ai_used": False,
           "info": None}
    try:
        info = probe(str(dst))
        job["info"] = info.to_dict()
        extract_frame_jpeg(str(dst), info, 0, str(d / "thumb.jpg"), 360)
    except Exception as e:
        job["status"], job["error"] = "error", f"無法讀取影片：{e}"
    with _lock:
        _jobs[jid] = job
        _save(job)
        if job["status"] == "queued":
            enqueue(jid, "analyze")
    return job


def enqueue(jid: str, task: str, args: Optional[dict] = None):
    with _cv:
        _queue.append((jid, task, args or {}))
        _cv.notify()


def update(jid: str, **kw):
    with _lock:
        job = _jobs[jid]
        job.update(kw)
        _save(job)


def _progress(jid: str, base: float, span: float):
    last = [0.0]

    def cb(p, msg):
        now = time.time()
        if now - last[0] > 0.4 or p >= 1:
            last[0] = now
            update(jid, progress=round(base + span * p, 3), message=msg)
    return cb


def delete(jid: str):
    with _lock:
        _jobs.pop(jid, None)
        _layout_cache.pop(jid, None)
    shutil.rmtree(job_dir(jid), ignore_errors=True)


# ------------------------------------------------------------------ editing

def apply_edits(jid: str, data: dict) -> dict:
    with _lock:
        job = _jobs[jid]
        if "style" in data and isinstance(data["style"], dict):
            job["style"] = {**DEFAULT_STYLE, **job.get("style", {}),
                            **{k: v for k, v in data["style"].items() if k in DEFAULT_STYLE}}
        byid = {t["id"]: t for t in job["tracks"]}
        for e in data.get("tracks", []):
            t = byid.get(int(e["id"]))
            if not t:
                continue
            for k in ("enabled", "translation", "override"):
                if k in e:
                    t[k] = e[k]
        job["version"] = job.get("version", 0) + 1
        _save(job)
        return job


def layout(jid: str) -> list[dict]:
    with _lock:
        job = _jobs[jid]
        v = job.get("version", 0)
        c = _layout_cache.get(jid)
        if c and c[0] == v:
            return c[1]
        info = job["info"]
        lay = build_layout(job["tracks"], job.get("style"), info["width"], info["height"])
        _layout_cache[jid] = (v, lay)
        return lay


# ------------------------------------------------------------------ tasks

def _apply_ai(job: dict, analysis: dict, info: VideoInfo, extra: str = ""):
    tracks = job["tracks"]
    if ai.available():
        res = ai.classify_and_translate(analysis, info, job.get("tone", "humor"), extra)
        for t in tracks:
            r = res.get(t["id"])
            if r:
                t["is_caption"] = bool(r["is_caption"])
                t["enabled"] = t["is_caption"]
                if r.get("corrected_english"):
                    t["text"] = r["corrected_english"].strip()
                t["translation"] = r.get("translation", "").strip()
        job["ai_used"] = True
    else:
        for t in tracks:
            t["is_caption"] = ai.heuristic_is_caption(t, info.height)
            t["enabled"] = t["is_caption"]
            t.setdefault("translation", "")
        job["ai_used"] = False
        job["warning"] = "未設定 AI 金鑰：已用規則判斷字幕，請手動填入中文或到「設定」輸入 API Key 後按「AI 重新翻譯」。"


def _task_analyze(jid: str, args: dict):
    job = _jobs[jid]
    src = str(job_dir(jid) / job["input"])
    info = probe(src)
    update(jid, status="analyzing", progress=0.0, message="開始分析", error=None)
    analysis = analyze(src, info, _progress(jid, 0.0, 0.8))
    info.frame_count = analysis["frame_count"]
    tracks = analysis["tracks"]
    for t in tracks:
        t.update({"is_caption": True, "enabled": True, "translation": "", "override": {}})
    with _lock:
        job["info"] = info.to_dict()
        job["tracks"] = tracks
    update(jid, status="translating", progress=0.82, message=f"AI 判斷字幕並翻譯（{len(tracks)} 段文字）")
    try:
        _apply_ai(job, analysis, info)
    except ai.AIUnavailable:
        raise
    except Exception as e:
        # keep the detection; user can retry the AI step
        for t in tracks:
            t["enabled"] = ai.heuristic_is_caption(t, info.height)
        job["warning"] = f"AI 翻譯失敗：{e}。可稍後按「AI 重新翻譯」重試。"
    # keep sample frames for later AI re-runs
    d = job_dir(jid) / "samples"
    d.mkdir(exist_ok=True)
    for f, jpg in analysis["_sample_jpgs"].items():
        (d / f"{f:06d}.jpg").write_bytes(jpg)
    with _lock:
        job["scale_back"] = analysis["_scale_back"]
        job["version"] = job.get("version", 0) + 1
    update(jid, status="ready", progress=1.0, message="分析完成，可預覽與編輯")
    if job.get("auto_render") and any(t.get("enabled") and t.get("translation") for t in tracks):
        enqueue(jid, "render")


def _load_analysis_for_ai(job: dict) -> dict:
    d = job_dir(job["id"]) / "samples"
    jpgs = {int(p.stem): p.read_bytes() for p in d.glob("*.jpg")}
    return {"tracks": job["tracks"], "_sample_jpgs": jpgs, "_scale_back": job.get("scale_back", 1.0)}


def _task_translate(jid: str, args: dict):
    job = _jobs[jid]
    prev = job["status"]
    update(jid, status="translating", message="AI 重新判斷與翻譯中", error=None)
    try:
        if args.get("tone"):
            job["tone"] = args["tone"]
        job.pop("warning", None)
        _apply_ai(job, _load_analysis_for_ai(job), info_of(job), args.get("instruction", ""))
        job["version"] = job.get("version", 0) + 1
        update(jid, status="ready", message="AI 翻譯完成")
    except Exception as e:
        update(jid, status="ready" if prev != "error" else prev, message="AI 翻譯失敗", warning=str(e))


def _task_render(jid: str, args: dict):
    job = _jobs[jid]
    info = info_of(job)
    lay = layout(jid)
    d = job_dir(jid)
    tmp = d / "output.tmp.mp4"
    update(jid, status="rendering", progress=0.0, message="輸出影片中", error=None)
    st = settings.load()
    render(str(d / job["input"]), str(tmp), info, lay, crf=int(st.get("crf", 16)), preset=st.get("preset", "medium"),
           progress=_progress(jid, 0.0, 1.0))
    name = f"output_v{job.get('version', 0)}_{int(time.time())}.mp4"
    tmp.replace(d / name)
    old = job.get("output_file")
    update(jid, status="done", progress=1.0, message="輸出完成，可下載", has_output=True,
           rendered_version=job.get("version", 0), output_file=name)
    for p in d.glob("output*.mp4"):          # best effort: old file may still be open on Windows
        if p.name not in (name, tmp.name):
            try:
                p.unlink()
            except OSError:
                pass


def output_path(job: dict) -> Path:
    return job_dir(job["id"]) / job.get("output_file", "output.mp4")


TASKS = {"analyze": _task_analyze, "translate": _task_translate, "render": _task_render}


def _worker():
    while True:
        with _cv:
            while not _queue:
                _cv.wait()
            jid, task, args = _queue.popleft()
        if jid not in _jobs:
            continue
        try:
            TASKS[task](jid, args)
        except Exception as e:
            traceback.print_exc()
            try:
                if jid in _jobs:
                    update(jid, status="error", error=str(e), message="處理失敗")
            except Exception:
                traceback.print_exc()


def start_worker():
    load_all()
    threading.Thread(target=_worker, daemon=True, name="subzh-worker").start()
