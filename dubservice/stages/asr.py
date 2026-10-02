"""Stage 1: video -> audio.wav, source-language sentences with word-level timings (units.json).
python -m dubservice.stages.asr <job_dir>"""
import re
import subprocess
import sys
from pathlib import Path

from .. import gpu_env  # noqa: F401  (must precede faster_whisper)
from .. import config, jobio


def extract_audio(video, wav):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)


def sentences(words, gap=3.0):
    """Group words into sentences: break on terminal punctuation or on a long silence."""
    units, cur = [], []

    def flush():
        if cur:
            units.append({"start": cur[0]["s"], "end": cur[-1]["e"], "src": "".join(w["w"] for w in cur).strip()})
            cur.clear()

    for w in words:
        cur.append(w)
        if re.search(r"[.!?…]$", w["w"].strip()) or (len(cur) > 1 and w["s"] - cur[-2]["e"] > gap):
            flush()
    flush()
    return units


def drop_fragments(units):
    """Drop echo fragments Whisper leaves after a sentence ("... large datasets." + "sets.", 0.16 s): too short to be speech
    or merely the tail of the previous sentence."""
    key = lambda t: re.sub(r"[^\w]", "", t.lower())
    out = []
    for u in units:
        short = (u["end"] - u["start"]) < 0.3 and len(u["src"].split()) <= 2
        echo = out and key(u["src"]) and key(out[-1]["src"]).endswith(key(u["src"])) and len(key(u["src"])) < len(key(out[-1]["src"]))
        if not (short or echo):
            out.append(u)
    return out


def drop_hallucinations(words):
    """Whisper invents text over silence/music with zero-width words (start == end). Remove those words."""
    return [w for w in words if w["e"] - w["s"] >= 0.04]


def main(job_dir):
    from faster_whisper import WhisperModel
    job_dir = Path(job_dir)
    video = next(job_dir.glob("source.*"))
    wav = job_dir / "audio.wav"
    jobio.progress(job_dir, "asr", 5, "extracting audio")
    extract_audio(video, wav)

    jobio.progress(job_dir, "asr", 15, f"loading {config.ASR_MODEL}")
    model = WhisperModel(config.ASR_MODEL, device="cuda", compute_type="int8_float16")
    opts = jobio.load(job_dir / "options.json", {})
    segs, info = model.transcribe(str(wav), language=opts.get("source_lang") or None, vad_filter=True, beam_size=5,
                                  word_timestamps=True, condition_on_previous_text=False, hallucination_silence_threshold=1.0)
    words = []
    for s in segs:
        words += [{"w": w.word, "s": round(w.start, 2), "e": round(w.end, 2)} for w in s.words]
        jobio.progress(job_dir, "asr", 15 + 80 * min(s.end / max(info.duration, 1), 1), f"{s.end:.0f}/{info.duration:.0f}s")
    words = drop_hallucinations(words)
    units = drop_fragments(sentences(words))
    for i, u in enumerate(units):
        u["i"], u["tgt"] = i, ""
    jobio.save(job_dir / "units.json", {"lang": info.language, "lang_prob": round(info.language_probability, 2),
                                        "duration": round(info.duration, 2), "units": units})
    jobio.progress(job_dir, "asr", 100, f"{len(units)} sentences, language {info.language}")


if __name__ == "__main__":
    main(sys.argv[1])
