"""Thai captions: draft from speech, split into readable chunks, export SRT, drive the overlay renderer."""

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from . import media, speech

ROOT = Path(__file__).resolve().parent.parent
OUT_ROOT = media.CACHE / "captions"

# particles / repeat marks that must stay on the same caption as the word before them
CLINGERS = {"ครับ", "คะ", "ค่ะ", "คับ", "นะ", "น่ะ", "นะครับ", "นะคะ", "จ้ะ", "จ้า", "ล่ะ", "ๆ", "เนี่ย", "เนี้ย",
            "หรอก", "สิ", "ซิ", "แหละ", "มั้ย", "ไหม", "หรือเปล่า", "ป่ะ"}

MODES = {
    # max visible chars per line, max lines, min/max caption seconds
    "long": {"max_chars": 38, "max_lines": 2, "min_dur": 0.9, "max_dur": 6.0},
    "short": {"max_chars": 16, "max_lines": 2, "min_dur": 0.35, "max_dur": 2.5},
}


def visible_len(text):
    """Characters that take horizontal space (Thai upper/lower vowels and tone marks do not)."""
    return sum(1 for ch in text if unicodedata.category(ch) not in ("Mn", "Me", "Cf"))


def tokenize(text):
    from pythainlp.tokenize import word_tokenize
    out = []
    for tok in word_tokenize(text, engine="newmm", keep_whitespace=True):
        if tok.isspace():
            if out and out[-1] != " ":
                out.append(" ")
        else:
            out.append(tok)
    return out


def _join(tokens):
    return "".join(tokens).strip()


def _is_clinger(tokens, i):
    return tokens[i] in CLINGERS or any("".join(tokens[i:i + 2]).startswith(c) and len(c) > 1 for c in ("นะครับ", "นะคะ"))


def chunk_text(text, max_chars):
    """Split text into balanced caption chunks <= max_chars.

    Breaks only between words, prefers phrase gaps (spaces), never starts a chunk with a particle.
    Dynamic programming over break points: cost = squared slack + penalty for breaking inside a phrase.
    """
    tokens = [t for t in tokenize(text)]
    n = len(tokens)
    if not n:
        return []
    lens = [visible_len(t) for t in tokens]
    best = [0.0] + [float("inf")] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        width = 0
        for i in range(j - 1, -1, -1):
            width += lens[i]
            chunk_len = visible_len("".join(tokens[i:j]).strip())
            if chunk_len > max_chars and i < j - 1:
                break
            if i > 0 and (_is_clinger(tokens, i) or (tokens[i].isspace() and i + 1 < n and _is_clinger(tokens, i + 1))):
                continue
            at_gap = i == 0 or tokens[i].isspace() or tokens[i - 1].isspace()
            cost = best[i] + (max_chars - chunk_len) ** 2 + (0 if at_gap else 30)
            if cost < best[j]:
                best[j], back[j] = cost, i
    chunks, j = [], n
    while j > 0:
        chunks.append("".join(tokens[back[j]:j]).strip())
        j = back[j]
    return [c for c in reversed(chunks) if c]


def split_lines(text, max_chars, max_lines):
    """Break one caption into at most max_lines balanced lines."""
    if visible_len(text) <= max_chars or max_lines == 1:
        return [text]
    tokens = tokenize(text)
    best, best_score = [text], None
    for i in range(1, len(tokens)):
        if tokens[i] in CLINGERS:
            continue
        a, b = _join(tokens[:i]), _join(tokens[i:])
        if not a or not b:
            continue
        score = max(visible_len(a), visible_len(b))
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best


