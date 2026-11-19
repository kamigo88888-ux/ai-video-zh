"""FastAPI web app: upload, AI processing, preview/edit, render, download, batch."""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import ai, jobs, settings
from .layout import DEFAULT_STYLE, sprite_png

app = FastAPI(title="AI 電商影片自動中文化")
STATIC = settings.ROOT / "static"
ALLOWED = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}


@app.on_event("startup")
def _startup():
    jobs.start_worker()


def _job(jid: str) -> dict:
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "找不到此工作")
    return j


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ------------------------------------------------------------- jobs

@app.post("/api/jobs")
async def upload(files: list[UploadFile] = File(...), tone: str = Form("humor"), auto_render: bool = Form(False)):
    created = []
    for f in files:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in ALLOWED:
            raise HTTPException(400, f"不支援的檔案格式：{f.filename}")
        fd, tmp = tempfile.mkstemp(suffix=ext, dir=settings.DATA)
        with os.fdopen(fd, "wb") as out:
            while chunk := await f.read(8 * 1024 * 1024):
                out.write(chunk)
        job = jobs.create(Path(tmp), f.filename, tone, auto_render)
        created.append({"id": job["id"], "name": job["name"], "status": job["status"]})
    return {"jobs": created}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": jobs.list_jobs(), "ai_available": ai.available()}


def _public_layout(jid):
    return [{k: v for k, v in l.items() if not k.startswith("_")} for l in jobs.layout(jid)]


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = _job(jid)
    out = {k: v for k, v in j.items()}
    out["layout"] = _public_layout(jid) if j.get("tracks") else []
    out["ai_available"] = ai.available()
    out["default_style"] = DEFAULT_STYLE
    return out


@app.patch("/api/jobs/{jid}")
async def edit_job(jid: str, request: Request):
    _job(jid)
    data = await request.json()
    j = jobs.apply_edits(jid, data)
    return {"version": j["version"], "layout": _public_layout(jid)}


@app.get("/api/jobs/{jid}/sprite/{tid}.png")
def sprite(jid: str, tid: int):
    _job(jid)
    for l in jobs.layout(jid):
        if l["id"] == tid:
            return Response(sprite_png(l), media_type="image/png", headers={"Cache-Control": "max-age=86400"})
    raise HTTPException(404)


@app.get("/api/jobs/{jid}/video")
def video(jid: str):
    j = _job(jid)
    return FileResponse(jobs.job_dir(jid) / j["input"], media_type="video/mp4")


@app.get("/api/jobs/{jid}/thumb.jpg")
def thumb(jid: str):
    p = jobs.job_dir(jid) / "thumb.jpg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


@app.get("/api/jobs/{jid}/output")
def output(jid: str, download: int = 0):
    j = _job(jid)
    p = jobs.output_path(j)
    if not p.exists():
        raise HTTPException(404, "尚未輸出")
    name = Path(j["name"]).stem + "_中文版.mp4"
    if download:
        return FileResponse(p, media_type="video/mp4", filename=name)
    return FileResponse(p, media_type="video/mp4")


@app.post("/api/jobs/{jid}/render")
def do_render(jid: str):
    j = _job(jid)
    if j["status"] in ("queued", "analyzing", "translating", "rendering"):
        raise HTTPException(409, "此影片正在處理中")
    jobs.update(jid, status="queued", message="排隊等待輸出", progress=0.0)
    jobs.enqueue(jid, "render")
    return {"ok": True}


@app.post("/api/jobs/{jid}/translate")
async def do_translate(jid: str, request: Request):
    j = _job(jid)
    if not ai.available():
        raise HTTPException(400, "尚未設定 Anthropic API Key")
    data = await request.json()
    if j["status"] in ("queued", "analyzing", "translating", "rendering"):
        raise HTTPException(409, "此影片正在處理中")
    jobs.update(jid, status="queued", message="排隊等待 AI 翻譯")
    jobs.enqueue(jid, "translate", {"tone": data.get("tone"), "instruction": data.get("instruction", "")})
    return {"ok": True}


@app.post("/api/jobs/{jid}/translate_one")
async def translate_one(jid: str, request: Request):
    j = _job(jid)
    data = await request.json()
    tid = int(data["id"])
    t = next((t for t in j["tracks"] if t["id"] == tid), None)
    if not t:
        raise HTTPException(404)
    ctx = [x["text"] for x in j["tracks"] if x.get("is_caption")]
    try:
        import anyio
        zh = await anyio.to_thread.run_sync(lambda: ai.translate_one(
            t["text"], ctx, data.get("tone") or j.get("tone", "humor"), data.get("instruction", "")))
    except ai.AIUnavailable as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI 翻譯失敗：{e}")
    jobs.apply_edits(jid, {"tracks": [{"id": tid, "translation": zh, "enabled": True}]})
    return {"translation": zh, "layout": _public_layout(jid)}


@app.post("/api/jobs/{jid}/reanalyze")
def reanalyze(jid: str):
    j = _job(jid)
    if j["status"] in ("queued", "analyzing", "translating", "rendering"):
        raise HTTPException(409, "此影片正在處理中")
    jobs.update(jid, status="queued", message="排隊中", progress=0.0)
    jobs.enqueue(jid, "analyze")
    return {"ok": True}


@app.delete("/api/jobs/{jid}")
def delete_job(jid: str):
    j = _job(jid)
    if j["status"] in ("analyzing", "translating", "rendering"):
        raise HTTPException(409, "處理中的工作無法刪除")
    jobs.delete(jid)
    return {"ok": True}


@app.post("/api/batch/render")
async def batch_render(request: Request):
    data = await request.json()
    n = 0
    for jid in data.get("ids", []):
        j = jobs.get(jid)
        if j and j["status"] in ("ready", "done"):
            jobs.update(jid, status="queued", message="排隊等待輸出", progress=0.0)
            jobs.enqueue(jid, "render")
            n += 1
    return {"queued": n}


@app.get("/api/batch/download")
def batch_download(ids: str):
    files = []
    for jid in ids.split(","):
        j = jobs.get(jid)
        p = jobs.output_path(j) if j else None
        if p and p.exists():
            files.append((p, Path(j["name"]).stem + "_中文版.mp4"))
    if not files:
        raise HTTPException(404, "沒有可下載的成品")
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p, name in files:
            base, i = name, 1
            while name in used:
                name = base.replace(".mp4", f"_{i}.mp4"); i += 1
            used.add(name)
            z.write(p, name)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('中文化影片.zip')}"})


# ------------------------------------------------------------- info

def _lan_ips() -> list[str]:
    import socket
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))      # no packet is sent; picks the LAN interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


@app.get("/api/info")
def info(request: Request):
    port = request.url.port or 8000
    return {"lan_urls": [f"http://{ip}:{port}" for ip in _lan_ips()]}


# ------------------------------------------------------------- settings

@app.get("/api/settings")
def get_settings():
    s = settings.load()
    key = settings.api_key()
    return {"model": s["model"], "tone": s["tone"], "crf": s["crf"], "preset": s["preset"],
            "api_key_set": bool(key), "api_key_hint": (key[:7] + "…" + key[-4:]) if len(key) > 12 else "",
            "api_key_from_env": bool(settings.api_key_from_env())}


@app.post("/api/settings")
async def set_settings(request: Request):
    data = await request.json()
    upd = {k: data[k] for k in ("model", "tone", "crf", "preset") if k in data}
    if data.get("anthropic_api_key"):
        upd["anthropic_api_key"] = data["anthropic_api_key"].strip()
    settings.save(upd)
    return get_settings()


app.mount("/static", StaticFiles(directory=STATIC), name="static")
