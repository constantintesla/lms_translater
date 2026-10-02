"""Course stage 3: translate PDF documents/presentations in place, keeping the layout.
Teachbase serves a rendered PDF instead of the original pptx/docx, so this works on the PDF: every text block is removed and
the Russian text is written into the same box (font shrinks to fit). Text inside pictures cannot be translated.
python -m dubservice.course.pdfs <job_dir>"""
import re
import sys
from pathlib import Path

import pymupdf

from .. import jobio
from ..mt import llm, parse_lines
from ..stages.translate import glossary_text

FONTS = Path(__file__).resolve().parent.parent / "fonts"
BATCH_CHARS = 2200
MIN_FONT = 5.0

SYSTEM = """/no_think
You translate slide and document text from English into Russian for university students.
Rules:
- Keep each translation SHORT: it must fit the same box as the English text; never longer than 1.25x the source. Prefer concise wording.
- Keep numbers, formulas, code, file names, URLs and product names as they are. Programming terms follow the glossary.
- Natural academic Russian, formal "вы". Do not explain.
- Output ONLY lines in the form: [id] translation. One line per input item."""


def worth(s):
    return len(re.findall(r"[A-Za-zА-Яа-я]", s)) >= 2


MARKER = re.compile(r"^\s*([●•▪◦○■□‣⁃\-–—*]|\d{1,2}[.)])\s*")


def blocks_of(doc):
    """Paragraphs of every page: consecutive lines, a new paragraph at each list marker or after a visible vertical gap.
    A paragraph keeps the style of its dominant (longest) span and the list marker it started with."""
    out = []
    for pno, page in enumerate(doc):
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            paras, cur = [], None
            for ln in blk["lines"]:
                spans = [sp for sp in ln["spans"] if sp["text"].strip()]
                if not spans:
                    continue
                text = "".join(sp["text"] for sp in ln["spans"]).strip()
                size = max(sp["size"] for sp in spans)
                new = cur is None or MARKER.match(text) or ln["bbox"][1] - cur["bbox"][3] > size * 0.9
                if new:
                    cur = {"lines": [], "spans": [], "bbox": list(ln["bbox"])}
                    paras.append(cur)
                cur["lines"].append(text)
                cur["spans"] += spans
                cur["bbox"] = [min(cur["bbox"][0], ln["bbox"][0]), min(cur["bbox"][1], ln["bbox"][1]),
                               max(cur["bbox"][2], ln["bbox"][2]), max(cur["bbox"][3], ln["bbox"][3])]
            for pa in paras:
                text = " ".join(pa["lines"])
                m = MARKER.match(text)
                marker = m.group(1) if m else ""
                text = text[m.end():] if m else text
                if not worth(text):
                    continue
                dom = max(pa["spans"], key=lambda sp: len(sp["text"].strip()))
                out.append({"id": len(out), "page": pno, "text": text, "marker": marker, "bbox": pa["bbox"], "size": dom["size"],
                            "color": dom["color"], "bold": bool(dom["flags"] & 16), "lines": len(pa["lines"])})
    return out


def translate_blocks(blocks, on_progress=lambda p: None):
    gloss = glossary_text()
    system = SYSTEM + (f"\n\nGlossary:\n{gloss}" if gloss else "")
    uniq = {}
    for b in blocks:
        uniq.setdefault(b["text"], []).append(b)
    items = list(enumerate(uniq))
    batches, cur, size = [], [], 0
    for it in items:
        if cur and size + len(it[1]) > BATCH_CHARS:
            batches.append(cur)
            cur, size = [], 0
        cur.append(it)
        size += len(it[1])
    if cur:
        batches.append(cur)
    done = {}
    for n, batch in enumerate(batches):
        lines = "\n".join(f"[{i}] {s}" for i, s in batch)
        got = parse_lines(llm([{"role": "system", "content": system}, {"role": "user", "content": f"Translate:\n{lines}"}]))
        for i, s in batch:
            done[s] = got.get(i, "")
        on_progress(100 * (n + 1) / len(batches))
    for src, group in uniq.items():
        for b in group:
            b["tgt"] = done.get(src, "")
    return blocks


def rgb(c):
    return ((c >> 16 & 255) / 255, (c >> 8 & 255) / 255, (c & 255) / 255)


def obstacles(page, blocks):
    """Rectangles text must not grow into: other paragraphs and pictures (a full-page background does not count)."""
    rects = [pymupdf.Rect(b["bbox"]) for b in blocks]
    area = page.rect.width * page.rect.height
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if r.width * r.height < area * 0.5:
            rects.append(r)
    return rects