# ---------- styles ----------
# size / stroke are fractions: size of frame width (vertical) or height (horizontal), stroke of font size
STYLES = {
    "clean": {  # long-form: calm bottom-centre subtitles
        "desc": "ซับคลิปยาว กลางล่างจอ ตัวขาวขอบดำ+เงา ไม่มีแอนิเมชัน",
        "font": "NotoSansThai-SemiBold", "size": 0.048, "color": "#FFFFFF", "stroke_color": "#000000",
        "stroke": 0.07, "shadow": True, "highlight": None, "highlight_color": "#FFFFFF", "emph_color": "#FFE066",
        "emph_scale": 1.0, "enter": "none", "word_pop": False, "y": 0.9, "max_width": 0.9,
        "max_chars": 38, "max_lines": 2, "line_spacing": 1.05},
    "pop": {  # short-form default
        "desc": "คลิปสั้น ตัวหนาขอบดำหนา ทั้งซับเด้งเข้า คำที่กำลังพูดเป็นสีเหลืองและขยาย คำเน้นเป็นสีแดง",
        "font": "Kanit-ExtraBold", "size": 0.088, "color": "#FFFFFF", "stroke_color": "#000000",
        "stroke": 0.13, "shadow": True, "highlight": "word", "highlight_color": "#FFD60A",
        "emph_color": "#FF4D5E", "emph_scale": 1.15, "enter": "pop", "word_pop": True, "y": 0.68,
        "max_width": 0.84, "max_chars": 14, "max_lines": 2, "line_spacing": 1.0},
    "karaoke": {
        "desc": "คลิปสั้น คำที่ยังไม่พูดเป็นสีเทา สว่างขึ้นตามเสียง สไลด์ขึ้น คำเน้นเป็นสีเขียว",
        "font": "Kanit-Bold", "size": 0.08, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.12,
        "shadow": True, "highlight": "karaoke", "highlight_color": "#FFFFFF", "emph_color": "#39D98A",
        "emph_scale": 1.12, "enter": "slide", "word_pop": True, "y": 0.68, "max_width": 0.84,
        "max_chars": 16, "max_lines": 2, "line_spacing": 1.0},
    "box": {
        "desc": "คลิปสั้น ตัวอักษรบนกล่องสีสลับทุกซับ เด้งเข้า คำเน้นสีเหลืองอ่อน",
        "font": "Prompt-Bold", "size": 0.075, "color": "#FFFFFF", "stroke_color": "#000000", "stroke": 0.0,
        "shadow": False, "highlight": None, "highlight_color": "#FFFFFF", "emph_color": "#FFF3A3",
        "emph_scale": 1.1, "enter": "pop", "word_pop": False, "box": True,
        "box_colors": ["#FF3B5C", "#2F80FF", "#FFB800", "#00B57A", "#8E5CFF"],
        "y": 0.7, "max_width": 0.82, "max_chars": 14, "max_lines": 2, "line_spacing": 1.12},
}


def resolve_style(name, W, H, overrides=None):
    st = dict(STYLES[name])
    st.update({k: v for k, v in (overrides or {}).items() if v is not None})
    base = W if H > W else H
    st["size"] = st["size"] * base * float(st.pop("size_scale", 1.0))
    st["stroke"] = st["stroke"] * st["size"]
    st.pop("desc", None)
    return st


# ---------- drafting ----------

def _timeline_segments(timeline):
    state_file = media.CACHE / "timelines.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    segs = state.get(timeline)
    if not segs:
        raise ValueError(f"ไม่รู้ว่า timeline '{timeline}' ใช้ช่วงไหนของไฟล์ไหน (ต้องสร้างด้วย build_rough_cut / "
                         f"build_timeline) - หรือ render timeline เป็นไฟล์แล้วใช้ source_path แทน")
    return segs


