"""Helpers shared by the API modules."""
from fastapi import HTTPException, Request

from . import config, db, jobio


def auth(request: Request):
    """Optional shared token (DUB_API_TOKEN): Authorization: Bearer <token>, or ?token=<token> for <video> tags."""
    if not config.API_TOKEN:
        return
    bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if config.API_TOKEN not in (bearer, request.query_params.get("token")):
        raise HTTPException(401, "invalid or missing token")


def job_or_404(jid):
    job = db.get(jid)
    if not job:
        raise HTTPException(404, "job not found")
    return job


def view(job):
    jdir = config.JOBS_DIR / job["id"]
    units = jobio.load(jdir / "units.json", {})
    v = {**job, "kind": job["source"]["kind"], "progress": jobio.load(jdir / "progress.json", {}),
         "has_units": bool(units), "units_count": len(units.get("units", [])), "source_lang": units.get("lang"),
         "has_result": (jdir / "result.mp4").exists()}
    if v["kind"] == "course":
        kids = jobio.load(jdir / "children.json", [])
        states = [(db.get(k["job_id"]) or {}).get("status", "?") for k in kids]
        v["videos"] = {"total": len(kids), "done": states.count("done"), "awaiting_review": states.count("awaiting_review"),
                       "failed": states.count("failed")}
        v["has_texts"] = (jdir / "texts.json").exists()
        v["pushed_course_id"] = (jobio.load(jdir / "push.json", {}) or {}).get("course_id")
    return v
