"""Stage 3: approved translation -> Russian voice track (F5-TTS Russian, cloned from the narrator) -> result.mp4.
python -m dubservice.stages.tts <job_dir>

Quality guards learned on the pilot: every phrase is generated CANDIDATES times, transcribed back with Whisper and the
closest one wins; reference-text words leaked at the start/end of a phrase are cut out; phrases that do not fit their
slot are regenerated faster instead of being stretched afterwards; the video is frozen on its last frame if the dub
runs past the end, so nothing is cut."""
import difflib
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

from .. import gpu_env  # noqa: F401
from .. import config, jobio
from . import refpick

SR = 24000


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def norm_words(s):
    return re.sub(r"[^а-яёa-z0-9 ]", "", s.lower().replace("ё", "е").replace("+", "")).split()


class Checker:
    """Whisper round-trip: how close is the generated audio to the intended text, and where do the real words start/end."""

    def __init__(self):
        from faster_whisper import WhisperModel
        self.m = WhisperModel(config.CHECK_MODEL, device="cuda", compute_type="int8_float16")

    def score(self, wav, text):
        segs, _ = self.m.transcribe(wav, language=config.TARGET_LANG, beam_size=5, word_timestamps=True,
                                    condition_on_previous_text=False)
        ws = [w for s in segs for w in s.words if norm_words(w.word)]
        got = [norm_words(w.word)[0] for w in ws]
        sm = difflib.SequenceMatcher(None, norm_words(text), got)
        first, last = None, -1
        for op, a1, a2, b1, b2 in sm.get_opcodes():
            if op in ("equal", "replace") and b2 > b1:
                first = b1 if first is None else first
                last = b2 - 1
        if first is None:
            return sm.ratio(), None, None
        head, tail = first, len(got) - 1 - last
        ratio = sm.ratio() - 0.05 * (head + tail)
        return ratio, (max(ws[first].start - 0.08, 0) if head else None), (ws[last].end + 0.12 if tail else None)


def trim(y):
    env = np.abs(y)
    idx = np.where(env > max(env.max() * 0.03, 1e-4))[0]
    if len(idx):
        y = y[: idx[-1] + int(0.05 * SR)]
    n = min(int(0.06 * SR), len(y))
    y = y.copy()
    y[-n:] *= np.linspace(1, 0, n)
    return y


def resample(y, sr):
    if sr == SR:
        return y.astype(np.float32)
    import torch
    import torchaudio
    return torchaudio.functional.resample(torch.from_numpy(y.astype(np.float32))[None], sr, SR)[0].numpy()


def main(job_dir):
    import soundfile as sf
    from f5_tts.api import F5TTS
    from ruaccent import RUAccent

    job_dir = Path(job_dir)
    out_dir = job_dir / "dub"
    out_dir.mkdir(exist_ok=True)
    video = next(job_dir.glob("source.*"))
    data = jobio.load(job_dir / "units.json")
    units, total = data["units"], data["duration"]

    jobio.progress(job_dir, "dub", 2, "choosing reference voice")
    ref = out_dir / "ref.wav"
    ref_text, ref_unit = refpick.make_ref(video, units, total, ref)

    jobio.progress(job_dir, "dub", 5, "loading models")
    acc = RUAccent()
    acc.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
    f5 = F5TTS(model="F5TTS_v1_Base", ckpt_file=config.F5_CKPT, vocab_file=config.F5_VOCAB, device="cuda")
    chk = Checker()

    def synth(text, speed):
        wav, sr, _ = f5.infer(ref_file=str(ref), ref_text=ref_text, gen_text=text, nfe_step=32, cfg_strength=2.0,
                              sway_sampling_coef=-1.0, speed=speed, remove_silence=False)
        return np.asarray(wav).squeeze(), sr

    track = np.zeros(int((total + 10) * SR), dtype=np.float32)
    cursor, report = 0.0, []
    todo = [(k, u) for k, u in enumerate(units) if u.get("tgt", "").strip()]
    for n, (k, u) in enumerate(todo):
        text = u["tgt"].strip()
        text += "" if text[-1] in ".!?…" else "."
        gen = acc.process_all(text)
        nxt = units[k + 1]["start"] if k + 1 < len(units) else u["end"] + 3
        slot = max(nxt - u["start"] - 0.15, 0.5)
        best, spd = None, 1.0
        for c in range(config.CANDIDATES):
            y, sr = synth(gen, spd)
            y = trim(resample(y, sr))
            wav = out_dir / f"u{u['i']}_{c}.wav"
            sf.write(wav, y, SR)
            sc, cut_start, cut_end = chk.score(str(wav), text)
            if cut_end is not None and cut_end * SR < len(y):
                y = trim(y[: int(cut_end * SR)])
            if cut_start is not None:
                y = y[int(cut_start * SR):]
            slow = len(y) / SR > max(len(text) / config.CHARS_PER_SEC, 1.0) * 1.4      # long pauses inside the phrase
            key = (round(sc - (0.06 if slow else 0), 2), -len(y))
            if best is None or key > best[0]:
                best = (key, y, sc)
            if c == 0 and len(y) / SR > slot * 1.05:
                spd = min(len(y) / SR / slot, config.MAX_F5_SPEED)
        _, y, sc = best
        clip, tempo = len(y) / SR, 1.0
        if clip > slot:
            tempo = min(clip / slot, config.MAX_ATEMPO)
            sf.write(out_dir / "pre.wav", y, SR)
            run("ffmpeg", "-y", "-i", str(out_dir / "pre.wav"), "-filter:a", f"atempo={tempo:.3f}", str(out_dir / "fit.wav"))
            y, _ = sf.read(out_dir / "fit.wav", dtype="float32")
        t0 = max(u["start"], cursor)
        i0 = int(t0 * SR)
        if i0 + len(y) > len(track):
            track = np.pad(track, (0, i0 + len(y) - len(track)))
        track[i0:i0 + len(y)] += y
        cursor = t0 + len(y) / SR + 0.12
        report.append({"i": u["i"], "similarity": round(float(sc), 2), "tempo": round(tempo, 2),
                       "src_start": u["start"], "dub_start": round(t0, 2), "dub_end": round(cursor - 0.12, 2)})
        jobio.progress(job_dir, "dub", 8 + 85 * (n + 1) / len(todo), f"phrase {n + 1}/{len(todo)}")

    sf.write(out_dir / "track.wav", track, SR)
    end = cursor - 0.12 + 0.3
    result = job_dir / "result.mp4"
    if end > total:       # dub is longer than the picture: hold the last frame instead of cutting the speech
        run("ffmpeg", "-y", "-i", str(video), "-i", str(out_dir / "track.wav"), "-filter_complex",
            f"[0:v]tpad=stop_mode=clone:stop_duration={end - total:.2f}[v]", "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-t", f"{end:.2f}", str(result))
    else:
        run("ffmpeg", "-y", "-i", str(video), "-i", str(out_dir / "track.wav"), "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(result))
    jobio.save(job_dir / "report.json", {"ref_unit": ref_unit, "phrases": report})
    jobio.progress(job_dir, "dub", 100, "done")


if __name__ == "__main__":
    main(sys.argv[1])
