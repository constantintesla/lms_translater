"""Tiny SQLite job store. The heavy artefacts (units, audio, video) live in the job directory, not here."""
import json
import sqlite3
import threading
import time
import uuid

from . import config

_lock = threading.Lock()
_conn = None

STATUSES = ("queued", "running", "awaiting_review", "approved", "done", "failed", "canceled")


def _db():
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, title TEXT, source TEXT, status TEXT, stage TEXT, error TEXT,
            options TEXT, created REAL, updated REAL)""")
    return _conn


def _row(r):
    if r is None:
        return None
    d = dict(r)
    d["source"] = json.loads(d["source"])
    d["options"] = json.loads(d["options"])
    return d


def create(title, source, options):
    jid = uuid.uuid4().hex[:10]
    now = time.time()
    with _lock:
        _db().execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)",
                      (jid, title, json.dumps(source), "queued", "", "", json.dumps(options), now, now))
        _db().commit()
    (config.JOBS_DIR / jid).mkdir(parents=True, exist_ok=True)
    return jid


def get(jid):
    with _lock:
        return _row(_db().execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())


def list_jobs():
    with _lock:
        return [_row(r) for r in _db().execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()]


def update(jid, **fields):
    if "options" in fields:
        fields["options"] = json.dumps(fields["options"])
    fields["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock:
        _db().execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))
        _db().commit()


def next_runnable():
    """Oldest job waiting for a worker: fresh jobs first, then approved ones (so review never blocks the queue)."""
    with _lock:
        for st in ("queued", "approved"):
            r = _db().execute("SELECT * FROM jobs WHERE status=? ORDER BY created LIMIT 1", (st,)).fetchone()
            if r:
                return _row(r)
    return None


def delete(jid):
    with _lock:
        _db().execute("DELETE FROM jobs WHERE id=?", (jid,))
        _db().commit()


def reset_running():
    """After a crash/restart, jobs stuck in 'running' resume: voice stage -> approved (no second review), others -> queued."""
    with _lock:
        _db().execute("UPDATE jobs SET status=CASE WHEN stage='dub' THEN 'approved' ELSE 'queued' END WHERE status='running'")
        _db().commit()
