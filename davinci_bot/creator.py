"""Creator analysis: measurable delivery metrics from raw footage + digests of scripts.

Everything here is evidence for a human-style critique written by the agent; numbers alone are not the verdict.
"""

import re
import subprocess
from pathlib import Path

import numpy as np

from . import audio, media, speech

SR = 16000


def _pitch_track(x, sr=SR, frame=0.05, hop=0.02, fmin=60, fmax=300):
    """F0 per voiced frame via normalised autocorrelation (good enough for range/variation stats)."""
    n, h = int(frame * sr), int(hop * sr)
    lo, hi = int(sr / fmax), int(sr / fmin)
    energy_floor = np.percentile(np.abs(x), 60) * 2
    f0 = []
    for i in range(0, len(x) - n, h):
        w = x[i:i + n]
        if np.sqrt((w ** 2).mean()) < energy_floor:
            continue
        w = w - w.mean()
        ac = np.correlate(w, w, mode="full")[n - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        lag = lo + int(np.argmax(ac[lo:hi]))
        # octave-up guard: if twice the period is about as periodic, the true pitch is the lower one
        if 2 * lag < len(ac) and 2 * lag <= int(sr / 60) and ac[2 * lag] > 0.9 * ac[lag]:
            lag *= 2
        if ac[lag] > 0.45:
            f0.append(sr / lag)
    f0 = np.array(f0)
    if len(f0) > 10:  # drop octave jumps relative to a running median
        med = np.array([np.median(f0[max(0, i - 7):i + 8]) for i in range(len(f0))])
        f0 = f0[np.abs(12 * np.log2(f0 / med)) < 5]
    return f0


def _words(text):
    from pythainlp.tokenize import word_tokenize
    return [w for w in word_tokenize(text, engine="newmm") if w.strip()]


def file_metrics(path, language="th"):
    path = str(Path(path).expanduser().resolve())
    r = speech.analyze(path, language)
    units = r["units"]
    dur = r["duration"]
    speech_t = sum(u["dur"] for u in units)
    pauses = np.array([u["pause_before"] for u in units[1:]]) if len(units) > 1 else np.array([0.0])
    text = " ".join(u["text"] for u in units)
    words = _words(text)
    retakes = sum(1 for u in units if any(f.startswith("retake_of") for f in u.get("flags", [])))
    fillers = sum(1 for u in units if "filler" in u.get("flags", []))
    x = audio.load(speech._wav(path), sr=SR)
    hop = int(SR * 0.05)
    n = len(x) // hop
    env_db = 20 * np.log10(np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) + 1e-9)
    voiced = env_db[env_db > np.percentile(env_db, 60)]
    f0 = _pitch_track(x)
    semis = 12 * np.log2(f0 / np.median(f0)) if len(f0) > 20 else np.array([0.0])
    return {
        "file": Path(path).name, "duration_s": round(dur, 1), "speech_s": round(speech_t, 1),
        "speech_ratio": round(speech_t / dur, 2) if dur else 0,
        "units": len(units), "retake_units": retakes, "retake_ratio": round(retakes / max(1, len(units)), 2),
        "filler_units": fillers,
        "words": len(words), "words_per_min_speech": round(len(words) / max(speech_t, 1) * 60),
        "pause_median_s": round(float(np.median(pauses)), 2), "pause_p90_s": round(float(np.percentile(pauses, 90)), 2),
        "long_pauses_3s": int((pauses > 3).sum()),
        "speech_level_db": round(float(np.median(voiced)), 1), "noise_floor_db": r["levels"]["noise_floor_db"],
        "snr_db": round(float(np.median(voiced)) - r["levels"]["noise_floor_db"], 1),
        "loudness_range_db": round(float(np.percentile(voiced, 90) - np.percentile(voiced, 10)), 1),
        "f0_median_hz": round(float(np.median(f0)), 1) if len(f0) else None,
        "pitch_range_semitones_p10_p90": round(float(np.percentile(semis, 90) - np.percentile(semis, 10)), 1),
        "pitch_std_semitones": round(float(np.std(semis)), 2),
        "transcript_sample": text[:400],
    }


