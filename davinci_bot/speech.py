"""Speech analysis for rough cuts: where speech is, what was said, and which lines look like retakes."""

import difflib
import io
import json
import socket
import subprocess
import time
import wave
from contextlib import contextmanager
from pathlib import Path

import httpx
import logging
import numpy as np

from . import media

logging.getLogger("httpx").setLevel(logging.WARNING)

SR = 16000
HOP = 0.01  # 10 ms energy frames
FILLERS = {"เอ่อ", "อ่า", "อืม", "เอ้อ", "อ้า", "เออ", "ฮะ", "อะ", "เอิ่ม", "อึ่ม"}


def _wav(path):
    """16 kHz mono wav next to the transcript cache (reused between runs)."""
    key = media._cache_key(path, "wav")
    out = media.CACHE / f"{key}.wav"
    if not out.exists():
        media.CACHE.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), tmp],
                       check=True)
        tmp.rename(out)
    return out


def _energy_db(wav_path):
    with wave.open(str(wav_path)) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    hop = int(SR * HOP)
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms)


def detect_speech(path, min_pause=0.25, min_speech=0.12, threshold_db=None):
    """Speech islands from signal energy. Boundaries here are far more precise than Whisper's.

    threshold_db defaults to noise floor + 12 dB (noise floor = 10th percentile frame level).
    """
    db = _energy_db(_wav(path))
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 98))
    thr = threshold_db if threshold_db is not None else min(floor + 12, (floor + peak) / 2)
    voiced = db > thr
    islands, start = [], None
    for i, v in enumerate(voiced):
        if v and start is None:
            start = i
        elif not v and start is not None:
            islands.append([start * HOP, i * HOP])
            start = None
    if start is not None:
        islands.append([start * HOP, len(voiced) * HOP])
    merged = []
    for s, e in islands:
        if merged and s - merged[-1][1] < min_pause:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    merged = [(round(s, 2), round(e, 2)) for s, e in merged if e - s >= min_speech]
    return merged, {"noise_floor_db": round(floor, 1), "threshold_db": round(thr, 1), "speech_db": round(peak, 1)}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _whisper_server(language):
    """Keep large-v3 loaded while we transcribe many short speech units."""
    port = _free_port()
    proc = subprocess.Popen(
        [media.WHISPER_BIN.replace("whisper-cli", "whisper-server"), "-m", media.WHISPER_MODEL,
         "-l", language, "--host", "127.0.0.1", "--port", str(port), "-t", "8", "-nf"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/inference"
    try:
        for _ in range(600):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                if proc.poll() is not None:
                    raise RuntimeError("whisper-server failed to start")
                time.sleep(0.1)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _slice_wav(samples, start, end, pad=0.15):
    a, b = max(0, int((start - pad) * SR)), min(len(samples), int((end + pad) * SR))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples[a:b].tobytes())
    return buf.getvalue()


def _transcribe_units(path, islands, language):
    """Transcribe each speech island on its own so Thai text never bleeds across a cut (cached)."""
    cache = media.CACHE / f"{media._cache_key(path, 'units')}-{language}.json"
    done = json.loads(cache.read_text()) if cache.exists() else {}
    todo = [(s, e) for s, e in islands if f"{s:.2f}-{e:.2f}" not in done]
    if todo:
        with wave.open(str(_wav(path))) as w:
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        with _whisper_server(language) as url, httpx.Client(timeout=120) as client:
            for s, e in todo:
                # no prompt: context makes Whisper copy the previous line, which hides retakes
                r = client.post(url, files={"file": ("u.wav", _slice_wav(samples, s, e), "audio/wav")},
                                data={"response_format": "json", "temperature": "0", "language": language})
                r.raise_for_status()
                done[f"{s:.2f}-{e:.2f}"] = r.json().get("text", "").strip()
        cache.write_text(json.dumps(done, ensure_ascii=False))
    return [done[f"{s:.2f}-{e:.2f}"] for s, e in islands]


def _norm(text):
    return "".join(ch for ch in text if ch.isalnum())


def analyze(path, language="th", min_pause=0.25, threshold_db=None):
    """Return speech units (speech between pauses) with text, pause lengths and editing hints."""
    path = str(Path(path).expanduser().resolve())
    islands, levels = detect_speech(path, min_pause=min_pause, threshold_db=threshold_db)
    texts = _transcribe_units(path, islands, language)
    units = []
    for i, (s, e) in enumerate(islands):
        text = texts[i]
        unit = {"id": i + 1, "start": s, "end": e, "dur": round(e - s, 2),
                "pause_before": round(s - islands[i - 1][1], 2) if i else round(s, 2), "text": text}
        flags = []
        if not text:
            flags.append("no_words")  # breath, noise, laugh, or a word Whisper missed
        elif _norm(text) in FILLERS:
            flags.append("filler")
        units.append((unit, flags))
    # retake hints: a later unit that repeats the start of an earlier one (within the last few units)
    for j, (u, flags) in enumerate(units):
        a = _norm(u["text"])
        if len(a) < 4:
            continue
        for k in range(max(0, j - 6), j):
            b = _norm(units[k][0]["text"])
            if len(b) < 4:
                continue
            n = min(len(a), len(b), 20)
            head = difflib.SequenceMatcher(None, a[:n], b[:n]).ratio()
            whole = difflib.SequenceMatcher(None, a, b).ratio()
            if head > 0.75 or whole > 0.6:
                units[k][1].append(f"retaken_by:{u['id']}")
                flags.append(f"retake_of:{units[k][0]['id']}")
    out = []
    for u, flags in units:
        if flags:
            u["flags"] = flags
        out.append(u)
    info = media.probe(path)
    # continuous transcript: better word accuracy (more context) but coarse timing - use to read units
    reference = [f"[{x['start']:.1f}-{x['end']:.1f}] {x['text']}" for x in media.transcribe(path, language, max_len=60)]
    return {"path": path, "duration": info["duration"], "fps": info.get("fps"), "levels": levels,
            "units": out, "reference_transcript": reference}


def refine(path, start, end, language="th", min_pause=0.06):
    """Zoom into a time range with a much smaller pause threshold to find word/syllable boundaries."""
    path = str(Path(path).expanduser().resolve())
    islands, _ = detect_speech(path, min_pause=min_pause, min_speech=0.05)
    sub = [(max(s, start), min(e, end)) for s, e in islands if e > start and s < end]
    texts = _transcribe_units(path, sub, language)
    return [{"start": s, "end": e, "text": t} for (s, e), t in zip(sub, texts)]


# ---------- rough-cut layout ----------

PACE = {  # seconds of handle kept before / after each kept piece of speech
    "tight": (0.05, 0.10),
    "natural": (0.10, 0.25),
    "relaxed": (0.15, 0.45),
}


def plan_layout(segments, fps, pace="natural"):
    """Turn editorial segments into frame-exact audio + video ranges.

    segments: [{"path", "start", "end",                 # speech range in source seconds
                "head": s?, "tail": s?,                 # optional handle overrides
                "cut": "straight" | "L" | "J",          # transition INTO THE NEXT segment
                "cut_frames": int}]                     # J/L overlap, default 6
    Audio is laid out contiguously; J/L cuts only move the picture edit:
      L = picture cuts early (outgoing voice continues over the incoming shot)
      J = picture cuts late  (incoming voice starts under the outgoing shot)
    """
    fr = lambda sec: int(round(sec * fps))  # noqa: E731
    head_d, tail_d = PACE.get(pace, PACE["natural"])
    speech = {}
    warnings = []
    pieces = []
    for i, seg in enumerate(segments):
        p = str(Path(seg["path"]).expanduser().resolve())
        if p not in speech:
            speech[p] = (detect_speech(p)[0], media.probe(p)["duration"])
        islands, dur = speech[p]
        s, e = float(seg["start"]), float(seg["end"])
        head = float(seg.get("head", head_d))
        tail = float(seg.get("tail", tail_d))
        # never let a handle reach into speech that was not selected
        prev_end = max([ie for is_, ie in islands if ie <= s + 0.02] or [0.0])
        next_start = min([is_ for is_, ie in islands if is_ >= e - 0.02] or [dur])
        a_in = min(s, max(0.0, s - head, prev_end + 0.02))
        a_out = max(e, min(dur, e + tail, next_start - 0.02))
        pieces.append({"path": p, "a_in": fr(a_in), "a_out": fr(a_out), "dur_f": fr(dur),
                       "prev_speech_f": fr(prev_end), "next_speech_f": fr(next_start),
                       "speech_in_f": fr(s), "speech_out_f": fr(e)})
    # picture shift at each cut (frames, negative = earlier)
    shifts = [0] * (len(pieces) + 1)
    for i, seg in enumerate(segments[:-1]):
        kind = (seg.get("cut") or "straight").upper()
        n = int(seg.get("cut_frames", 6))
        if kind == "L":
            shifts[i + 1] = -n
        elif kind == "J":
            shifts[i + 1] = n
    video, audio, rec = [], [], 0
    for i, pc in enumerate(pieces):
        s_in, s_out = shifts[i], shifts[i + 1]
        v_in, v_out = pc["a_in"] + s_in, pc["a_out"] + s_out
        # keep the picture inside the source and away from the speaker talking in another take
        if v_in < 0 or (s_in < 0 and v_in < pc["prev_speech_f"]):
            warnings.append(f"segment {i + 1}: L-cut needs {-s_in} frames before the speech; mouth may move")
        if v_out > pc["dur_f"]:
            warnings.append(f"segment {i + 1}: J-cut runs past the end of the clip; shortened")
            v_out = pc["dur_f"]
        if s_out > 0 and v_out > pc["next_speech_f"]:
            warnings.append(f"segment {i + 1}: J-cut picture runs into unselected speech (mouth moves)")
        a_len = pc["a_out"] - pc["a_in"]
        audio.append({"path": pc["path"], "src_in": pc["a_in"], "src_out": pc["a_out"], "record": rec})
        video.append({"path": pc["path"], "src_in": max(0, v_in), "src_out": v_out, "record": rec + s_in})
        rec += a_len
    return {"fps": fps, "frames": rec, "duration": round(rec / fps, 2), "video": video, "audio": audio,
            "warnings": warnings}


def preview_audio(layout, out_path, language="th"):
    """Concatenate the planned audio into one wav and transcribe it, to check the cut reads naturally."""
    fps = layout["fps"]
    chunks, cache = [], {}
    for a in layout["audio"]:
        if a["path"] not in cache:
            with wave.open(str(_wav(a["path"]))) as w:
                cache[a["path"]] = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        x = cache[a["path"]][int(a["src_in"] / fps * SR): int(a["src_out"] / fps * SR)].astype(np.float32)
        fade = min(int(0.005 * SR), len(x) // 2)  # 5 ms fades so joins do not click
        if fade:
            ramp = np.linspace(0, 1, fade)
            x[:fade] *= ramp
            x[-fade:] *= ramp[::-1]
        chunks.append(x.astype(np.int16))
    out_path = Path(out_path).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.concatenate(chunks).tobytes() if chunks else b"")
    segs = media.transcribe(str(out_path), language, max_len=60)
    return {"preview_wav": str(out_path), "duration": layout["duration"],
            "transcript": [f"[{s['start']:.1f}] {s['text']}" for s in segs]}
