"""Pick the narrator's reference clip automatically. A bad reference caused the "ios"/English leaks in the pilot, so the
rules come from there: one clean sentence of 6-12 s, followed by a real pause so the model never sees a clipped last word."""
import subprocess
from pathlib import Path

PAD_AFTER = 0.5     # natural pause kept after the sentence (seconds)


def choose(units, total):
    """Return the best unit to clone the voice from, or None."""
    best, best_score = None, -1e9
    for k, u in enumerate(units):
        dur = u["end"] - u["start"]
        nxt = units[k + 1]["start"] if k + 1 < len(units) else total
        prv = units[k - 1]["end"] if k else 0
        gap_after, gap_before = nxt - u["end"], u["start"] - prv
        if not 5.0 <= dur <= 12.5 or gap_after < 0.4:
            continue
        words = len(u["src"].split())
        score = -abs(dur - 9) - 2 * max(0.0, 0.9 - gap_after) - 0.5 * max(0.0, 0.3 - gap_before) + 0.05 * min(words, 25)
        if score > best_score:
            best, best_score = u, score
    if best is None:   # fallback: longest sentence under 12 s, trimmed hard
        cands = [u for u in units if u["end"] - u["start"] <= 12.5]
        best = max(cands or units, key=lambda u: u["end"] - u["start"])
    return best


def make_ref(video, units, total, out_wav):
    u = choose(units, total)
    pad = min(PAD_AFTER, max(0.0, (units[u["i"] + 1]["start"] if u["i"] + 1 < len(units) else total) - u["end"]))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-ss", f"{max(u['start'] - 0.05, 0):.2f}",
                    "-t", f"{u['end'] - u['start'] + 0.05 + pad:.2f}", "-vn", "-ac", "1", "-ar", "24000",
                    "-af", "highpass=f=70,loudnorm,apad=pad_dur=0.3", str(out_wav)], check=True)
    return u["src"], u["i"]
