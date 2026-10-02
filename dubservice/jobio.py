"""Files shared between the API process and the stage subprocesses (they only talk through the job directory)."""
import json
import time
from pathlib import Path


def load(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf8")) if p.exists() else default


def save(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf8")


def progress(job_dir, stage, pct, msg=""):
    save(Path(job_dir) / "progress.json", {"stage": stage, "pct": round(pct, 1), "msg": msg, "ts": time.time()})
