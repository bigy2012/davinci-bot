"""Media analysis that runs outside Resolve: probing, Whisper transcription, SRT building."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CACHE = Path(os.environ.get("DAVINCI_BOT_CACHE", "~/.cache/davinci-bot")).expanduser()
WHISPER_BIN = os.environ.get("WHISPER_BIN") or shutil.which("whisper-cli") or "whisper-cli"
WHISPER_MODEL = os.environ.get(
    "WHISPER_MODEL", str(Path("~/.cache/hyperframes/whisper/models/ggml-large-v3.bin").expanduser())
)
VAD_MODEL = os.environ.get(
    "WHISPER_VAD_MODEL", str(Path("~/.cache/hyperframes/whisper/models/ggml-silero-v5.1.2.bin").expanduser())
)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".mxf", ".avi", ".m4v", ".webm", ".mts", ".braw"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac"}


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    info = {"path": path, "duration": round(float(data["format"].get("duration", 0)), 3)}
    for s in data["streams"]:
        if s["codec_type"] == "video" and "width" not in info:
            num, den = (s.get("avg_frame_rate") or "0/1").split("/")
            info.update(width=s["width"], height=s["height"],
                        fps=round(float(num) / float(den), 3) if float(den) else None)
        if s["codec_type"] == "audio":
            info["has_audio"] = True
    return info


def list_folder(folder, recursive=True):
    folder = Path(folder).expanduser()
    files = folder.rglob("*") if recursive else folder.iterdir()
    out = []
    for f in sorted(files):
        ext = f.suffix.lower()
        if ext in VIDEO_EXT:
            try:
                info = probe(str(f))
            except Exception as e:
                info = {"path": str(f), "error": str(e)}
            info["kind"] = "video"
            out.append(info)
        elif ext in IMAGE_EXT:
            out.append({"path": str(f), "kind": "image"})
        elif ext in AUDIO_EXT:
            out.append({"path": str(f), "kind": "audio"})
    return out


def _cache_key(path, max_len):
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime}|{max_len}|{WHISPER_MODEL}"
    return hashlib.sha1(raw.encode()).hexdigest()


def transcribe(path, language="th", max_len=0):
    """Return [{"start": sec, "end": sec, "text": str}] for the file's audio (cached)."""
    path = os.path.abspath(os.path.expanduser(path))
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{_cache_key(path, max_len)}-{language}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "a.wav")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", wav], check=True)
        cmd = [WHISPER_BIN, "-m", WHISPER_MODEL, "-l", language, "-t", str(os.cpu_count() or 4),
               "-oj", "-of", os.path.join(tmp, "out"), "-f", wav, "-np"]
        if max_len:
            cmd += ["-ml", str(max_len), "-sow"]
        if os.path.exists(VAD_MODEL):
            cmd += ["--vad", "-vm", VAD_MODEL]
        subprocess.run(cmd, check=True, capture_output=True)
        data = json.loads(Path(tmp, "out.json").read_text(encoding="utf-8", errors="replace"))
    segs = []
    for s in data.get("transcription", []):
        text = s["text"].strip()
        if text:
            segs.append({"start": s["offsets"]["from"] / 1000, "end": s["offsets"]["to"] / 1000, "text": text})
    cache_file.write_text(json.dumps(segs, ensure_ascii=False))
    return segs
