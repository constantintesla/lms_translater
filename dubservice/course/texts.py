"""Course stage 2: every translatable string of the course tree -> Russian (HTML-aware), saved in texts.json.
texts.json is the single source of truth: the review page edits it, package/push apply it to a copy of the tree.
python -m dubservice.course.texts <job_dir>"""
import copy
import re
import sys
from pathlib import Path

from .. import jobio
from ..mt import llm, parse_lines
from ..stages.translate import glossary_text

QUIZ_KEYS = {"name", "description", "title", "text", "question", "answer", "explanation", "comment", "caption"}
BATCH_CHARS = 2500
NL = " ⏎ "

SYSTEM = """/no_think
You translate e-learning course content from English into Russian for university students.
Rules:
- Translate ONLY the visible text. Keep every HTML tag, attribute and entity (&nbsp; &amp; ...) exactly as in the source, in the same order.
- Keep proper names, product names, URLs, e-mail addresses, file names and code as they are. Programming terms follow the glossary.
- Use natural academic Russian, formal "вы". Do not add or drop information; do not explain.
- The marker ⏎ stands for a line break: keep it.
- Output ONLY lines in the form: [id] translation. One line per input item, no comments."""


# ---------- tree access -------------------------------------------------------------------------------------------------

def get_path(obj, path):
    for p in path:
        obj = obj[p]
    return obj


def set_path(obj, path, value):
    get_path(obj, path[:-1])[path[-1]] = value


def worth(s):
    return isinstance(s, str) and bool(re.search(r"[A-Za-zА-Яа-яЁё]{2}", re.sub(r"<[^>]+>|&\w+;", "", s))) \
        and not re.match(r"^(https?://|/)\S+$", s.strip())


def collect(tree):
    """-> list of {id, path, kind, src}. kind: title | html (carries markup)."""
    out = []

    def add(path, kind=None):
        try:
            v = get_path(tree, path)
        except (KeyError, IndexError, TypeError):
            return
        if worth(v):
            out.append({"id": f"t{len(out)}", "path": list(path), "kind": kind or ("html" if "<" in v else "title"), "src": v})

    add(["course", "name"])
    add(["course", "description"])
    for i, sec in enumerate(tree["sections"]):
        add(["sections", i, "name"])
        add(["sections", i, "description"])
        for j, m in enumerate(sec.get("materials", [])):
            base = ["sections", i, "materials", j]
            add(base + ["name"])
            add(base + ["description"])
            for k, b in enumerate((m.get("content") or {}).get("blocks", []) if isinstance(m.get("content"), dict) else []):
                bp = base + ["content", "blocks", k, "data"]
                if b["type"] in ("paragraph", "header", "quote"):
                    add(bp + ["text"])
                elif b["type"] == "list":
                    for n in range(len(b["data"].get("items", []))):
                        add(bp + ["items", n])
                elif b["type"] in ("image", "embed"):
                    add(bp + ["caption"])
        for j, t in enumerate(sec.get("tasks", [])):
            add(["sections", i, "tasks", j, "name"])
            add(["sections", i, "tasks", j, "description"])
        for j, sc in enumerate(sec.get("scorms", [])):
            add(["sections", i, "scorms", j, "name"])
            add(["sections", i, "scorms", j, "description"])
        for j, q in enumerate(sec.get("quizzes", [])):
            walk_quiz(q, ["sections", i, "quizzes", j], add)
    return out


def walk_quiz(node, path, add):
    """Quiz structure is not documented for this API: translate known text keys anywhere inside it."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in QUIZ_KEYS and isinstance(v, str):
                add(path + [k])
            elif isinstance(v, (dict, list)):
                walk_quiz(v, path + [k], add)
    elif isinstance(node, list):
        for n, v in enumerate(node):
            walk_quiz(v, path + [n], add)


def apply(tree, entries):
    """Copy of the tree with the Russian strings in place."""
    out = copy.deepcopy(tree)
    for e in entries:
        if e.get("tgt"):
            set_path(out, e["path"], e["tgt"])
    return out


# ---------- translation ------------------------------------------------------------------------------------------------

def markup(s):
    return re.findall(r"</?\w+|&\w+;", s)


def check(src, tgt):
    return markup(src) == markup(tgt)


def translate(entries, on_progress=lambda p, m: None):
    gloss = glossary_text()
    system = SYSTEM + (f"\n\nGlossary (source -> required Russian):\n{gloss}" if gloss else "")
    uniq = {}
    for e in entries:                       # identical strings (e.g. repeated headings) are translated once
        uniq.setdefault(e["src"], []).append(e)
    items = [(f"u{n}", src) for n, src in enumerate(uniq)]
    done = {}
    batches, cur, size = [], [], 0
    for it in items:
        if cur and size + len(it[1]) > BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it[1])
    if cur:
        batches.append(cur)

    def ask(batch):
        lines = "\n".join(f"[{i}] {s.replace(chr(10), NL)}" for i, s in batch)
        got = parse_lines(llm([{"role": "system", "content": system}, {"role": "user", "content": f"Translate:\n{lines}"}]))
        return {i: got[i] for i in got}

    for b, batch in enumerate(batches):
        got = ask([(int(i[1:]), s) for i, s in batch])
        for i, s in batch:
            t = got.get(int(i[1:]), "")
            if not t or not check(s, t):                       # one individual retry for lost/damaged markup
                t2 = ask([(int(i[1:]), s)]).get(int(i[1:]), "")
                if t2 and (check(s, t2) or not t):
                    t = t2
            done[s] = (t.replace(NL.strip(), "\n").replace("  ", " ") if t else "", bool(t) and check(s, t))
        on_progress(95 * (b + 1) / len(batches), f"{b + 1}/{len(batches)}")
    for src, group in uniq.items():
        tgt, ok = done.get(src, ("", False))
        for e in group:
            e["tgt"], e["markup_ok"] = tgt, ok
    return entries


def main(job_dir):
    job_dir = Path(job_dir)
    tree = jobio.load(job_dir / "tree.json")
    entries = collect(tree)
    jobio.progress(job_dir, "course_texts", 1, f"{len(entries)} strings")
    translate(entries, lambda p, m: jobio.progress(job_dir, "course_texts", p, m))
    jobio.save(job_dir / "texts.json", entries)
    bad = sum(1 for e in entries if not e["markup_ok"])
    jobio.progress(job_dir, "course_texts", 100, f"{len(entries)} strings, {bad} need a look")


if __name__ == "__main__":
    main(sys.argv[1])