def frames_sheet(paths, out_path, per_file=1, width=480):
    """One frame from the middle of each clip, tiled - to judge framing, light, background, expression."""
    from PIL import Image
    tiles = []
    for p in paths:
        d = media.probe(str(p))["duration"]
        for k in range(per_file):
            t = d * (k + 1) / (per_file + 1)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1",
                                  "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                                 capture_output=True).stdout
            if raw:
                from io import BytesIO
                tiles.append(Image.open(BytesIO(raw)).convert("RGB"))
    if not tiles:
        return None
    cols = 4
    tw, th = tiles[0].size
    sheet = Image.new("RGB", (cols * tw, ((len(tiles) + cols - 1) // cols) * th), (15, 15, 15))
    for i, t in enumerate(tiles):
        sheet.paste(t.resize((tw, th)), ((i % cols) * tw, (i // cols) * th))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=85)
    return str(out_path)


def script_digest(path, head_chars=1500, tail_chars=700):
    """Outline of a long markdown script so the agent can judge structure without reading 80 KB."""
    text = Path(path).expanduser().read_text(encoding="utf-8", errors="replace")
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    headings = [l.strip() for l in body.splitlines() if l.lstrip().startswith("#")]
    words = len(_words(re.sub(r"[#*>\-|`\[\]()]", " ", body)))
    questions = re.findall(r"[^\n?？]{6,80}[?？]", body)
    return {
        "file": str(path), "chars": len(text), "words": words,
        "est_minutes_at_180wpm": round(words / 180, 1),
        "headings": headings[:80], "n_headings": len(headings),
        "questions": len(questions), "question_samples": [q.strip() for q in questions[:12]],
        "opening": body[:head_chars], "ending": body[-tail_chars:],
    }


# ---------- practice feedback ----------

BASELINE_FILE = Path(__file__).resolve().parent.parent / "reports" / "metrics.json"
TARGETS = {  # what "fixed" looks like for this creator (see reports/training-plan-*.md)
    "speech_ratio": (">=", 0.65), "retake_ratio": ("<=", 0.20), "long_pauses_per_10min": ("<=", 10),
    "pitch_range_semitones_p10_p90": (">=", 7.0), "snr_db": (">=", 30.0),
}


def baseline():
    if not BASELINE_FILE.exists():
        return {}
    import json
    rows = [m for proj in json.loads(BASELINE_FILE.read_text()).values() for m in proj["files"]
            if "error" not in m and m.get("words_per_min_speech") and m.get("snr_db", 0) > 15]
    if not rows:
        return {}
    T = sum(m["duration_s"] for m in rows)
    S = sum(m["speech_s"] for m in rows)
    return {"speech_ratio": round(S / T, 2),
            "retake_ratio": round(sum(m["retake_units"] for m in rows) / sum(m["units"] for m in rows), 2),
            "long_pauses_per_10min": round(sum(m["long_pauses_3s"] for m in rows) / T * 600, 1),
            "words_per_min_speech": round(sum(m["words"] for m in rows) / S * 60),
            "pitch_range_semitones_p10_p90": round(float(np.mean([m["pitch_range_semitones_p10_p90"] for m in rows])), 1),
            "snr_db": round(float(np.mean([m["snr_db"] for m in rows])), 1)}


def script_adherence(transcript, script_text):
    """How much of the script was actually said, and which phrases were skipped or changed."""
    import difflib
    clean = lambda t: [w for w in _words(re.sub(r"\[[^\]]*\]|\*\*|[#>*_`]", " ", t)) if w.strip()]  # noqa: E731
    said, ref = clean(transcript), clean(script_text)
    sm = difflib.SequenceMatcher(None, ref, said, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    missed = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace") and i2 - i1 >= 3:
            missed.append({"script": "".join(ref[i1:i2])[:80], "said": "".join(said[j1:j2])[:80] if tag == "replace" else ""})
    return {"script_words": len(ref), "covered_pct": round(matched / max(1, len(ref)) * 100, 1),
            "skipped_or_changed": missed[:15]}


def practice_feedback(path, script_text=None):
    m = file_metrics(path)
    m["long_pauses_per_10min"] = round(m["long_pauses_3s"] / max(m["duration_s"], 1) * 600, 1)
    base = baseline()
    report = {}
    for k in ("speech_ratio", "retake_ratio", "long_pauses_per_10min", "words_per_min_speech",
              "pitch_range_semitones_p10_p90", "snr_db"):
        row = {"now": m.get(k), "baseline": base.get(k)}
        if k in TARGETS:
            op, val = TARGETS[k]
            row["target"] = f"{op} {val}"
            row["pass"] = (m.get(k) is not None) and (m[k] >= val if op == ">=" else m[k] <= val)
        report[k] = row
    out = {"file": m["file"], "duration_s": m["duration_s"], "metrics": report}
    if script_text:
        transcript = " ".join(u["text"] for u in speech.analyze(str(Path(path).expanduser().resolve()))["units"])
        out["script"] = script_adherence(transcript, script_text)
    return out
