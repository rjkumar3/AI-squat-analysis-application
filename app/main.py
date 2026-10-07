"""FastAPI server: upload -> background job -> report + annotated playback."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
import uuid
from queue import Queue

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import evaluate, pipeline

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
os.makedirs(DATA, exist_ok=True)
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "150")) * 1024 * 1024
ALLOWED_EXT = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}

STAGES = [
    ("validate", "Checking the video"),
    ("transcode", "Preparing video for playback"),
    ("pose", "Finding hips, knees, ankles and shoulders"),
    ("bar", "Tracking the barbell"),
    ("measure", "Finding reps and measuring"),
    ("assess", "Checking against the standard"),
]

app = FastAPI(title="Squat Standard Coach")
jobs: dict[str, dict] = {}
queue: Queue = Queue()
lock = threading.Lock()


def job_dir(jid):
    return os.path.join(DATA, jid)


def save_job(job):
    with open(os.path.join(job_dir(job["id"]), "job.json"), "w") as f:
        json.dump({k: v for k, v in job.items() if k != "result"}, f)


def set_stage(job, name, p):
    job["stage"] = name
    job["stage_progress"] = round(float(p), 3)
    job["updated"] = time.time()


def worker():
    while True:
        jid = queue.get()
        job = jobs[jid]
        d = job_dir(jid)
        try:
            job["state"] = "running"
            set_stage(job, "validate", 0)
            info = pipeline.probe(job["src"])
            job["source_info"] = info
            if info["duration"] > pipeline.MAX_DURATION_S + 0.5:
                raise ValueError(f"The video is {info['duration']:.0f} s long; the limit is {pipeline.MAX_DURATION_S:.0f} s. Trim it to the set you want analysed.")
            if info["duration"] < 1.5:
                raise ValueError("The video is shorter than 1.5 seconds.")
            set_stage(job, "transcode", 0.1)
            pipeline.transcode(job["src"], os.path.join(d, "video.mp4"))
            set_stage(job, "transcode", 1)
            m = pipeline.run(os.path.join(d, "video.mp4"), job.get("markers"),
                             stage=lambda n, p: set_stage(job, n, p))
            json.dump(m, open(os.path.join(d, "measurements.json"), "w"))
            res = pipeline.assess(m, stage=lambda n, p: set_stage(job, n, p))
            json.dump(res, open(os.path.join(d, "assessment.json"), "w"))
            job["state"] = "done"
            set_stage(job, "done", 1)
        except ValueError as e:
            job["state"] = "failed"
            job["error"] = str(e)
        except Exception as e:  # unexpected: keep the details server-side
            traceback.print_exc()
            job["state"] = "failed"
            job["error"] = f"Processing failed unexpectedly ({type(e).__name__}). Please try another video."
        finally:
            save_job(job)
            queue.task_done()


threading.Thread(target=worker, daemon=True).start()


def _load_existing():
    for jid in os.listdir(DATA):
        p = os.path.join(DATA, jid, "job.json")
        if os.path.exists(p):
            try:
                j = json.load(open(p))
                if j.get("state") in ("queued", "running"):
                    j["state"] = "failed"
                    j["error"] = "The server restarted while this video was processing. Please upload it again."
                jobs[jid] = j
            except Exception:
                pass


_load_existing()


@app.get("/api/config")
def config():
    skill, _, _ = evaluate.load_skill()
    return {
        "max_duration_s": pipeline.MAX_DURATION_S,
        "max_upload_mb": MAX_BYTES // (1024 * 1024),
        "formats": sorted(e.lstrip(".").upper() for e in ALLOWED_EXT),
        "stages": [{"id": s, "label": l} for s, l in STAGES],
        "agent_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "skill_version": skill.get("version"), "skill_hash": skill["_hash"],
    }


@app.get("/api/skill")
def get_skill():
    skill, raw, md = evaluate.load_skill()
    return {"criteria": skill, "criteria_yaml": raw, "skill_md": md}


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), markers: str = Form("")):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported file type '{ext or '?'}'. Use MP4, MOV, WebM, AVI or MKV.")
    jid = uuid.uuid4().hex[:12]
    d = job_dir(jid)
    os.makedirs(d)
    src = os.path.join(d, "source" + ext)
    size = 0
    with open(src, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                shutil.rmtree(d, ignore_errors=True)
                raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            out.write(chunk)
    mk = None
    if markers:
        try:
            mk = json.loads(markers)
        except Exception:
            raise HTTPException(400, "Markers were not valid JSON.")
    job = {"id": jid, "state": "queued", "stage": "queued", "stage_progress": 0, "filename": file.filename,
           "src": src, "markers": mk, "created": time.time(), "updated": time.time(), "error": None}
    jobs[jid] = job
    save_job(job)
    queue.put(jid)
    return {"id": jid}


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "Unknown analysis id.")
    out = {k: job.get(k) for k in ("id", "state", "stage", "stage_progress", "error", "filename", "markers")}
    out["queue_position"] = None
    if job["state"] == "done":
        d = job_dir(jid)
        out["measurements"] = json.load(open(os.path.join(d, "measurements.json")))
        out["assessment"] = json.load(open(os.path.join(d, "assessment.json")))
    return out


@app.post("/api/jobs/{jid}/reassess")
def reassess(jid: str, use_agent: bool = True):
    """Re-applies the CURRENT skill to cached measurements (no pose re-run)."""
    job = jobs.get(jid)
    if not job or job["state"] != "done":
        raise HTTPException(400, "Analysis not finished.")
    d = job_dir(jid)
    m = json.load(open(os.path.join(d, "measurements.json")))
    try:
        res = pipeline.assess(m, use_agent=use_agent)
    except Exception as e:
        raise HTTPException(400, f"Skill could not be applied: {e}")
    json.dump(res, open(os.path.join(d, "assessment.json"), "w"))
    return res


@app.get("/api/jobs/{jid}/video")
def video(jid: str):
    p = os.path.join(job_dir(jid), "video.mp4")
    if not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="video/mp4")


@app.get("/api/jobs/{jid}/annotated.mp4")
def annotated(jid: str):
    from . import render

    d = job_dir(jid)
    out = os.path.join(d, "annotated.mp4")
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(os.path.join(d, "assessment.json")):
        render.render(d, out)
    return FileResponse(out, media_type="video/mp4", filename=f"squat-annotated-{jid}.mp4")


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


app.mount("/", StaticFiles(directory=os.path.join(ROOT, "web"), html=True), name="web")
