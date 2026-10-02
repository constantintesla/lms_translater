"""Stage 2: translate sentences with a local OpenAI-compatible LLM (LM Studio), fitting each to its time slot.
python -m dubservice.stages.translate <job_dir>"""
import json
import re
import sys
from pathlib import Path

import requests

from .. import config, jobio
from ..mt import llm, parse_lines

GLOSSARY = Path(__file__).resolve().parent.parent / "glossary.json"
BATCH = 8
CONTEXT = 3

SYSTEM = """/no_think
You are a professional translator of university lectures, working on voice-over scripts for dubbing into Russian.
Rules:
- Translate the speaker's sentences into natural, correct spoken Russian. Address listeners formally ("вы").
- Keep each translation within its character budget: it will be spoken aloud in a fixed time slot. Be concise, drop filler words, never drop facts.
- Programming terms and code identifiers (Python, list, dict, ...) follow the glossary; keep code in Latin letters.
- Write numbers and abbreviations in a way that is easy to pronounce.
- Output ONLY lines in the form: [number] translation. No comments, no extra lines."""


def budgets(units, total):
    """Characters a phrase may contain: speech window plus up to 1.5 s of the following pause."""
    out = {}
    for k, u in enumerate(units):
        nxt = units[k + 1]["start"] if k + 1 < len(units) else total
        window = (u["end"] - u["start"]) + min(max(nxt - u["end"], 0) * 0.5, 1.5)
        out[u["i"]] = max(14, int(config.CHARS_PER_SEC * window))
    return out


def glossary_text():
    g = json.loads(GLOSSARY.read_text(encoding="utf8")) if GLOSSARY.exists() else {}
    return "\n".join(f"{k} -> {v}" for k, v in g.items())


def main(job_dir):
    job_dir = Path(job_dir)
    data = jobio.load(job_dir / "units.json")
    units, total = data["units"], data["duration"]
    bud = budgets(units, total)
    gloss = glossary_text()
    system = SYSTEM + (f"\n\nGlossary (source -> required Russian):\n{gloss}" if gloss else "")
    done = []
    for b in range(0, len(units), BATCH):
        batch = units[b:b + BATCH]
        ctx = "\n".join(f"{u['src']}  =>  {u['tgt']}" for u in done[-CONTEXT:])
        lines = "\n".join(f"[{u['i']}] (≤{bud[u['i']]} chars) {u['src']}" for u in batch)
        prompt = (f"Previous sentences (for context only, already translated):\n{ctx}\n\n" if ctx else "") + \
                 f"Translate these sentences:\n{lines}"
        got = parse_lines(llm([{"role": "system", "content": system}, {"role": "user", "content": prompt}]))
        for u in batch:
            u["tgt"] = got.get(u["i"], "")
            done.append(u)
        jobio.progress(job_dir, "translate", 85 * min((b + BATCH) / len(units), 1), f"{min(b + BATCH, len(units))}/{len(units)}")

    # second pass: missing or too long phrases, one by one
    for k, u in enumerate(units):
        limit = bud[u["i"]]
        for attempt in range(2):
            if u["tgt"] and len(u["tgt"]) <= limit * 1.2:
                break
            ask = f"Translate into Russian in at most {limit} characters, keeping the meaning:\n[{u['i']}] {u['src']}" \
                if not u["tgt"] else \
                f"This Russian translation is too long ({len(u['tgt'])} chars). Rewrite it in at most {limit} characters, keeping the meaning.\n" \
                f"Source: {u['src']}\nCurrent: {u['tgt']}\nAnswer as: [{u['i']}] text"
            got = parse_lines(llm([{"role": "system", "content": system}, {"role": "user", "content": ask}]))
            if got.get(u["i"]):
                u["tgt"] = got[u["i"]]
        u["budget"] = limit
        jobio.progress(job_dir, "translate", 85 + 15 * (k + 1) / len(units), "fitting to timing")
    jobio.save(job_dir / "units.json", data)
    jobio.progress(job_dir, "translate", 100, "done")


if __name__ == "__main__":
    main(sys.argv[1])
