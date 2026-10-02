"""Teachbase Endpoint API v1: find the video materials of a course and fetch the files. Read-only."""
import time
from pathlib import Path

import requests

from . import config

BASE = "https://go.teachbase.ru"


class Teachbase:
    def __init__(self):
        if not (config.TB_PUBLIC_KEY and config.TB_SECRET_KEY):
            raise RuntimeError("Set TB_PUBLIC_KEY and TB_SECRET_KEY environment variables")
        self.s = requests.Session()
        self.token, self.exp = None, 0.0

    def _auth(self):
        r = self.s.post(f"{BASE}/oauth/token", timeout=30, data={
            "grant_type": "client_credentials", "client_id": config.TB_PUBLIC_KEY, "client_secret": config.TB_SECRET_KEY})
        r.raise_for_status()
        j = r.json()
        self.token, self.exp = j["access_token"], time.time() + j["expires_in"] - 60

    def get(self, path, **params):
        if not self.token or time.time() > self.exp:
            self._auth()
        r = self.s.get(f"{BASE}/endpoint/v1{path}", params=params, timeout=60,
                       headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()

    def write(self, method, path, body):
        """POST/PATCH to the Endpoint API. Only reached through course/push.py, which is gated by DUB_ALLOW_PUSH and an explicit confirm."""
        if not self.token or time.time() > self.exp:
            self._auth()
        r = self.s.request(method, f"{BASE}/endpoint/v1{path}", json=body, timeout=60,
                           headers={"Authorization": f"Bearer {self.token}"})
        if not r.ok:
            raise RuntimeError(f"Teachbase {method} {path}: HTTP {r.status_code} {r.text[:300]}")
        return r.json()

    def course(self, course_id):
        return self.get(f"/courses/{course_id}")

    def videos(self, course_id):
        """All video materials of a course, in course order."""
        out = []
        for sec in self.get(f"/courses/{course_id}/sections"):
            for m in self.get(f"/sections/{sec['id']}/materials"):
                if m.get("category") == "video" and m.get("has_file"):
                    out.append({"material_id": m["id"], "name": m.get("name") or m.get("file_name"),
                                "section": sec.get("name"), "file_name": m.get("file_name"), "url": media_url(m)})
        return out

    def material(self, course_id, material_id):
        for v in self.videos(course_id):
            if v["material_id"] == material_id:
                return v
        raise KeyError(f"video material {material_id} not found in course {course_id}")


def media_url(material):
    u = material.get("view_url")
    u = (u.get("mp4") or u.get("webm")) if isinstance(u, dict) else u
    return "https:" + u if u and u.startswith("//") else u


def download(url, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
