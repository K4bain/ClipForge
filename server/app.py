"""ClipForge web server: runs the real pipeline as background jobs.

Accepts a YouTube URL, spawns `clipforge --jsonl run <source>` as a
subprocess, streams its JSONL progress events, and exposes rendered clips
for download. Job bookkeeping lives in SERVER_HOME (default /data on HF
Spaces); pipeline artifacts live under CLIPFORGE_HOME/jobs/<id>.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

SERVER_HOME = Path(os.environ.get("CLIPFORGE_SERVER_HOME", "/data/clipforge-server"))
JOBS_META = SERVER_HOME / "jobs"
EVENTS = SERVER_HOME / "events"
MAX_CONCURRENT = int(os.environ.get("CLIPFORGE_MAX_JOBS", "2"))

# Statuses we track ourselves: queued → running → done | failed | cancelled
STAGES = [
    "ingest", "asr", "diarize", "events", "candidates", "score", "camera", "render",
]

app = FastAPI(title="ClipForge", version="0.1.0")

_lock = threading.Lock()
_procs: dict[str, subprocess.Popen] = {}
_running = 0


class JobRequest(BaseModel):
    source: str = Field(min_length=4)
    llm: str = Field(default="ollama", pattern="^(ollama|gemini)$")
    captions: str | None = None
    camera: str | None = Field(default=None, pattern="^(cut|pan|locked)$")


def _meta_path(job_id: str) -> Path:
    return JOBS_META / f"{job_id}.json"


def _events_path(job_id: str) -> Path:
    return EVENTS / f"{job_id}.jsonl"


def _job_dir(job_id: str) -> Path:
    return Path(os.environ.get("CLIPFORGE_HOME", str(Path.home() / ".clipforge"))) / "jobs" / job_id


def _load_meta(job_id: str) -> dict:
    try:
        return json.loads(_meta_path(job_id).read_text())
    except (OSError, json.JSONDecodeError):
        return {"job_id": job_id, "status": "unknown"}


def _progress_from_events(job_id: str) -> dict | None:
    path = _events_path(job_id)
    if not path.exists():
        return None
    progress = {"stage": None, "fraction": 0.0, "message": "starting…"}
    try:
        for line in path.read_text().splitlines():
            ev = json.loads(line)
            if ev.get("event") == "progress":
                progress = {
                    "stage": ev.get("stage"),
                    "fraction": ev.get("fraction", 0.0),
                    "message": ev.get("message", ""),
                }
    except (OSError, json.JSONDecodeError):
        pass
    return progress


def _score_summary(job_id: str) -> dict | None:
    score_path = _job_dir(job_id) / "score.json"
    try:
        data = json.loads(score_path.read_text())["data"]
        clips = []
        for i, clip in enumerate(data.get("clips", [])):
            clips.append(
                {
                    "index": i,
                    "start": clip.get("start"),
                    "end": clip.get("end"),
                    "score": clip.get("score"),
                    "verdict": clip.get("verdict"),
                    "subscores": clip.get("subscores", {}),
                }
            )
        return {"clips": clips}
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def _clip_files(job_id: str) -> list[dict]:
    out = []
    clips_dir = _job_dir(job_id) / "clips"
    if not clips_dir.exists():
        return out
    for mp4 in sorted(clips_dir.glob("clip_*.mp4")):
        m = re.match(r"clip_(\d+)\.mp4", mp4.name)
        if m:
            out.append({"index": int(m.group(1)), "filename": mp4.name, "size": mp4.stat().st_size})
    return out


def _job_listing(job_id: str) -> dict:
    meta = _load_meta(job_id)
    return {
        "job_id": job_id,
        "source": meta.get("source"),
        "status": meta.get("status"),
        "created_at": meta.get("created_at"),
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at"),
        "error": meta.get("error"),
        "progress": _progress_from_events(job_id),
        "llm": meta.get("llm", "ollama"),
        "clips": len(_clip_files(job_id)),
    }


def _cookies_path() -> Path:
    return SERVER_HOME / "youtube-cookies.txt"


def _spawn(job_id: str, source: str, llm: str, captions: str | None, camera: str | None) -> None:
    cmd = [
        os.environ.get("CLIPFORGE_BIN", "clipforge"), "--jsonl", "run", source,
    ]
    if llm:
        cmd += ["--llm", llm]
    if captions:
        cmd += ["--captions", captions]
    if camera:
        cmd += ["--camera", camera]
    env = dict(os.environ)
    env["CLIPFORGE_HOME"] = os.environ.get("CLIPFORGE_HOME", str(Path.home() / ".clipforge"))
    env["HF_HOME"] = os.environ.get("HF_HOME", str(Path(env["CLIPFORGE_HOME"]) / "models" / "hf"))
    if _cookies_path().exists():
        env["CLIPFORGE_YTDLP_COOKIES"] = str(_cookies_path())

    def run() -> None:
        global _running
        with _lock:
            _running += 1
        meta = _load_meta(job_id)
        meta["status"] = "running"
        meta["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _meta_path(job_id).write_text(json.dumps(meta))
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, env=env, bufsize=1,
            )
            with _lock:
                _procs[job_id] = proc
            with _events_path(job_id).open("a", buffering=1) as fh:
                assert proc.stdout is not None
                for line in proc.stdout:
                    fh.write(line)
                    fh.flush()
            code = proc.wait()
        except Exception as err:  # noqa: BLE001
            meta["status"] = "failed"
            meta["error"] = str(err)
        else:
            meta["status"] = "done" if code == 0 else "failed"
            if code != 0:
                tail = _events_path(job_id).read_text().splitlines()[-3:]
                meta["error"] = "pipeline exited %d: %s" % (code, " | ".join(tail))
        finally:
            with _lock:
                _procs.pop(job_id, None)
                _running -= 1
            meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _meta_path(job_id).write_text(json.dumps(meta))

    threading.Thread(target=run, daemon=True).start()


@app.get("/")
def root():
    return {
        "service": "ClipForge",
        "tagline": "Long video in, forged clips out — every score auditable.",
        "version": "0.1.0",
        "endpoints": [
            "POST /api/jobs", "GET /api/jobs", "GET /api/jobs/{id}",
            "GET /api/jobs/{id}/events", "GET /api/jobs/{id}/clips",
            "GET /api/jobs/{id}/clips/{n}/file", "DELETE /api/jobs/{id}",
        ],
        "note": "Full pipeline (ASR, diarization, laughter, camera, render) runs here on CPU.",
    }


@app.get("/health")
def health():
    return {"ok": True, "running": _running, "queued": sum(1 for p in JOBS_META.glob("*.json") if _load_meta(p.stem).get("status") == "queued")}


@app.get("/api/cookies")
def cookies_status():
    return {"present": _cookies_path().exists()}


@app.post("/api/cookies")
async def cookies_upload(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")
    if "# Netscape HTTP Cookie File" not in body and ".youtube.com" not in body:
        raise HTTPException(422, "That does not look like a cookies.txt export (Netscape format).")
    SERVER_HOME.mkdir(parents=True, exist_ok=True)
    _cookies_path().write_text(body)
    return {"ok": True, "bytes": len(body)}


@app.delete("/api/cookies")
def cookies_delete():
    _cookies_path().unlink(missing_ok=True)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


@app.get("/ui")
def ui():
    return FileResponse(str(Path(__file__).parent / "static" / "index.html"))


@app.post("/api/jobs", status_code=202)
def create_job(req: JobRequest):
    global _running
    with _lock:
        active = _running + sum(1 for p in JOBS_META.glob("*.json") if _load_meta(p.stem).get("status") == "queued")
        if active >= MAX_CONCURRENT:
            raise HTTPException(429, f"Too many jobs running ({active}/{MAX_CONCURRENT}). Wait for one to finish.")
    job_id = uuid.uuid4().hex[:8]
    JOBS_META.mkdir(parents=True, exist_ok=True)
    EVENTS.mkdir(parents=True, exist_ok=True)
    meta = {
        "job_id": job_id,
        "source": req.source,
        "llm": req.llm,
        "status": "queued",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _meta_path(job_id).write_text(json.dumps(meta))
    _spawn(job_id, req.source, req.llm, req.captions, req.camera)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs():
    jobs = []
    for p in sorted(JOBS_META.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        jobs.append(_job_listing(p.stem))
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if not _meta_path(job_id).exists():
        raise HTTPException(404, "no such job")
    info = _job_listing(job_id)
    info["score"] = _score_summary(job_id)
    info["clips"] = _clip_files(job_id)
    return info


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    if not _meta_path(job_id).exists():
        raise HTTPException(404, "no such job")
    events_file = _events_path(job_id)

    def stream():
        seen = 0
        last = time.time()
        while True:
            try:
                lines = events_file.read_text().splitlines() if events_file.exists() else []
            except OSError:
                lines = []
            for line in lines[seen:]:
                seen += 1
                yield f"data: {line}\n\n"
            meta = _load_meta(job_id)
            if meta.get("status") in ("done", "failed", "cancelled") and seen >= len(lines):
                yield f"event: done\ndata: {json.dumps(meta)}\n\n"
                return
            if time.time() - last > 30:
                yield ": keepalive\n\n"
                last = time.time()
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/jobs/{job_id}/clips")
def list_clips(job_id: str):
    if not _meta_path(job_id).exists():
        raise HTTPException(404, "no such job")
    return {"job_id": job_id, "clips": _clip_files(job_id), "score": _score_summary(job_id)}


@app.get("/api/jobs/{job_id}/clips/{index}/file")
def clip_file(job_id: str, index: int):
    files = _clip_files(job_id)
    match = next((f for f in files if f["index"] == index), None)
    if match is None:
        raise HTTPException(404, "clip not rendered yet")
    return FileResponse(
        _job_dir(job_id) / "clips" / match["filename"],
        media_type="video/mp4",
        filename=match["filename"],
    )


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    with _lock:
        proc = _procs.get(job_id)
    meta = _load_meta(job_id)
    if proc is not None:
        proc.terminate()
        meta["status"] = "cancelled"
    elif meta.get("status") == "queued":
        meta["status"] = "cancelled"
    else:
        raise HTTPException(400, f"job is {meta.get('status')} — nothing to cancel")
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _meta_path(job_id).write_text(json.dumps(meta))
    return {"job_id": job_id, "status": "cancelled"}


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str):
    if not _meta_path(job_id).exists():
        raise HTTPException(404, "no such job")
    events = _events_path(job_id)
    body = events.read_text() if events.exists() else ""
    return Response(body, media_type="text/plain")