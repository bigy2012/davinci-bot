"""Music & SFX: library analysis (sync points, loudness, tempo, energy), voice reference, ducked music beds."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import media

SR = 22050
AUDIO_EXT = {".mp3", ".wav", ".aif", ".aiff", ".m4a", ".flac", ".ogg", ".caf"}
LIB_CONFIG = Path(__file__).resolve().parent.parent / "library.json"
DEFAULT_LIB = {"sfx": ["~/Desktop/Agent-M/SFX"],
               "music": ["~/Desktop/Agent-M/MUSIC", "~/Desktop/Agent-M/เนื้อหา หรือ ท่อนเดือด.mp3",
                         "~/Desktop/Agent-M/เนื้อหา หรือท่อนตื่นเต้น.mp3"]}

# filename keywords -> role, used as a hint next to the measured shape
ROLES = [
    ("impact", ["boom", "hit", "impact", "punch", "slam", "thud", "bass-drop", "vine", "braam"]),
    ("transition", ["whoosh", "swoosh", "swish", "swipe", "transition", "riser", "sweep", "glitch"]),
    ("comedy", ["boing", "spring", "fail", "trumpet", "wrong", "bruh", "nooo", "villager", "among", "creeper",
                "bad-to-the-bone", "honk", "squeak", "eating", "cartoon"]),
    ("positive", ["success", "victory", "fanfare", "ding", "correct", "benar", "bell", "win", "cash"]),
    ("ui", ["click", "pop", "bubble", "typing", "keyboard", "shutter", "camera", "notification", "minimal"]),
    ("tension", ["suspense", "tension", "drone", "heartbeat", "tick", "clock", "riser", "dramatic", "horror"]),
    ("signal", ["radio", "static", "morse", "sonar", "modem", "dial-up", "blip", "beep", "computer"]),
]


def library_paths():
    cfg = json.loads(LIB_CONFIG.read_text()) if LIB_CONFIG.exists() else DEFAULT_LIB
    return {k: [str(Path(p).expanduser()) for p in cfg.get(k, [])] for k in ("sfx", "music")}


def _files(paths):
    out = []
    for p in paths:
        p = Path(p)
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            out.append(p)
        elif p.is_dir():
            out += sorted(f for f in p.rglob("*") if f.suffix.lower() in AUDIO_EXT)
    return out


def load(path, sr=SR, mono=True):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1" if mono else "2", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    return x if mono else x.reshape(-1, 2)


def _env(x, hop):
    n = len(x) // hop
    if n == 0:
        return np.zeros(1)
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1) + 1e-12)


def _db(v):
    return float(20 * np.log10(max(v, 1e-9)))


def analyze_sfx(path):
    x = load(path)
    hop = int(SR * 0.01)
    env = _env(x, hop)
    dur = len(x) / SR
    peak_i = int(np.argmax(env))
    peak_t = peak_i * 0.01
    floor = env.max() * 0.1
    active = np.where(env > floor)[0]
    start_t = active[0] * 0.01 if len(active) else 0.0
    end_t = (active[-1] + 1) * 0.01 if len(active) else dur
    # loudness ~ RMS of the loudest 300 ms
    w = max(1, int(0.3 / 0.01))
    loud = _db(float(np.sqrt(np.convolve(env ** 2, np.ones(w) / w, mode="valid").max()))) if len(env) >= w \
        else _db(float(env.max()))
    rise = peak_t - start_t
    if dur < 0.6 or rise < 0.06 and (end_t - peak_t) < 0.8:
        shape = "hit"          # instant transient: lands exactly on the beat
    elif rise > 0.5 * (end_t - start_t):
        shape = "swell"        # builds up to its peak (riser / whoosh-in): end it on the moment
    elif end_t - start_t > 2.5:
        shape = "long"         # musical sting / sustained effect
    else:
        shape = "short"
    spec = np.abs(np.fft.rfft(x[: SR * 2] if len(x) > 1024 else np.pad(x, (0, 1024))))
    freqs = np.fft.rfftfreq(len(x[: SR * 2]) if len(x) > 1024 else len(x) + 1024, 1 / SR)
    centroid = float((spec * freqs).sum() / (spec.sum() + 1e-9))
    name = Path(path).stem.lower()
    roles = [r for r, keys in ROLES if any(k in name for k in keys)]
    return {"path": str(path), "name": Path(path).stem, "dur": round(dur, 2), "sync": round(peak_t, 2),
            "sound_start": round(start_t, 2), "sound_end": round(end_t, 2), "shape": shape,
            "loud_db": round(loud, 1), "tone": "bright" if centroid > 3000 else "dark" if centroid < 900 else "mid",
            "roles": roles or ["other"]}


def _tempo(env, fps):
    """Tempo + beat phase from an onset envelope (autocorrelation, 60-180 BPM)."""
    onset = np.maximum(0, np.diff(env, prepend=env[0]))
    onset = onset - onset.mean()
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 60)
    if hi >= len(ac):
        return None, 0.0
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60 * fps / lag
    phases = [onset[p::lag].sum() for p in range(lag)]
    return round(bpm, 1), int(np.argmax(phases)) / fps


def analyze_music(path):
    x = load(path)
    dur = len(x) / SR
    hop = int(SR * 0.02)
    env = _env(x, hop)
    bpm, phase = _tempo(env, 50)
    # energy per 2 s block, relative to the track's loudest block
    blk = 100
    blocks = [float(np.sqrt((env[i:i + blk] ** 2).mean())) for i in range(0, len(env), blk)]
    top = max(blocks) or 1
    rel = [b / top for b in blocks]
    bars = "".join(" ▁▂▃▄▅▆▇█"[min(8, int(r * 8.99))] for r in rel)
    jumps = [round(i * 2.0, 1) for i in range(1, len(rel)) if rel[i] - rel[i - 1] > 0.25]
    quiet = [round(i * 2.0, 1) for i in range(1, len(rel)) if rel[i - 1] - rel[i] > 0.25]
    return {"path": str(path), "name": Path(path).stem, "dur": round(dur, 1), "bpm": bpm,
            "first_beat": round(phase, 2), "beat_sec": round(60 / bpm, 3) if bpm else None,
            "energy_2s": bars, "energy_jumps_at": jumps, "drops_to_quiet_at": quiet,
            "loud_db": round(_db(float(np.sqrt((x ** 2).mean()))), 1)}


def _cached(kind, path, fn):
    st = os.stat(path)
    key = f"{kind}|{path}|{st.st_size}|{st.st_mtime}|v2"
    cache = media.CACHE / "audio_index.json"
    idx = json.loads(cache.read_text()) if cache.exists() else {}
    if key not in idx:
        idx[key] = fn(path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(idx, ensure_ascii=False))
    return idx[key]


def scan_library():
    paths = library_paths()
    sfx = [_cached("sfx", str(f), analyze_sfx) for f in _files(paths["sfx"])]
    music = [_cached("music", str(f), analyze_music) for f in _files(paths["music"])]
    return {"config": str(LIB_CONFIG), "paths": paths, "sfx": sfx, "music": music}


def find(name_or_path, kind):
    p = Path(name_or_path).expanduser()
    if p.exists():
        return _cached(kind, str(p.resolve()), analyze_sfx if kind == "sfx" else analyze_music)
    for item in scan_library()[kind]:
        if item["name"] == name_or_path or Path(item["path"]).name == name_or_path:
            return item
    raise ValueError(f"{kind} not found in library: {name_or_path}")


# ---------- timeline voice & ducking ----------

def voice_wav(segments, out_path, sr=48000):
    """The dialogue exactly as laid on the timeline (from saved segments), for ducking and level checks."""
    inputs, filters = [], []
    for i, s in enumerate(segments):
        inputs += ["-i", s["path"]]
        filters.append(f"[{i}:a]atrim={s['start']:.4f}:{s['end']:.4f},asetpts=PTS-STARTPTS,"
                       f"aformat=sample_rates={sr}:channel_layouts=mono[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=0:a=1[out]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters), "-map", "[out]",
                    str(out_path)], check=True)
    return str(out_path)


def speech_level_db(wav_path):
    x = load(wav_path)
    env = _env(x, int(SR * 0.05))
    voiced = env[env > env.max() * 0.1]
    return _db(float(np.sqrt((voiced ** 2).mean()))) if len(voiced) else -20.0


def ducked_bed(music_path, src_start, length, voice_path, voice_offset, out_path, duck_db=10.0,
               fade_in=0.5, fade_out=1.5):
    """Music section (looped if needed) with sidechain ducking from the timeline voice."""
    duck_ratio = max(2.0, min(20.0, duck_db / 2))
    with tempfile.TemporaryDirectory():
        flt = (f"[0:a]atrim=start={src_start:.3f},asetpts=PTS-STARTPTS,aloop=loop=-1:size=2e9,"
               f"atrim=duration={length:.3f},aformat=sample_rates=48000:channel_layouts=stereo,"
               f"afade=t=in:d={fade_in:.2f},afade=t=out:st={max(0, length - fade_out):.3f}:d={fade_out:.2f}[m];"
               f"[1:a]atrim=start={voice_offset:.3f}:duration={length:.3f},asetpts=PTS-STARTPTS,"
               f"aformat=sample_rates=48000:channel_layouts=stereo,apad=whole_dur={length:.3f}[v];"
               f"[m][v]sidechaincompress=threshold=0.02:ratio={duck_ratio}:attack=30:release=450:makeup=1[out]")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-i", voice_path, "-filter_complex", flt,
                        "-map", "[out]", "-c:a", "pcm_s24le", str(out_path)], check=True)
    return str(out_path)


def mix_preview(voice_path, layers, out_path, length):
    """Quick stereo mix (voice + music beds + SFX at their gains) for listening before placing."""
    inputs, chains, labels = ["-i", voice_path], [], ["[0:a]"]
    for i, l in enumerate(layers, 1):
        inputs += ["-i", l["path"]]
        delay = int(l["at"] * 1000)
        trim = f"atrim=start={l.get('src_in', 0):.3f}" + (f":end={l['src_out']:.3f}" if l.get("src_out") else "")
        chains.append(f"[{i}:a]{trim},asetpts=PTS-STARTPTS,volume={l['gain_db']}dB,"
                      f"adelay={delay}|{delay},aformat=channel_layouts=stereo[l{i}]")
        labels.append(f"[l{i}]")
    chains.append(f"[0:a]aformat=channel_layouts=stereo[v]")
    labels[0] = "[v]"
    chains.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,atrim=duration={length:.3f},"
                  "alimiter=limit=0.95[out]")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(chains), "-map", "[out]",
                    "-c:a", "libmp3lame", "-q:a", "3", str(out_path)], check=True)
    return str(out_path)