def draft(timeline=None, source_path=None, mode="short", max_chars=None, language="th"):
    """Caption draft in timeline seconds from the speech actually used on the timeline."""
    cfg = dict(MODES[mode])
    if max_chars:
        cfg["max_chars"] = max_chars
    if source_path:
        p = str(Path(source_path).expanduser().resolve())
        segs = [{"path": p, "start": 0.0, "end": media.probe(p)["duration"]}]
    else:
        segs = _timeline_segments(timeline)
    per_caption = cfg["max_chars"]  # one comfortable line per caption; extra lines only for edited text
    caps, offset = [], 0.0
    for seg in segs:
        islands, _ = speech.detect_speech(seg["path"], min_pause=0.15)
        inside = [(max(s, seg["start"]), min(e, seg["end"])) for s, e in islands
                  if e > seg["start"] + 0.05 and s < seg["end"] - 0.05]
        texts = speech._transcribe_units(seg["path"], inside, language)
        for (s, e), text in zip(inside, texts):
            pieces = chunk_text(text, per_caption) if text else []
            total = sum(visible_len(p) for p in pieces) or 1
            t = s
            for piece in pieces:
                dur = (e - s) * visible_len(piece) / total
                caps.append({"start": round(offset + t - seg["start"], 3),
                             "end": round(offset + t + dur - seg["start"], 3), "text": piece})
                t += dur
        offset += seg["end"] - seg["start"]
    return _retime(_merge_particles(caps, cfg["max_chars"]), cfg)


def _merge_particles(caps, max_chars):
    """A caption that is only a particle (speaker paused before "นะครับ") joins the caption before it."""
    out = []
    for c in caps:
        toks = [t for t in tokenize(c["text"]) if not t.isspace()]
        lone = toks and all(t in CLINGERS or t in ("นะ", "ครับ", "ค่ะ") for t in toks)
        if out and lone and c["start"] - out[-1]["end"] < 0.6 \
                and visible_len(out[-1]["text"] + c["text"]) <= max_chars + 6:
            out[-1]["text"] += c["text"]
            out[-1]["end"] = c["end"]
        else:
            out.append(dict(c))
    return out


def _retime(caps, cfg):
    """Enforce min/max durations and close tiny gaps so captions do not flicker."""
    caps = sorted(caps, key=lambda c: c["start"])
    for i, c in enumerate(caps):
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else None
        if c["end"] - c["start"] < cfg["min_dur"]:
            c["end"] = c["start"] + cfg["min_dur"]
        if nxt is not None:
            if nxt - c["end"] < 0.25:  # small gap: hold the caption until the next one
                c["end"] = nxt
            c["end"] = min(c["end"], nxt)
        c["start"], c["end"] = round(c["start"], 3), round(c["end"], 3)
    for i, c in enumerate(caps, 1):
        c["id"] = i
    return caps


# ---------- outputs ----------

def _ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def strip_markup(text):
    return re.sub(r"\*([^*]+)\*", r"\1", text)


def write_srt(caps, out_path, max_chars, max_lines=2):
    lines = []
    for i, c in enumerate(caps, 1):
        body = "\n".join(split_lines(strip_markup(c["text"]), max_chars, max_lines))
        lines += [str(i), f"{_ts(c['start'])} --> {_ts(c['end'])}", body, ""]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


def frame_at(timeline, t, out_path):
    """Grab the source frame shown at timeline second t (for previews on real footage)."""
    offset = 0.0
    for seg in _timeline_segments(timeline):
        length = seg["end"] - seg["start"]
        if t < offset + length:
            src_t = seg["start"] + (t - offset)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{src_t:.3f}", "-i", seg["path"],
                            "-frames:v", "1", str(out_path)], check=True)
            return str(out_path)
        offset += length
    raise ValueError(f"time {t}s is past the end of timeline '{timeline}'")


def run_renderer(job, module="davinci_bot.caption_render"):
    """Run a frame renderer in a subprocess (Pillow needs libraqm on the dylib path for Thai shaping)."""
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    job_file = OUT_ROOT / f"job-{int(time.time() * 1000)}.json"
    job_file.write_text(json.dumps(job, ensure_ascii=False))
    env = dict(os.environ)
    env["DYLD_LIBRARY_PATH"] = ":".join(filter(None, ["/opt/homebrew/lib", env.get("DYLD_LIBRARY_PATH")]))
    proc = subprocess.run([sys.executable, "-m", module, str(job_file)],
                          cwd=ROOT, env=env, capture_output=True, text=True)
    job_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"caption renderer failed:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout)
