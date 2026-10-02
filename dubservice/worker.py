"""One GPU worker, one job at a time. Every stage runs in its own subprocess so VRAM is fully released between stages
(ASR large-v3, the LLM in LM Studio and F5-TTS never share the 8 GB card)."""
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from . import config, db, jobio, teachbase
from .course import package

STAGES = {"asr": "stages.asr", "translate": "stages.translate", "dub": "stages.tts",
          "course_snapshot": "course.snapshot", "course_texts": "course.texts", "course_pdfs": "course.pdfs"}


def run_stage(job, name):
    jdir = config.JOBS_DIR / job["id"]
    db.update(job["id"], stage=name)
    with open(jdir / "log.txt", "a", encoding="utf8") as log:
        log.write(f"\n=== {name} ===\n")
        log.flush()
        p = subprocess.run([sys.executable, "-m", f"dubservice.{STAGES[name]}", str(jdir)],
                           cwd=config.ROOT, stdout=log, stderr=subprocess.STDOUT)
    if p.returncode:
        tail = (jdir / "log.txt").read_text(encoding="utf8", errors="ignore").strip().splitlines()[-8:]
        raise RuntimeError(f"stage {name} failed (exit {p.returncode}):\n" + "\n".join(tail))


def fetch(job):
    jdir = config.JOBS_DIR / job["id"]
    if list(jdir.glob("source.*")):
        return
    src = job["source"]
    if src["kind"] == "teachbase":
        db.update(job["id"], stage="fetch")
        v = teachbase.Teachbase().material(src["course_id"], src["material_id"])
        suffix = Path(v["file_name"] or "x.mp4").suffix or ".mp4"
        teachbase.download(v["url"], jdir / f"source{suffix}")
    elif not list(jdir.glob("source.*")):
        raise RuntimeError("no source file for this job")


def spawn_videos(job):
    """One dubbing job per video material of the course; they share the queue with everything else."""
    jdir = config.JOBS_DIR / job["id"]
    if (jdir / "children.json").exists():
        return
    tree = jobio.load(jdir / "tree.json")
    course_id = job["source"]["course_id"]
    kids = []
    for sec in tree["sections"]:
        for m in sec["materials"]:
            if m.get("category") == "video" and m.get("has_file"):
                jid = db.create(f"{tree['course']['name']} / {m['name']}",
                                {"kind": "teachbase", "course_id": course_id, "material_id": m["id"]},
                                {"review": job["options"].get("review_videos", True), "parent": job["id"]})
                kids.append({"material_id": m["id"], "job_id": jid})
    jobio.save(jdir / "children.json", kids)


def process_course(job):
    jid = job["id"]
    jdir = config.JOBS_DIR / jid
    if job["status"] == "queued":
        db.update(jid, status="running", error="")
        jobio.save(jdir / "course_ref.json", {"course_id": job["source"]["course_id"]})
        if not (jdir / "tree.json").exists():
            run_stage(job, "course_snapshot")
        if not (jdir / "texts.json").exists():
            run_stage(job, "course_texts")
        if job["options"].get("translate_documents", True) and not (jdir / "pdf_report.json").exists():
            run_stage(job, "course_pdfs")
        if job["options"].get("dub_videos", True):
            spawn_videos(job)
        db.update(jid, status="awaiting_review" if job["options"].get("review", True) else "approved", stage="review")
    else:   # approved: texts accepted -> build the package of files for manual upload
        db.update(jid, status="running", stage="package", error="")
        package.build(jdir)
        db.update(jid, status="done", stage="done")


def process(job):
    jid = job["id"]
    jdir = config.JOBS_DIR / jid
    jobio.save(jdir / "options.json", job["options"])
    try:
        if job["source"]["kind"] == "course":
            return process_course(job)
        if job["status"] == "queued":
            db.update(jid, status="running", error="")
            fetch(job)
            if not (jdir / "units.json").exists():
                run_stage(job, "asr")
            data = jobio.load(jdir / "units.json")
            if any(not u.get("tgt", "").strip() for u in data["units"]):   # keep an existing (edited) translation
                run_stage(job, "translate")
            db.update(jid, status="awaiting_review" if job["options"].get("review", True) else "approved", stage="review")
        else:   # approved
            db.update(jid, status="running", error="")
            run_stage(job, "dub")
            db.update(jid, status="done", stage="done")
    except Exception as e:
        traceback.print_exc()
        db.update(jid, status="failed", error=str(e))


def loop(stop: threading.Event):
    db.reset_running()
    while not stop.is_set():
        job = db.next_runnable()
        if job:
            process(job)
        else:
            stop.wait(2)


def start():
    stop = threading.Event()
    t = threading.Thread(target=loop, args=(stop,), daemon=True, name="dub-worker")
    t.start()
    return stop


def remove_files(jid):
    shutil.rmtree(config.JOBS_DIR / jid, ignore_errors=True)
