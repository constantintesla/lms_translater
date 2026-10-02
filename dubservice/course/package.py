"""Collect everything that has to be uploaded by hand into <job>/manual_upload with a checklist (README.md).
Safe to run repeatedly: videos that finish later are picked up on the next run.   Also writes tree_ru.json."""
import re
import shutil
from pathlib import Path

from .. import config, db, jobio
from . import texts


def slug(s, n=50):
    return re.sub(r"[^\w.-]+", "_", s or "", flags=re.U).strip("_")[:n] or "item"


def build(job_dir):
    job_dir = Path(job_dir)
    tree = jobio.load(job_dir / "tree.json")
    entries = jobio.load(job_dir / "texts.json", [])
    ru = texts.apply(tree, entries)
    jobio.save(job_dir / "tree_ru.json", ru)
    children = {c["material_id"]: c["job_id"] for c in jobio.load(job_dir / "children.json", [])}
    out = job_dir / "manual_upload"
    shutil.rmtree(out, ignore_errors=True)
    (out / "videos").mkdir(parents=True)
    (out / "documents").mkdir()
    rows, todo = [], {"videos": 0, "videos_pending": 0, "documents": 0, "manual": 0}

    for i, (sec, sec_ru) in enumerate(zip(tree["sections"], ru["sections"]), 1):
        for j, (m, m_ru) in enumerate(zip(sec["materials"], sec_ru["materials"]), 1):
            label = f"{i:02d}_{j:02d}_{slug(m['name'])}"
            title = f"{sec['name']} → {m['name']}"
            cat = m.get("category")
            if cat == "video":
                jid = children.get(m["id"])
                job = db.get(jid) if jid else None
                res = config.JOBS_DIR / jid / "result.mp4" if jid else None
                if job and job["status"] == "done" and res.exists():
                    shutil.copy(res, out / "videos" / f"{label}_ru.mp4")
                    rows.append((title, "видео", f"videos/{label}_ru.mp4", "Заменить файл видео в материале курса (RU)"))
                    todo["videos"] += 1
                else:
                    why = f"ещё не готово ({job['status']}); пересоберите пакет позже" if job else "озвучка видео для этого курса не запускалась"
                    rows.append((title, "видео", "—", why))
                    todo["videos_pending"] += 1
            elif m.get("translated_file"):
                shutil.copy(job_dir / m["translated_file"], out / "documents" / f"{label}_ru.pdf")
                rows.append((title, f"документ ({m.get('declared_ext') or 'pdf'})", f"documents/{label}_ru.pdf",
                             "Загрузить как файл материала. Оригинал в LMS хранится как PDF-копия, поэтому и перевод в PDF"))
                todo["documents"] += 1
            elif cat == "rich_text":
                rows.append((title, "текстовая страница", "—", "Перенесётся автоматически при отправке текстов в новый курс"))
            elif cat == "other" and m.get("external_type") == "embed":
                rows.append((title, "встроенный материал (embed)", "—", "Не переводится и не создаётся через API: добавить вручную, проверить язык содержимого"))
                todo["manual"] += 1
            else:
                rows.append((title, cat or "?", "—", "Файл не переводился (картинка или неизвестный тип): добавить оригинал вручную"))
                todo["manual"] += 1
        for q in sec.get("quizzes", []):
            rows.append((f"{sec['name']} → {q.get('name', 'тест')}", "тест", "—",
                         "Тесты через API создать нельзя: перенести вручную (переведённый текст в tree_ru.json)"))
            todo["manual"] += 1

    lines = [f"# Пакет для ручной загрузки: {ru['course'].get('name')}", "",
             f"Видео: {todo['videos']} готово, {todo['videos_pending']} ожидает. Документов: {todo['documents']}. Ручных действий: {todo['manual']}.", "",
             "Порядок: сначала отправьте тексты в новый курс (кнопка в сервисе), затем загрузите файлы из этого пакета "
             "в материалы нового курса через интерфейс LMS. Файловые материалы API создать не позволяет.", "",
             "| Раздел → материал | Тип | Файл | Что сделать |", "|---|---|---|---|"]
    lines += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf8")
    return todo