def grown(rect, others, page, size):
    """The paragraph's own box plus the free space to its right and below (never into neighbours)."""
    r = pymupdf.Rect(rect)
    right = min(page.rect.width - 24, r.x1 + page.rect.width * 0.35)
    for o in others:
        if o.y0 < rect.y1 and o.y1 > rect.y0 and o.x0 >= rect.x1 - 1 and o != rect:
            right = min(right, o.x0 - 6)
    r.x1 = max(r.x1, right)
    bottom = min(page.rect.height - 16, r.y1 + rect.height * 0.5 + size)
    for o in others:
        if o.x0 < r.x1 and o.x1 > r.x0 and o.y0 >= rect.y1 - 1 and o != rect:
            bottom = min(bottom, o.y0 - 2)
    r.y1 = max(r.y1, bottom)
    return r


def render(src_pdf, blocks, out_pdf):
    """Write the translated paragraphs into a copy of the PDF. Returns stats."""
    doc = pymupdf.open(src_pdf)
    stats = {"blocks": 0, "shrunk": 0, "overflow": 0, "untranslated": 0}
    by_page = {}
    for b in blocks:
        by_page.setdefault(b["page"], []).append(b)
    for pno, page in enumerate(doc):
        todo = [b for b in by_page.get(pno, []) if b.get("tgt")]
        stats["untranslated"] += sum(1 for b in by_page.get(pno, []) if not b.get("tgt"))
        if not todo:
            continue
        others = obstacles(page, by_page[pno])
        for b in todo:
            page.add_redact_annot(pymupdf.Rect(b["bbox"]), fill=False)        # remove the English text, keep the background
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        page.insert_font(fontname="djv", fontfile=str(FONTS / "DejaVuSans.ttf"))
        page.insert_font(fontname="djvb", fontfile=str(FONTS / "DejaVuSans-Bold.ttf"))
        for b in todo:
            rect = pymupdf.Rect(b["bbox"])
            text = (b["marker"] + " " if b["marker"] else "") + b["tgt"]
            font = "djvb" if b["bold"] else "djv"
            align = pymupdf.TEXT_ALIGN_CENTER if (abs((rect.x0 + rect.x1) / 2 - page.rect.width / 2) < page.rect.width * 0.04
                                                 and b["lines"] == 1) else pymupdf.TEXT_ALIGN_LEFT
            placed, fs = False, b["size"]
            for box, floor in ((rect, 0.85), (grown(rect, others, page, b["size"]), 0.45)):   # own box first, then free space
                fs = b["size"] * 0.97
                while fs >= max(MIN_FONT, b["size"] * floor):
                    if page.insert_textbox(box, text, fontsize=fs, fontname=font, color=rgb(b["color"]), align=align) >= 0:
                        placed = True
                        break
                    fs *= 0.94
                if placed:
                    break
            if not placed:
                page.insert_textbox(grown(rect, others, page, b["size"] * 2), text, fontsize=max(MIN_FONT, b["size"] * 0.45),
                                    fontname=font, color=rgb(b["color"]))
                stats["overflow"] += 1
            stats["blocks"] += 1
            stats["shrunk"] += fs < b["size"] * 0.9
    doc.save(out_pdf, garbage=3, deflate=True)
    return stats


def main(job_dir):
    job_dir = Path(job_dir)
    tree = jobio.load(job_dir / "tree.json")
    out_dir = job_dir / "translated"
    out_dir.mkdir(exist_ok=True)
    pdfs = [(m, s) for s in tree["sections"] for m in s["materials"] if m.get("real_type") == "pdf"]
    report = []
    for n, (m, sec) in enumerate(pdfs):
        src = job_dir / m["local_file"]
        doc = pymupdf.open(src)
        blocks = blocks_of(doc)
        images = sum(len(p.get_images()) for p in doc)
        translate_blocks(blocks, lambda p: jobio.progress(job_dir, "course_pdfs", 100 * (n + p / 100) / len(pdfs), f"{m['name'][:40]}"))
        out = out_dir / f"{m['id']}_ru.pdf"
        stats = render(str(src), blocks, str(out))
        jobio.save(out_dir / f"{m['id']}_blocks.json", blocks)
        report.append({"material_id": m["id"], "name": m["name"], "pages": len(doc), "images": images, **stats})
        m["translated_file"] = f"translated/{out.name}"
    jobio.save(job_dir / "pdf_report.json", report)
    jobio.save(job_dir / "tree.json", tree)
    jobio.progress(job_dir, "course_pdfs", 100, f"{len(pdfs)} documents")


if __name__ == "__main__":
    main(sys.argv[1])
