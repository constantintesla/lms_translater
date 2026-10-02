"""HTTP routes of the whole-course mode (/api/courses/...)."""
import os
import zipfile

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, db, jobio
from .common import auth, job_or_404, view
from .course import package, push
from .teachbase import Teachbase

router = APIRouter(prefix="/api/courses")


class CourseJob(BaseModel):
    course_id: int
    review: bool = True              # review the translated texts before the package is built
    review_videos: bool = True       # review each video's translation before it is voiced
    translate_documents: bool = True
    dub_videos: bool = True


class TextEdit(BaseModel):
    id: str
    tgt: str


class Push(BaseModel):
    confirm: bool = False
    suffix: str = " (RU)"


def course_job(jid):
    job = job_or_404(jid)
    if job["source"]["kind"] != "course":
        raise HTTPException(404, "not a course job")
    return job


def idle(job):
    if job["status"] in ("running", "queued"):
        raise HTTPException(409, f"job is {job['status']}")


@router.post("")
def enqueue(body: CourseJob, _=Depends(auth)):
    try:
        name = Teachbase().course(body.course_id).get("name", str(body.course_id))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except requests.HTTPError as e:
        raise HTTPException(502, f"Teachbase: {e}")
    jid = db.create(f"Курс: {name}", {"kind": "course", "course_id": body.course_id},
                    {"review": body.review, "review_videos": body.review_videos,
                     "translate_documents": body.translate_documents, "dub_videos": body.dub_videos})
    return view(db.get(jid))


@router.get("/{jid}/texts")
def texts(jid: str, _=Depends(auth)):
    course_job(jid)
    data = jobio.load(config.JOBS_DIR / jid / "texts.json")
    if data is None:
        raise HTTPException(404, "texts are not translated yet")
    return data


@router.put("/{jid}/texts")
def texts_put(jid: str, edits: list[TextEdit], _=Depends(auth)):
    idle(course_job(jid))
    path = config.JOBS_DIR / jid / "texts.json"
    data = jobio.load(path)
    by = {e["id"]: e for e in data}
    for e in edits:
        if e.id not in by:
            raise HTTPException(422, f"unknown string {e.id}")
        by[e.id]["tgt"] = e.tgt
        by[e.id]["markup_ok"] = True          # reviewed by a person
    jobio.save(path, data)
    return {"saved": len(edits)}


@router.get("/{jid}/documents")
def documents(jid: str, _=Depends(auth)):
    course_job(jid)
    return jobio.load(config.JOBS_DIR / jid / "pdf_report.json", [])


@router.get("/{jid}/videos")
def videos(jid: str, _=Depends(auth)):
    course_job(jid)
    out = []
    for k in jobio.load(config.JOBS_DIR / jid / "children.json", []):
        child = db.get(k["job_id"])
        out.append({**k, "job": view(child) if child else None})
    return out


@router.post("/{jid}/approve_videos")
def approve_videos(jid: str, _=Depends(auth)):
    course_job(jid)
    n = 0
    for k in jobio.load(config.JOBS_DIR / jid / "children.json", []):
        child = db.get(k["job_id"])
        if child and child["status"] == "awaiting_review":
            db.update(child["id"], status="approved")
            n += 1
    return {"approved": n}


@router.post("/{jid}/package")
def build_package(jid: str, _=Depends(auth)):
    """(Re)build the manual-upload package, e.g. after more videos have finished."""
    idle(course_job(jid))
    if not (config.JOBS_DIR / jid / "texts.json").exists():
        raise HTTPException(409, "texts are not translated yet")
    return package.build(config.JOBS_DIR / jid)


@router.get("/{jid}/package/readme")
def package_readme(jid: str, _=Depends(auth)):
    course_job(jid)
    p = config.JOBS_DIR / jid / "manual_upload" / "README.md"
    if not p.exists():
        raise HTTPException(404, "package is not built yet")
    return {"readme": p.read_text(encoding="utf8")}


@router.get("/{jid}/package.zip")
def package_zip(jid: str, _=Depends(auth)):
    course_job(jid)
    src = config.JOBS_DIR / jid / "manual_upload"
    if not src.exists():
        raise HTTPException(404, "package is not built yet")
    zpath = config.JOBS_DIR / jid / "manual_upload.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:          # videos are already compressed
        for f in src.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(src))
    return FileResponse(zpath, filename="manual_upload.zip")


@router.post("/{jid}/push")
def push_texts(jid: str, body: Push, _=Depends(auth)):
    """Without confirm: a dry run listing exactly what would be created. With confirm: creates a NEW course in the LMS."""
    idle(course_job(jid))
    try:
        return push.run(config.JOBS_DIR / jid, confirm=body.confirm, suffix=body.suffix)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@router.post("/{jid}/retry")
def retry(jid: str, stage: str = "texts", _=Depends(auth)):
    job = course_job(jid)
    if job["status"] in ("running", "queued", "approved"):
        raise HTTPException(409, f"job is {job['status']}")
    jdir = config.JOBS_DIR / jid
    drop = {"snapshot": ["tree.json", "texts.json", "pdf_report.json", "children.json"],
            "texts": ["texts.json"], "pdfs": ["pdf_report.json"]}
    if stage == "package":
        db.update(jid, status="approved", error="")
    elif stage in drop:
        for name in drop[stage]:
            (jdir / name).unlink(missing_ok=True)
        db.update(jid, status="queued", error="")
    else:
        raise HTTPException(422, "stage must be snapshot, texts, pdfs or package")
    return view(db.get(jid))


@router.get("/{jid}/files/{name}")
def translated_file(jid: str, name: str, _=Depends(auth)):
    """Translated PDF for preview: <material_id>_ru.pdf"""
    course_job(jid)
    if not name.endswith("_ru.pdf") or "/" in name or "\\" in name:
        raise HTTPException(404)
    p = config.JOBS_DIR / jid / "translated" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)



def push_enabled():
    return os.environ.get("DUB_ALLOW_PUSH") == "1"
