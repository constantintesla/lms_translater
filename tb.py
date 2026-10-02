"""Teachbase Endpoint API v1 client. Keys: TB_PUBLIC_KEY / TB_SECRET_KEY env vars."""
import os, requests, time
BASE = "https://go.teachbase.ru"
class TB:
    def __init__(self):
        self.s = requests.Session(); self.tok = None; self.exp = 0
    def auth(self):
        r = self.s.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials",
            "client_id": os.environ["TB_PUBLIC_KEY"], "client_secret": os.environ["TB_SECRET_KEY"]})
        r.raise_for_status(); j = r.json()
        self.tok = j["access_token"]; self.exp = time.time() + j["expires_in"] - 60
    def get(self, path, **params):
        if not self.tok or time.time() > self.exp: self.auth()
        r = self.s.get(f"{BASE}/endpoint/v1{path}", params=params,
                       headers={"Authorization": f"Bearer {self.tok}"})
        r.raise_for_status(); return r.json()
    def pages(self, path, per_page=100, **params):
        p = 1
        while True:
            d = self.get(path, page=p, per_page=per_page, **params)
            if not d: return
            yield from d
            if len(d) < per_page: return
            p += 1
