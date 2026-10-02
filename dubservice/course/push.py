"""Send the translated TEXTS to the LMS as a NEW course (the original is never touched).
Creates: course, sections, text pages (rich_text materials) and tasks. Files, videos, tests and embeds cannot be created through
the public API; they are listed in manual_upload/README.md.

Safety: nothing is written unless DUB_ALLOW_PUSH=1 is set AND the caller passes confirm=True. A dry run is always available.
Resumable: push.json records every created id, so a retry after a failure never duplicates what already exists."""
import os
from pathlib import Path

from .. import jobio
from ..teachbase import Teachbase
from . import texts

BLOCK_KEYS = {"paragraph": {"text", "alignment"}, "header": {"text", "level"}, "quote": {"text", "caption", "alignment"},
              "list": {"items", "style"}, "code": {"code"}, "image": {"file", "caption", "stretched", "with_border", "with_background"},
              "embed": {"embed", "caption", "service", "source", "width", "height"}}


def clean_content(content):
    """Keep only what the API schema accepts (it rejects unknown fields)."""
    blocks = []
    for b in (content or {}).get("blocks", []):
        keep = BLOCK_KEYS.get(b.get("type"))
        if keep is None:
            continue
        blocks.append({"type": b["type"], "id": b.get("id"), "data": {k: v for k, v in b.get("data", {}).items() if k in keep}})
    return {"blocks": blocks}


def build_plan(tree_ru, suffix=" (RU)"):
    ops, skipped = [], []
    c = tree_ru["course"]
    ops.append({"key": "course", "op": "create_course", "path": "/courses",
                "body": {"course": {k: v for k, v in {"name": (c.get("name") or "Course") + suffix, "description": c.get("description")}.items() if v}}})
    for i, sec in enumerate(tree_ru["sections"]):
        sk = f"s{sec['id']}"
        ops.append({"key": sk, "op": "create_section", "path": "/courses/{course}/sections", "body": {"section": {"name": sec["name"]}}})
        for m in sec.get("materials", []):
            if m.get("category") == "rich_text":
                ops.append({"key": f"m{m['id']}", "op": "create_material", "path": f"/sections/{{{sk}}}/materials",
                            "body": {"material": {"name": m["name"], "description": m.get("description") or m["name"],
                                                  "content": clean_content(m.get("content"))}}})
            else:
                skipped.append({"what": f"{sec['name']} → {m['name']}", "category": m.get("category"),
                                "reason": "файл/встроенный материал нельзя создать через API"})
        for t in sec.get("tasks", []):
            task = {"name": t["name"], "description": t.get("description") or t["name"]}
            if t.get("score") is not None:
                task["score"] = t["score"]
            ops.append({"key": f"t{t['id']}", "op": "create_task", "path": f"/sections/{{{sk}}}/tasks", "body": {"task": task}})
        for q in sec.get("quizzes", []):
            skipped.append({"what": f"{sec['name']} → {q.get('name')}", "category": "quiz", "reason": "тесты нельзя создать через API"})
        for s in sec.get("scorms", []):
            skipped.append({"what": f"{sec['name']} → {s.get('name')}", "category": "scorm", "reason": "SCORM-пакет нужно загрузить вручную"})
    return ops, skipped


def summary(ops, skipped):
    count = {}
    for o in ops:
        count[o["op"]] = count.get(o["op"], 0) + 1
    return {"will_create": count, "cannot_create": skipped}


def run(job_dir, confirm=False, suffix=" (RU)"):
    job_dir = Path(job_dir)
    tree = jobio.load(job_dir / "tree.json")
    ru = texts.apply(tree, jobio.load(job_dir / "texts.json", []))
    ops, skipped = build_plan(ru, suffix)
    if not confirm:
        return {"dry_run": True, **summary(ops, skipped)}
    if os.environ.get("DUB_ALLOW_PUSH") != "1":
        raise PermissionError("writing to the LMS is disabled: start the service with DUB_ALLOW_PUSH=1")
    state = jobio.load(job_dir / "push.json", {"ids": {}})
    tb = Teachbase()
    for n, op in enumerate(ops):
        if op["key"] in state["ids"]:
            continue
        path = op["path"].replace("{course}", str(state["ids"].get("course", "")))
        for key, new_id in state["ids"].items():
            path = path.replace("{" + key + "}", str(new_id))
        res = tb.write("POST", path, op["body"])
        state["ids"][op["key"]] = res["id"]
        jobio.save(job_dir / "push.json", state)          # persisted after every call: retry resumes here
        jobio.progress(job_dir, "push", 100 * (n + 1) / len(ops), op["op"])
    state["course_id"] = state["ids"]["course"]
    jobio.save(job_dir / "push.json", state)
    return {"dry_run": False, "course_id": state["course_id"], "created": len(state["ids"]), **summary([], skipped)}
