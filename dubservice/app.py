"""HTTP API + web page.   python -m dubservice            (or: uvicorn dubservice.app:app)"""
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, jobio, worker
from .common import auth, job_or_404, view
from .course_api import router as course_router, push_enabled
from .teachbase import Teachbase

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app):
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    stop = worker.start()
    yield
    stop.set()


app = FastAPI(title="Lecture dubbing service", lifespan=lifespan)
app.include_router(course_router)








@app.get("/api/health")
def health(_=Depends(auth)):
    gpu = None
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        pass
    llm = {"url": config.LLM_URL, "ok": False, "models": []}
    try:
        llm["models"] = [m["id"] for m in requests.get(f"{config.LLM_URL}/models", timeout=4).json()["data"]]
        llm["ok"] = bool(llm["models"])
    except (requests.RequestException, KeyError, ValueError):
        pass
    return {"ffmpeg": bool(shutil.which("ffmpeg")), "gpu": gpu, "llm": llm,
            "tts_weights": Path(config.F5_CKPT).exists() and Path(config.F5_VOCAB).exists(),
            "teachbase_keys": bool(config.TB_PUBLIC_KEY and config.TB_SECRET_KEY)}


@app.get("/api/jobs")
def jobs(_=Depends(auth)):
    return [view(j) for j in db.list_jobs()]


@app.get("/api/jobs/{jid}")
def job(jid: str, _=Depends(auth)):
    return view(job_or_404(jid))


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), title: str = Form(""), review: bool = Form(True),
                 source_lang: str = Form(""), _=Depends(auth)):
    suffix = Path(file.filename or "x.mp4").suffix.lower() or ".mp4"
    jid = db.create(title or file.filename, {"kind": "upload", "name": file.filename},
                    {"review": review, "source_lang": source_lang})
    with open(config.JOBS_DIR / jid / f"source{suffix}", "wb") as f:
        shutil.copyfileobj(file.file, f)
    return view(db.get(jid))


class TeachbaseJobs(BaseModel):
    course_id: int
    material_ids: list[int]
    review: bool = True
    source_lang: str = ""


@app.get("/api/teachbase/courses/{course_id}/videos")
def tb_videos(course_id: int, _=Depends(auth)):
    try:
        tb = Teachbase()
        return {"course": tb.course(course_id).get("name"), "videos": tb.videos(course_id)}
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except requests.HTTPError as e:
        raise HTTPException(502, f"Teachbase: {e}")


@app.post("/api/jobs/teachbase")
def tb_enqueue(body: TeachbaseJobs, _=Depends(auth)):
    try:
        tb = Teachbase()
        course = tb.course(body.course_id).get("name", str(body.course_id))
        known = {v["material_id"]: v for v in tb.videos(body.course_id)}
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except requests.HTTPError as e:
        raise HTTPException(502, f"Teachbase: {e}")
    created = []
    for mid in body.material_ids:
        if mid not in known:
            raise HTTPException(404, f"video material {mid} is not in course {body.course_id}")
        jid = db.create(f"{course} / {known[mid]['name']}",
                        {"kind": "teachbase", "course_id": body.course_id, "material_id": mid},
                        {"review": body.review, "source_lang": body.source_lang})
        created.append(view(db.get(jid)))
    return created


@app.get("/api/jobs/{jid}/units")
def get_units(jid: str, _=Depends(auth)):
    job_or_404(jid)
    data = jobio.load(config.JOBS_DIR / jid / "units.json")
    if not data:
        raise HTTPException(404, "no transcript yet")
    return data


class UnitEdit(BaseModel):
    i: int
    tgt: str


class UnitsEdit(BaseModel):
    units: list[UnitEdit]


@app.put("/api/jobs/{jid}/units")
def put_units(jid: str, body: UnitsEdit, _=Depends(auth)):
    job = job_or_404(jid)
    if job["status"] not in ("awaiting_review", "failed", "done"):
        raise HTTPException(409, f"cannot edit while job is {job['status']}")
    path = config.JOBS_DIR / jid / "units.json"
    data = jobio.load(path)
    by = {u["i"]: u for u in data["units"]}
    for e in body.units:
        if e.i not in by:
            raise HTTPException(422, f"unknown unit {e.i}")
        by[e.i]["tgt"] = e.tgt.strip()
    jobio.save(path, data)
    return {"saved": len(body.units)}


@app.post("/api/jobs/{jid}/approve")
def approve(jid: str, _=Depends(auth)):
    job = job_or_404(jid)
    if job["status"] != "awaiting_review":
        raise HTTPException(409, f"job is {job['status']}, not awaiting review")
    db.update(jid, status="approved")
    return view(db.get(jid))


@app.post("/api/jobs/{jid}/retry")
def retry(jid: str, stage: str = "dub", _=Depends(auth)):
    """stage=asr: redo everything; translate: redo translation (overwrites edits); dub: redo only the voice."""
    job = job_or_404(jid)
    if job["status"] in ("running", "queued", "approved"):
        raise HTTPException(409, f"job is {job['status']}")
    jdir = config.JOBS_DIR / jid
    if stage == "asr":
        (jdir / "units.json").unlink(missing_ok=True)
        db.update(jid, status="queued", error="")
    elif stage == "translate":
        data = jobio.load(jdir / "units.json")
        if not data:
            raise HTTPException(409, "no transcript yet; use stage=asr")
        for u in data["units"]:
            u["tgt"] = ""
        jobio.save(jdir / "units.json", data)
        db.update(jid, status="queued", error="")
    elif stage == "dub":
        if not jobio.load(jdir / "units.json"):
            raise HTTPException(409, "nothing to dub yet")
        db.update(jid, status="approved", error="")
    else:
        raise HTTPException(422, "stage must be asr, translate or dub")
    return view(db.get(jid))


@app.delete("/api/jobs/{jid}")
def delete(jid: str, _=Depends(auth)):
    job = job_or_404(jid)
    if job["status"] == "running":
        raise HTTPException(409, "job is running")
    db.delete(jid)
    worker.remove_files(jid)
    return {"deleted": jid}


def _file(jid, pattern, name=None):
    job_or_404(jid)
    files = sorted((config.JOBS_DIR / jid).glob(pattern))
    if not files:
        raise HTTPException(404, "file not ready")
    return FileResponse(files[0], filename=name or files[0].name)


@app.get("/api/jobs/{jid}/source")
def source(jid: str, _=Depends(auth)):
    return _file(jid, "source.*")


@app.get("/api/jobs/{jid}/result")
def result(jid: str, _=Depends(auth)):
    job = job_or_404(jid)
    return _file(jid, "result.mp4", f"{job['title'][:80]}_ru.mp4".replace("/", "_"))


@app.get("/api/jobs/{jid}/report")
def report(jid: str, _=Depends(auth)):
    job_or_404(jid)
    data = jobio.load(config.JOBS_DIR / jid / "report.json")
    if not data:
        raise HTTPException(404, "no report yet")
    return data


@app.get("/api/jobs/{jid}/log")
def log(jid: str, _=Depends(auth)):
    job_or_404(jid)
    p = config.JOBS_DIR / jid / "log.txt"
    return {"log": p.read_text(encoding="utf8", errors="ignore")[-6000:] if p.exists() else ""}


@app.get("/api/config")
def public_config(_=Depends(auth)):
    return {"push_enabled": push_enabled()}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
