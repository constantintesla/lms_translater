"""Course stage 1: read the whole course (sections, materials of every kind, tasks, quizzes, scorms) into tree.json and
download the non-video files. Read-only on the LMS.   python -m dubservice.course.snapshot <job_dir>"""
import json
import re
import sys
from pathlib import Path

from .. import jobio
from ..teachbase import Teachbase, download, media_url

MAGIC = [(b"%PDF", "pdf"), (b"\x89PNG", "png"), (b"\xff\xd8\xff", "jpg"), (b"GIF8", "gif"), (b"PK\x03\x04", "zip"),
         (b"RIFF", "webp")]


def real_type(path, fallback):
    """Teachbase serves converted renditions: a '.pptx' material is really a PDF. Trust the bytes, not the name."""
    head = Path(path).read_bytes()[:12]
    for sig, ext in MAGIC:
        if head.startswith(sig):
            return ext
    return "mp4" if head[4:8] == b"ftyp" else (fallback or "bin")


def safe(s, n=60):
    return re.sub(r"[^\w.-]+", "_", s or "", flags=re.U).strip("_")[:n] or "item"


def main(job_dir):
    job_dir = Path(job_dir)
    opts = jobio.load(job_dir / "options.json", {})
    course_id = jobio.load(job_dir / "course_ref.json")["course_id"]
    tb = Teachbase()
    jobio.progress(job_dir, "course_snapshot", 2, "reading course")
    course = tb.course(course_id)
    sections = tb.get(f"/courses/{course_id}/sections")
    files_dir = job_dir / "files"
    files_dir.mkdir(exist_ok=True)
    tree = {"course": course, "sections": []}
    for k, sec in enumerate(sections):
        item = dict(sec)
        for kind in ("materials", "tasks", "quizzes", "scorms"):
            item[kind] = tb.get(f"/sections/{sec['id']}/{kind}")
        tree["sections"].append(item)
        jobio.progress(job_dir, "course_snapshot", 5 + 40 * (k + 1) / len(sections), f"section {k + 1}/{len(sections)}")

    todo = [(s, m) for s in tree["sections"] for m in s["materials"] if m.get("has_file") and m.get("category") != "video"]
    for n, (sec, m) in enumerate(todo):
        url = media_url(m)
        if not url:
            continue
        tmp = files_dir / f"{m['id']}.part"
        download(url, tmp)
        ext = real_type(tmp, m.get("extension"))
        dest = files_dir / f"{m['id']}.{ext}"
        tmp.replace(dest)
        m["local_file"], m["real_type"] = f"files/{dest.name}", ext
        m["declared_ext"] = m.get("extension")
        jobio.progress(job_dir, "course_snapshot", 45 + 55 * (n + 1) / len(todo), f"file {n + 1}/{len(todo)}")
    jobio.save(job_dir / "tree.json", tree)
    n_vid = sum(1 for s in tree["sections"] for m in s["materials"] if m.get("category") == "video")
    jobio.progress(job_dir, "course_snapshot", 100,
                   f"{len(tree['sections'])} sections, {n_vid} videos, {len(todo)} files")


if __name__ == "__main__":
    main(sys.argv[1])
