"""Usage: python export.py [--download] [--limit N] [course_id ...]
Dumps course -> sections -> materials/quizzes/tasks/scorms to out/<course_id>/course.json;
with --download also saves material files. Keys via TB_PUBLIC_KEY / TB_SECRET_KEY."""
import sys, os, re, json, requests
from tb import TB
args = sys.argv[1:]; dl = "--download" in args
limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
ids = [a for a in args if a.isdigit() and not (args.index(a) > 0 and args[args.index(a)-1] == "--limit")]
t = TB()
courses = [t.get(f"/courses/{i}") for i in ids] if ids else list(t.pages("/courses"))
if limit: courses = courses[:limit]
safe = lambda s: re.sub(r'[\/:*?"<>|\s]+', "_", s or "")[:80]
def url_of(m):
    u = m.get("view_url")
    u = (u.get("mp4") or u.get("webm")) if isinstance(u, dict) else u
    return "https:" + u if u and u.startswith("//") else u
for c in courses:
    d = f"out/{c['id']}"; os.makedirs(d, exist_ok=True)
    secs = t.get(f"/courses/{c['id']}/sections")
    for s in secs:
        for kind in ("materials", "quizzes", "tasks", "scorms"):
            s[kind] = t.get(f"/sections/{s['id']}/{kind}")
            if kind == "materials" and dl:
                for m in s[kind]:
                    u = url_of(m)
                    if not u or not m.get("has_file"): continue
                    fn = f"{d}/{s['id']}_{m['id']}_{safe(m.get('file_name') or m['name'])}"
                    if not os.path.exists(fn):
                        try:
                            r = requests.get(u, timeout=(10, 120))
                            if r.ok:
                                if r.content[:4] == b"%PDF":      # Teachbase serves rendered PDFs for pptx/docx materials
                                    fn = os.path.splitext(fn)[0] + ".pdf"
                                open(fn, "wb").write(r.content)
                            else: print("  skip", r.status_code, u)
                        except requests.RequestException as e:
                            print("  download failed:", u, type(e).__name__)
    c["sections"] = secs
    json.dump(c, open(f"{d}/course.json", "w", encoding="utf8"), ensure_ascii=False, indent=1)
    print(c["id"], c["name"], len(secs), "sections")
