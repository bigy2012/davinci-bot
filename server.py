"""DaVinci Bot - MCP server that lets Claude edit in DaVinci Resolve (free edition).

Resolve-side work goes through the Lua bridge (resolve_script/), started by the user from
Workspace > Scripts > DaVinci Bot Bridge. Media analysis (ffprobe, Whisper) runs here.
"""

import json
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from davinci_bot import audio, broll, captions, creator, media, prompter, speech
from davinci_bot.bridge import call

INSTRUCTIONS = """\
You edit videos in the user's DaVinci Resolve. The user speaks Thai; reply in Thai.

Hard rule - the user decides every job. Before touching the timeline:
1. Ask for (or confirm) the edit brief: format (short vertical 9:16 / horizontal 16:9 shot),
   target length, source files, script (if ordering by script), B-roll style + folder,
   subtitles yes/no, transitions yes/no, timeline name. Use the `edit_brief` prompt checklist.
   Never assume a value the user has not given for this job; ask.
2. Analyse: probe_media / list_media_folder, transcribe sources to find exact spoken ranges.
3. Present the cut plan as a table (order, file, start-end, text/reason, B-roll at which second)
   and wait for the user's explicit approval or changes.
4. Only then call build_timeline / add_broll / render_captions / etc. Always build into a NEW
   timeline name so the user's existing work is never overwritten.
Times are seconds. Cut on segment boundaries from the transcript, adding ~0.1s of padding.
If a tool says the bridge is not running, ask the user to open Resolve and click
Workspace > Scripts > DaVinci Bot Bridge, then retry.
16:9 B-roll on a 9:16 timeline needs fill_zoom=3.16 to fill the frame (only if the user wants fill).
"""

mcp = MCPServer("davinci-bot", instructions=INSTRUCTIONS)
STATE = media.CACHE / "timelines.json"


def _load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_segments(timeline, segments):
    state = _load_state()
    state[timeline] = segments
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _abs(p):
    return str(Path(p).expanduser().resolve())


# ---------- prompts ----------

@mcp.prompt()
def edit_brief() -> str:
    """เช็คลิสต์ก่อนเริ่มตัดต่อทุกครั้ง"""
    return """ก่อนเริ่มตัด ช่วยกำหนดงานนี้ให้ครบ (ข้อไหนไม่ใช้ให้บอก "ไม่ใช้"):
1. รูปแบบ: คลิปสั้นแนวตั้ง 9:16 (1080x1920) / คลิปแนวนอน 16:9 (1920x1080) / อื่นๆ
2. ความยาวเป้าหมาย (เช่น 30 วิ, 60 วิ, 3 นาที)
3. ไฟล์ต้นฉบับ (path ไฟล์หรือโฟลเดอร์)
4. สคริปต์สำหรับเรียงลำดับ (วางสคริปต์มา หรือ "ให้เลือกช่วงที่ดีที่สุดเอง")
5. B-roll: สไตล์ที่ต้องการ, โฟลเดอร์ B-roll, ความถี่/ความยาวต่อช็อต, ซูมให้เต็มจอหรือไม่
6. ซับไตเติล: ทำ/ไม่ทำ, ภาษา
7. Transition ระหว่างช็อต: ไม่มี (ตัดชน) / Cross Dissolve / อื่นๆ
8. ชื่อ timeline ใหม่
9. Export เลยไหม (ที่ไหน, preset อะไร)"""


# ---------- Resolve status ----------

@mcp.tool()
def resolve_status() -> dict:
    """ตรวจว่า bridge ใน Resolve ทำงานอยู่ และดูโปรเจกต์/timeline ปัจจุบัน"""
    return call("ping")


@mcp.tool()
def list_projects() -> list:
    """รายชื่อโปรเจกต์ใน Project Manager (โฟลเดอร์ปัจจุบัน)"""
    return call("list_projects")


@mcp.tool()
def open_project(name: str, create_if_missing: bool = False) -> dict:
    """เปิดโปรเจกต์ตามชื่อ (สร้างใหม่ได้ถ้า create_if_missing=True)"""
    return call("open_project", name=name, create_if_missing=create_if_missing)


@mcp.tool()
def save_project() -> bool:
    """บันทึกโปรเจกต์ปัจจุบัน"""
    return call("save_project")


# ---------- media analysis ----------

@mcp.tool()
def probe_media(path: str) -> dict:
    """ดูความยาว ขนาด fps ของไฟล์วิดีโอ"""
    return media.probe(_abs(path))


@mcp.tool()
def list_media_folder(folder: str, recursive: bool = True) -> list:
    """แสดงไฟล์วิดีโอ/รูป/เสียงในโฟลเดอร์ พร้อมความยาวและขนาด (ใช้เลือก B-roll หรือหาไฟล์ต้นฉบับ)"""
    return media.list_folder(folder, recursive)


@mcp.tool()
def transcribe(path: str, language: str = "th", max_chars: int = 40) -> list:
    """ถอดเสียงไฟล์ด้วย Whisper (ในเครื่อง) ได้ช่วงเวลา start/end เป็นวินาทีพร้อมข้อความ.
    ใช้หาจุดตัดให้ตรงสคริปต์ ไฟล์ยาวครั้งแรกอาจใช้เวลาหลายนาที ครั้งต่อไปใช้ cache.
    max_chars = ความยาวต่อท่อน (ยิ่งน้อยยิ่งได้จุดตัดละเอียด)"""
    return media.transcribe(_abs(path), language, max_len=max_chars)


@mcp.tool()
def export_frame_previews(path: str, times: list[float], width: int = 480) -> list[str]:
    """ดึงภาพนิ่งจากวิดีโอที่เวลาที่กำหนด (วินาที) เป็นไฟล์ jpg เพื่อให้ Claude อ่านภาพดูเนื้อหาช็อตได้"""
    import subprocess
    out_dir = media.CACHE / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    src, outs = _abs(path), []
    for t in times:
        out = out_dir / f"{Path(src).stem}_{t:.2f}.jpg"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", src, "-frames:v", "1",
                        "-vf", f"scale={width}:-2", str(out)], check=True)
        outs.append(str(out))
    return outs


# ---------- rough cut ----------

@mcp.tool()
def analyze_speech(path: str, language: str = "th", min_pause: float = 0.25) -> str:
    """วิเคราะห์ไฟล์สำหรับ rough cut: แบ่งเป็น "unit" = ช่วงพูดระหว่างช่วงเงียบ (ขอบแม่นจากพลังงานเสียง)
    พร้อมข้อความของแต่ละ unit, ช่วงเงียบก่อนหน้า และ flag ช่วยตัดสินใจ:
      retake_of:N / retaken_by:N = พูดซ้ำกับ unit N, filler = คำเติม, no_words = หายใจ/เสียงรบกวน
    ท้ายผลลัพธ์มี reference transcript (ถอดต่อเนื่อง ตัวสะกดแม่นกว่าแต่เวลาหยาบ) ใช้อ่านประกอบ.
    ครั้งแรกใช้เวลา ~1/3 ของความยาวไฟล์ ครั้งต่อไปใช้ cache"""
    r = speech.analyze(_abs(path), language, min_pause=min_pause)
    lines = [f"file: {r['path']}", f"duration: {r['duration']}s  fps: {r['fps']}  levels: {r['levels']}",
             "id | start-end | pause_before | text | flags"]
    for u in r["units"]:
        flags = ",".join(u.get("flags", []))
        lines.append(f"{u['id']} | {u['start']:.2f}-{u['end']:.2f} | {u['pause_before']:.2f} | {u['text']}"
                     + (f" | {flags}" if flags else ""))
    lines += ["", "reference transcript:"] + r["reference_transcript"]
    return "\n".join(lines)


@mcp.tool()
def refine_speech(path: str, start: float, end: float, language: str = "th",
                  min_pause: float = 0.06) -> list:
    """ซูมดูช่วงเวลาสั้นๆ ให้ละเอียดขึ้น (แยกตามช่วงเงียบสั้นถึง 0.06s) เพื่อหาขอบคำ
    ใช้เมื่อต้องตัดกลาง unit เช่น เอาเฉพาะครึ่งหลังของประโยค หรือ unit ขึ้นต้นด้วยคำที่ขาด"""
    return speech.refine(_abs(path), start, end, language, min_pause)


def _layout(segments, pace):
    segs = [{**s, "path": _abs(s["path"])} for s in segments]
    fps = media.probe(segs[0]["path"]).get("fps") or 30
    return segs, speech.plan_layout(segs, fps, pace)


@mcp.tool()
def preview_rough_cut(segments: list[dict], pace: str = "natural", name: str = "preview") -> dict:
    """ต่อเสียงตามแผน rough cut เป็นไฟล์ .wav (ฟังได้ทันที) และถอดเสียงผลลัพธ์ เพื่อตรวจว่าประโยคต่อกันลื่น
    ไม่ขาดตอน ก่อนสร้าง timeline จริง. segments/pace เหมือน build_rough_cut"""
    _, layout = _layout(segments, pace)
    out = media.CACHE / "previews" / f"{name}.wav"
    result = speech.preview_audio(layout, out)
    result["warnings"] = layout["warnings"]
    return result


@mcp.tool()
def build_rough_cut(name: str, segments: list[dict], pace: str = "natural",
                    width: int | None = None, height: int | None = None,
                    punch_in: float | None = None, audio_fade_frames: int = 2) -> dict:
    """สร้าง rough cut ลง timeline ใหม่ (เรียกหลังผู้ใช้อนุมัติแผนแล้วเท่านั้น).

    segments: ลำดับช่วงพูดที่เลือก (วินาทีของไฟล์ต้นฉบับ, ใช้ขอบ unit จาก analyze_speech):
      [{"path": "...", "start": 14.09, "end": 15.55,
        "cut": "straight" | "L" | "J",   # วิธีตัดเข้าช่วงถัดไป (default straight = jump cut)
        "cut_frames": 6,                 # ระยะ J/L (เฟรม)
        "head": 0.1, "tail": 0.25}]      # (ไม่บังคับ) ระยะเผื่อหน้า/หลังคำพูด แทนค่าของ pace
    pace: tight | natural | relaxed = ระยะเผื่อมาตรฐาน (ช่องว่างระหว่างประโยค)
    punch_in: เช่น 1.1 = ซูมสลับช็อตที่เป็น jump cut จากกล้องเดียวกันเพื่อให้ดูต่อเนื่อง
    width/height ต่างสัดส่วนจากต้นฉบับ (เช่น 1080x1920 จากฟุตเทจ 16:9) จะซูมให้ภาพเต็มจออัตโนมัติ
    audio_fade_frames: ครอสเฟดเสียงสั้นๆ ทุกจุดต่อกันเสียงคลิก (0 = ปิด)
    เสียงถูกวางต่อเนื่องเสมอ; J/L ขยับเฉพาะจุดตัดภาพ (L = ภาพตัดก่อน เสียงเดิมพูดต่อ, J = ได้ยินเสียงถัดไปก่อนภาพ)
    """
    segs, layout = _layout(segments, pace)
    # a timeline with a different aspect (e.g. 9:16 from 16:9 footage) is filled, not letterboxed
    base = 1.0
    if width and height:
        src = media.probe(segs[0]["path"])
        if src.get("width") and src.get("height"):
            sx, sy = width / src["width"], height / src["height"]
            base = round(max(sx, sy) / min(sx, sy), 4)
    zooms, z = [], 1.0
    for i, v in enumerate(layout["video"]):
        same_shot = i > 0 and v["path"] == layout["video"][i - 1]["path"] \
            and (segs[i - 1].get("cut") or "straight") == "straight"
        z = (punch_in if z == 1.0 else 1.0) if (punch_in and same_shot) else 1.0
        zooms.append(round(base * z, 4))
    if all(x == 1.0 for x in zooms):
        zooms = None
    result = call("build_av_timeline", name=name, video=layout["video"], audio=layout["audio"],
                  fps=layout["fps"], width=width, height=height, zooms=zooms,
                  audio_fade_frames=audio_fade_frames, bin_name="Source")
    fps = layout["fps"]
    _save_segments(name, [{"path": a["path"], "start": a["src_in"] / fps, "end": a["src_out"] / fps}
                          for a in layout["audio"]])
    result["warnings"] = layout["warnings"]
    return result


# ---------- media pool & timelines ----------

@mcp.tool()
def list_media_pool() -> list:
    """รายการคลิปทั้งหมดใน Media Pool ของโปรเจกต์"""
    return call("list_media")


@mcp.tool()
def import_media(paths: list[str], bin_name: str | None = None) -> list:
    """นำไฟล์เข้า Media Pool (ใส่ bin ได้)"""
    return call("import_media", paths=[_abs(p) for p in paths], bin_name=bin_name)


@mcp.tool()
def list_timelines() -> list:
    """รายชื่อ timeline ในโปรเจกต์"""
    return call("list_timelines")


@mcp.tool()
def timeline_info(timeline: str | None = None, full: bool = False, track: str | None = None) -> dict:
    """ดูโครงสร้าง timeline: ขนาด, fps, ความยาว และสรุปแต่ละ track (จำนวนคลิป, ช่วงเวลา, ชื่อคลิปที่ใช้บ่อย,
    5 คลิปแรก/สุดท้าย). full=True หรือ track="V2" เพื่อดูคลิปทุกชิ้น (ของ track นั้น) - เวลาเป็นวินาที"""
    from collections import Counter
    info = call("timeline_info", timeline=timeline)
    if full:
        return info
    if track:
        return {k: v for k, v in info.items() if k != "tracks"} | {"track": track, "items": info["tracks"].get(track, [])}
    summary = {}
    for name, items in info["tracks"].items():
        if not items:
            summary[name] = {"clips": 0}
            continue
        names = Counter(i["name"] for i in items)
        summary[name] = {"clips": len(items), "from": items[0]["start"], "to": items[-1]["end"],
                         "covered_s": round(sum(i["end"] - i["start"] for i in items), 1),
                         "top_names": names.most_common(6), "first": items[:5], "last": items[-5:] if len(items) > 5 else []}
    return {k: v for k, v in info.items() if k != "tracks"} | {"tracks": summary}


@mcp.tool()
def build_timeline(name: str, segments: list[dict], width: int | None = None,
                   height: int | None = None, fps: float | None = None) -> dict:
    """สร้าง timeline ใหม่จากช่วงคลิปต้นฉบับเรียงตามลำดับ (ใช้ทั้งตัดคลิปสั้นและเรียงตามสคริปต์).
    เรียกหลังจากผู้ใช้อนุมัติแผนการตัดแล้วเท่านั้น.

    segments: [{"path": "/full/path.mp4", "start": 12.3, "end": 18.9}, ...] (วินาทีของไฟล์ต้นฉบับ)
    width/height: เช่น 1080x1920 สำหรับแนวตั้ง, 1920x1080 สำหรับแนวนอน
    fps: ใช้ได้เฉพาะโปรเจกต์ที่ยังไม่มี timeline (ค่าเริ่มต้น = fps ของคลิปแรก)
    """
    segs = [{**s, "path": _abs(s["path"])} for s in segments]
    result = call("build_timeline", name=name, segments=segs, width=width, height=height,
                  fps=fps, bin_name="Source")
    _save_segments(name, segs)
    return result


@mcp.tool()
def add_broll(items: list[dict], timeline: str | None = None, track: int = 2,
              fill_zoom: float | None = None) -> dict:
    """วาง B-roll (เฉพาะภาพ ไม่เอาเสียง) บน track วิดีโอด้านบน.

    items: [{"path": "/broll/city.mp4", "at": 5.0, "duration": 3.0, "src_start": 0}]
      at = วินาทีบน timeline, src_start = เริ่มจากวินาทีไหนของไฟล์ B-roll
    fill_zoom: ซูม B-roll บน track นั้น (เช่น 3.16 ให้ภาพ 16:9 เต็มจอแนวตั้ง 9:16) - ใช้ตามที่ผู้ใช้เลือก
    """
    items = [{**i, "path": _abs(i["path"])} for i in items]
    result = call("add_broll", items=items, timeline=timeline, track=track)
    if fill_zoom:
        result["transform"] = call("set_clip_transform", track=track, zoom=fill_zoom, timeline=timeline)
    return result


@mcp.tool()
def set_clip_transform(track: int = 2, zoom: float | None = None, pan: float | None = None,
                       tilt: float | None = None, timeline: str | None = None) -> dict:
    """ปรับ zoom / pan(x) / tilt(y) ให้ทุกคลิปใน track วิดีโอนั้น"""
    return call("set_clip_transform", track=track, zoom=zoom, pan=pan, tilt=tilt, timeline=timeline)


@mcp.tool()
def add_transitions(kind: str = "Cross Dissolve", frames: int = 12, track: int = 1,
                    timeline: str | None = None) -> dict:
    """ใส่ transition ทุกจุดต่อบน track วิดีโอ (ใช้เมื่อผู้ใช้สั่งเท่านั้น)"""
    return call("add_transitions", kind=kind, frames=frames, track=track, timeline=timeline)


@mcp.tool()
def add_markers(markers: list[dict], timeline: str | None = None) -> dict:
    """ใส่ marker บน timeline: [{"at": 10.5, "note": "ใส่ B-roll ตรงนี้", "color": "Blue"}]"""
    return call("add_markers", markers=markers, timeline=timeline)


# ---------- captions ----------

def _safe_name(text):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in text)[:60]


@mcp.tool()
def draft_captions(timeline: str | None = None, source_path: str | None = None, mode: str = "short",
                   max_chars: int | None = None) -> str:
    """ร่างซับไทยจากเสียงที่ใช้จริงบน timeline (ต้องเป็น timeline ที่สร้างด้วย build_rough_cut/build_timeline)
    หรือจากไฟล์วิดีโอที่ render แล้ว (source_path). เวลาเป็นวินาทีบน timeline.
    mode: short = ซับสั้น 1 บรรทัด ≤16 ตัวอักษร (คลิปสั้น) | long = ≤38 ตัวอักษร (คลิปยาว)
    ตัดคำไทยด้วย pythainlp: ไม่ตัดกลางคำ, ไม่ให้ ครับ/นะ ขึ้นต้นซับ.
    ข้อความมาจาก Whisper อาจสะกดผิด - ต้องตรวจแก้ตามบริบทก่อน render_captions"""
    caps = captions.draft(timeline=timeline, source_path=source_path and _abs(source_path), mode=mode,
                          max_chars=max_chars)
    lines = [f"{len(caps)} captions (mode={mode}) - id | start-end | text"]
    lines += [f"{c['id']} | {c['start']:.2f}-{c['end']:.2f} | {c['text']}" for c in caps]
    return "\n".join(lines)


def _timeline_dims(timeline, width=None, height=None, fps=None):
    if width and height and fps:
        return int(width), int(height), float(fps)
    info = call("timeline_info", timeline=timeline)
    return int(info["width"]), int(info["height"]), float(info["fps"])


@mcp.tool()
def preview_captions(timeline: str, captions_to_show: list[dict], mode: str = "short",
                     style: str | None = None, overrides: dict | None = None) -> dict:
    """เรนเดอร์ภาพนิ่งตัวอย่างซับบนเฟรมจริงของ timeline (สูงสุด 4 ซับ ต่อกันเป็นแผ่นเดียว) เพื่อเช็คสไตล์,
    ขนาด, ตำแหน่ง ว่าไม่ล้นจอและไม่บังหน้า/จุดโฟกัส. คลิปแนวตั้งจะมีแถบแดง = พื้นที่ที่ปุ่มของ Shorts/TikTok บัง.
    ใช้ Read เปิดดูภาพที่ได้. captions_to_show: [{"id","start","end","text"}] (ใส่ *คำ* เพื่อเน้น)
    style/overrides เหมือน render_captions"""
    W, H, fps = _timeline_dims(timeline)
    style = style or ("pop" if mode == "short" else "clean")
    out_dir = captions.OUT_ROOT / _safe_name(timeline)
    out_dir.mkdir(parents=True, exist_ok=True)
    caps = []
    for c in captions_to_show[:4]:
        mid = (float(c["start"]) + float(c["end"])) / 2
        frame = captions.frame_at(timeline, mid, out_dir / f"frame_{c.get('id', 0)}.jpg")
        caps.append({"id": int(c.get("id", len(caps) + 1)), "start": float(c["start"]), "end": float(c["end"]),
                     "text": c["text"], "frame_path": frame})
    job = {"preview": True, "width": W, "height": H, "fps": fps, "captions": caps,
           "style": captions.resolve_style(style, W, H, overrides),
           "out": str(out_dir / f"preview_{style}_{int(time.time())}.jpg")}
    return captions.run_renderer(job)


@mcp.tool()
def render_captions(timeline: str, captions_list: list[dict], mode: str = "short", style: str | None = None,
                    overrides: dict | None = None, output: str | None = None) -> dict:
    """ใส่ซับไทยลง timeline (เรียกหลังผู้ใช้อนุมัติข้อความและสไตล์แล้ว).

    captions_list: [{"id": 1, "start": 0.08, "end": 1.06, "text": "มีชายคนหนึ่ง"}, ...] เวลาเป็นวินาทีบน timeline
      ใส่ *คำ* เพื่อเน้นคำสำคัญ (สี emph + ขยาย) - ใช้กับคลิปสั้น 1 คำต่อ 1-2 ซับ
    mode: short | long
    output: "overlay" = เรนเดอร์ซับมีแอนิเมชันเป็นคลิปใส (ProRes 4444) 1 ไฟล์ต่อ 1 ซับ วางบน track "Captions"
              (เรนเดอร์ซ้ำจะแทนที่ของเดิม) - ค่าเริ่มต้นของ short
            "track" = ไฟล์ .srt ใส่ subtitle track ของ Resolve (แก้ข้อความใน Resolve ได้, export ขึ้น YouTube ได้)
              - ค่าเริ่มต้นของ long
            "both"
    style: clean (คลิปยาว ขาวขอบดำ+เงา ไม่มีแอนิเมชัน) | pop (คลิปสั้น เด้งเข้า คำที่พูดอยู่สีเหลืองขยาย คำเน้นสีแดง)
      | karaoke (คำที่ยังไม่พูดเป็นสีเทา สว่างตามเสียง สไลด์ขึ้น คำเน้นสีเขียว) | box (กล่องสีสลับทุกซับ เด้งเข้า)
    overrides (ไม่บังคับ): font (ชื่อไฟล์ฟอนต์ เช่น Kanit-Black, Prompt-Bold, NotoSansThai-Bold), size_scale,
      color, highlight_color, emph_color, stroke_color, y (0-1 ตำแหน่งกึ่งกลางซับจากบน), max_width (0-1),
      enter (pop|slide|fade|none), highlight (word|karaoke|null), word_pop, box, box_colors, shadow
    """
    W, H, fps = _timeline_dims(timeline)
    style = style or ("pop" if mode == "short" else "clean")
    output = output or ("overlay" if mode == "short" else "track")
    caps = [{"id": int(c.get("id", i + 1)), "start": float(c["start"]), "end": float(c["end"]), "text": c["text"]}
            for i, c in enumerate(captions_list)]
    caps.sort(key=lambda c: c["start"])
    st = captions.resolve_style(style, W, H, overrides)
    out_dir = captions.OUT_ROOT / _safe_name(timeline) / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    srt = captions.write_srt(caps, out_dir / f"{_safe_name(timeline)}.srt", st["max_chars"], st["max_lines"])
    (out_dir / "captions.json").write_text(json.dumps(caps, ensure_ascii=False, indent=1), encoding="utf-8")
    result = {"srt": srt, "captions": len(caps), "style": style, "frame": f"{W}x{H}@{fps}"}
    if output in ("track", "both"):
        result["subtitle_track"] = call("import_subtitles", srt_path=srt, timeline=timeline)
    if output in ("overlay", "both"):
        t0 = time.time()
        rendered = captions.run_renderer({"width": W, "height": H, "fps": fps, "style": st, "captions": caps,
                                          "out_dir": str(out_dir)})
        result["render_seconds"] = round(time.time() - t0, 1)
        result["overlay"] = call("add_overlays", timeline=timeline, items=rendered["clips"],
                                 track_name="Captions", bin_name="Captions")
    return result


# ---------- B-roll / inserts ----------

@mcp.tool()
def search_broll(query: str, kind: str = "any", sources: list[str] | None = None, limit: int = 8,
                 allow_fair_use: bool = False) -> dict:
    """ค้นหาภาพ/วิดีโอ/ฟุตเทจเก่า/มีม สำหรับ B-roll จากแหล่งที่บอกลิขสิทธิ์ชัด (ใช้คำค้นภาษาอังกฤษจะได้ผลดีกว่า).
    kind: image | video | any. sources (ค่าเริ่มต้น = ทั้งหมดที่ใช้ได้):
      openverse (ภาพ CC จาก Flickr/พิพิธภัณฑ์), archive (Internet Archive: ฟุตเทจเก่า/ข่าวเก่า),
      nasa (public domain), imgflip (เทมเพลตมีม - ต้อง allow_fair_use), pexels/pixabay (ถ้ามี API key)
    ระดับสิทธิ์: A = ใช้ได้เสรี, B = ต้องให้เครดิต, C = fair use/ไม่ระบุสิทธิ์ (ใช้น้อยๆ เพื่อวิจารณ์/ประกอบเท่านั้น).
    NC/ND ถูกตัดทิ้งเสมอ (ช่องที่หารายได้ใช้ไม่ได้).
    คืนรายการ + contact_sheet (ภาพ thumbnail มีเลข id) - ใช้ Read เปิดดูเพื่อเลือกภาพที่ตรงเนื้อหาจริง"""
    cands, errors = broll.search(query, kind, sources, limit, allow_fair_use)
    sheet = broll.contact_sheet(cands, media.CACHE / "broll" / f"sheet_{int(time.time())}.jpg") if cands else None
    rows = [f"{x['id']} | {x['kind']} | [{x['class']}] {x['license']} | {x['source']} | {x['title'][:70]}"
            + (f" | {x.get('width')}x{x.get('height')}" if x.get("width") else "")
            + (f" | {x['duration']}s" if x.get("duration") else "") for x in cands]
    return {"results": rows, "contact_sheet": sheet, "errors": errors}


@mcp.tool()
def fetch_broll(ids: list[str], folder: str | None = None) -> dict:
    """ดาวน์โหลดไฟล์ที่ผู้ใช้อนุมัติแล้ว (id จาก search_broll) ไปไว้ใน ~/Movies/DaVinci Bot B-roll
    (หรือ folder) พร้อมเขียนเครดิตลง CREDITS.md. ต้องให้ผู้ใช้อนุมัติรายการ (ชื่อ, แหล่ง, สิทธิ์) ก่อนเรียกเสมอ"""
    return broll.fetch(ids, folder)


def _broll_items(items):
    out = []
    for it in items:
        it = dict(it)
        cand = broll.candidate(it["id"]) if it.get("id") else None
        if not it.get("path"):
            if not cand or not cand.get("local_path"):
                raise ValueError(f"item {it.get('id')} has no local file - fetch_broll first or give path")
            it["path"] = cand["local_path"]
        it["path"] = _abs(it["path"])
        if not it.get("kind"):
            ext = Path(it["path"]).suffix.lower()
            it["kind"] = "video" if ext in media.VIDEO_EXT or ext in (".gif", ".webm", ".ogv") else "image"
        credit = it.get("credit")
        if credit is None:
            credit = bool(cand and cand["class"] in ("B", "C"))
        if credit is True:
            it["credit"] = broll.short_credit(cand) if cand else None
        elif credit is False:
            it["credit"] = None
        out.append(it)
    return out


@mcp.tool()
def preview_broll(timeline: str, items: list[dict]) -> dict:
    """ภาพนิ่งตัวอย่าง B-roll (สูงสุด 4 ชิ้น) วางทับเฟรมจริงของ timeline ณ เวลานั้น เพื่อเช็คว่าตรงเนื้อหา,
    ไม่บังหน้าผิดจังหวะ, layout เหมาะ. items รูปแบบเดียวกับ place_broll. ใช้ Read เปิดดู"""
    W, H, fps = _timeline_dims(timeline)
    out_dir = media.CACHE / "broll" / "preview"
    its = _broll_items(items[:4])
    for i, it in enumerate(its):
        it["frame_path"] = captions.frame_at(timeline, (float(it["start"]) + float(it["end"])) / 2,
                                             out_dir / f"tl_{i}.jpg")
    job = {"preview": True, "width": W, "height": H, "fps": fps, "items": its, "out_dir": str(out_dir),
           "out": str(out_dir / f"preview_{int(time.time())}.jpg")}
    return captions.run_renderer(job, "davinci_bot.broll_render")


@mcp.tool()
def place_broll(timeline: str, items: list[dict]) -> dict:
    """เตรียมและวาง B-roll/insert/มีม ลง track "B-roll" (อยู่ใต้ track Captions เสมอ; เรียกซ้ำ = แทนที่ทั้ง track)
    เรียกหลังผู้ใช้อนุมัติแผนแล้ว.

    items: [{"id": "op1e9fe196" หรือ "path": "/local/file.jpg",
             "start": 12.9, "end": 15.3,          # วินาทีบน timeline
             "layout": "full" | "pip" | "top" | "meme",
             "motion": "in" | "out" | "left" | "right" | "none",   # Ken Burns ของภาพนิ่ง
             "src_start": 4.0,                    # วิดีโอ: เริ่มจากวินาทีไหนของไฟล์
             "credit": true/false/"ข้อความเอง",    # ค่าเริ่มต้น: ใส่ให้อัตโนมัติถ้าสิทธิ์ระดับ B/C
             "box": [cx, cy, w, h]}]              # (ไม่บังคับ) ตำแหน่ง/ขนาดการ์ดเป็นสัดส่วนของจอ
    full = เต็มจอ (ภาพนิ่ง Ken Burns, สัดส่วนต่าง = พื้นหลังเบลอ), pip = การ์ดข้างคนพูด,
    top = การ์ดด้านบนของคลิปแนวตั้ง, meme = การ์ดกลางจอเด้งเอียง"""
    W, H, fps = _timeline_dims(timeline)
    its = sorted(_broll_items(items), key=lambda x: float(x["start"]))
    out_dir = media.CACHE / "broll" / _safe_name(timeline) / time.strftime("%Y%m%d-%H%M%S")
    t0 = time.time()
    rendered = captions.run_renderer({"width": W, "height": H, "fps": fps, "items": its, "out_dir": str(out_dir)},
                                     "davinci_bot.broll_render")
    placed = call("add_overlays", timeline=timeline, items=rendered["clips"], track_name="B-roll",
                  keep_above=["Captions"], bin_name="B-roll")
    credits = sorted({broll.credit_line(broll.candidate(it["id"])) for it in its
                      if it.get("id") and broll.candidate(it["id"])})
    (out_dir / "CREDITS.txt").write_text("\n".join(credits), encoding="utf-8")
    return {"placed": placed, "clips": len(rendered["clips"]), "render_seconds": round(time.time() - t0, 1),
            "credits_for_description": credits, "credits_file": str(out_dir / "CREDITS.txt")}


# ---------- music & SFX ----------

SFX_LEVELS = {"loud": 0.0, "normal": -4.0, "soft": -9.0, "subtle": -14.0}


@mcp.tool()
def audio_library() -> str:
    """คลังเสียงที่วิเคราะห์แล้ว (ตั้งค่าโฟลเดอร์ที่ library.json ในโปรเจกต์ davinci-bot).
    SFX: dur, sync = วินาทีที่เสียงกระแทก/พีค (จุดที่ต้องตรงกับจังหวะ), shape (hit = กระแทกทันที,
      swell = ค่อยๆ ดังขึ้นไปจบที่พีค ให้ align "end", short/long), loud_db, tone, roles (คำใบ้จากชื่อไฟล์)
    เพลง: dur, bpm (อาจเป็นครึ่งหนึ่งของจังหวะจริง), first_beat, beat_sec, energy_2s (กราฟพลังงานทุก 2 วิ),
      energy_jumps_at (วินาทีที่เพลงพุ่ง/ดรอป เหมาะให้ตรงกับจุดหักมุม), drops_to_quiet_at"""
    lib = audio.scan_library()
    lines = [f"config: {lib['config']}", f"sfx dirs: {lib['paths']['sfx']}", f"music: {lib['paths']['music']}", "",
             "SFX: name | dur | sync | shape | loud_db | tone | roles"]
    lines += [f"{x['name']} | {x['dur']} | {x['sync']} | {x['shape']} | {x['loud_db']} | {x['tone']} | "
              f"{','.join(x['roles'])}" for x in lib["sfx"]]
    lines += ["", "MUSIC: name | dur | bpm | first_beat | energy (2s/char) | jumps | quiet"]
    lines += [f"{m['name']} | {m['dur']} | {m['bpm']} | {m['first_beat']} | {m['energy_2s']} | "
              f"{m['energy_jumps_at']} | {m['drops_to_quiet_at']}" for m in lib["music"]]
    return "\n".join(lines)


@mcp.tool()
def sfx_cue_map(timeline: str) -> str:
    """แผนที่จังหวะของ timeline สำหรับวาง SFX/เพลง (เวลาเป็นวินาที): ทุกประโยคพร้อมช่วงเงียบหลังประโยค
    (pause ยาว = ที่ว่างให้ตบมุก/ให้อารมณ์ค้าง), จุดตัดภาพ, จุดที่ B-roll/มีมเข้า, คำที่เน้นในซับ"""
    segs = captions._timeline_segments(timeline)
    total = sum(s["end"] - s["start"] for s in segs)
    events = []
    caps = captions.draft(timeline=timeline, mode="long")
    for i, c in enumerate(caps):
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else total
        events.append((c["start"], f"SAY {c['start']:.2f}-{c['end']:.2f} \"{c['text']}\" pause_after={nxt - c['end']:.2f}"))
    try:
        info = call("timeline_info", timeline=timeline)
        v1 = info["tracks"].get("V1", [])
        for it in v1[1:]:
            events.append((it["start"], f"CUT {it['start']:.2f}"))
        names = {}
        for k, items in info["tracks"].items():
            if k.startswith("V") and k != "V1":
                for it in items:
                    names.setdefault(it["start"], f"OVERLAY-IN {it['start']:.2f}-{it['end']:.2f} ({k}) {it['name']}")
        events += list(names.items())
    except Exception as e:  # cue map still useful from speech alone
        events.append((0, f"(timeline tracks unavailable: {e})"))
    cap_root = captions.OUT_ROOT / _safe_name(timeline)
    latest = sorted(cap_root.glob("*/captions.json")) if cap_root.exists() else []
    if latest:
        for c in json.loads(latest[-1].read_text()):
            for w in __import__("re").findall(r"\*([^*]+)\*", c["text"]):
                events.append((c["start"], f"EMPHASIS {c['start']:.2f} \"{w}\""))
    events.sort(key=lambda e: e[0])
    return f"timeline {timeline}: {total:.2f}s\n" + "\n".join(e[1] for e in events)


def _plan_audio(timeline, music, sfx):
    segs = captions._timeline_segments(timeline)
    total = sum(s["end"] - s["start"] for s in segs)
    key = __import__("hashlib").sha1(json.dumps(segs).encode()).hexdigest()[:10]
    work = media.CACHE / "audio" / f"{_safe_name(timeline)}-{key}"
    work.mkdir(parents=True, exist_ok=True)
    voice = work / "voice.wav"
    if not voice.exists():
        audio.voice_wav(segs, voice)
    speech_db = audio.speech_level_db(str(voice))
    stamp = time.strftime("%H%M%S")
    music_layers, sfx_layers = [], []
    for i, m in enumerate(music or []):
        info = audio.find(m["music"], "music")
        start = float(m.get("start", 0))
        end = float(m.get("end", total))
        length = end - start
        src = float(m.get("src_start", 0))
        if m.get("hit_at"):  # land a moment of the song (e.g. an energy jump) on a timeline moment
            src = float(m["hit_at"]["music_time"]) - (float(m["hit_at"]["timeline_time"]) - start)
            src = max(0.0, src)
        gain = round(speech_db + float(m.get("level", -12)) - info["loud_db"], 1)
        fi, fo = float(m.get("fade_in", 1.0)), float(m.get("fade_out", 2.0))
        if m.get("duck", True):
            bed = audio.ducked_bed(info["path"], src, length, str(voice), start, work / f"music_{stamp}_{i}.wav",
                                   float(m.get("duck_db", 10)), fi, fo)
            music_layers.append({"path": bed, "at": start, "src_in": 0, "src_out": length, "gain_db": gain,
                                 "fade_in": 0, "fade_out": 0, "name": info["name"]})
        else:
            music_layers.append({"path": info["path"], "at": start, "src_in": src,
                                 "src_out": min(info["dur"], src + length), "gain_db": gain,
                                 "fade_in": fi, "fade_out": fo, "name": info["name"]})
    for x in sfx or []:
        info = audio.find(x["sfx"], "sfx")
        align = x.get("align") or ("end" if info["shape"] == "swell" else "sync")
        anchor = {"sync": info["sync"], "end": info["sound_end"], "start": info["sound_start"]}[align]
        at = float(x["at"]) - anchor
        src_in = 0.0
        if at < 0:
            src_in, at = -at, 0.0
        src_out = info["dur"]
        if x.get("max_len"):
            src_out = min(src_out, anchor + float(x["max_len"]))
        lvl = x.get("level", "normal")
        offset = SFX_LEVELS.get(lvl, lvl if isinstance(lvl, (int, float)) else -4.0)
        gain = round(max(-30.0, min(10.0, speech_db + offset - info["loud_db"])), 1)
        fade_out = float(x.get("fade_out", 0.15 if src_out < info["dur"] else 0))
        sfx_layers.append({"path": info["path"], "at": round(at, 3), "src_in": round(src_in, 3),
                           "src_out": round(src_out, 3), "gain_db": gain, "fade_in": 0, "fade_out": fade_out,
                           "name": info["name"], "cue": float(x["at"])})
    # overlapping SFX go to SFX 2, SFX 3 ... (a track cannot hold overlapping clips)
    lanes = []
    for l in sorted(sfx_layers, key=lambda l: l["at"]):
        end = l["at"] + l["src_out"] - l["src_in"]
        for lane in lanes:
            if lane["end"] <= l["at"] + 1e-3:
                lane["items"].append(l)
                lane["end"] = end
                break
        else:
            lanes.append({"items": [l], "end": end})
    return {"work": work, "voice": str(voice), "speech_db": round(speech_db, 1), "total": total,
            "music": music_layers, "sfx_lanes": [ln["items"] for ln in lanes]}


@mcp.tool()
def preview_audio_mix(timeline: str, music: list[dict] | None = None, sfx: list[dict] | None = None) -> dict:
    """มิกซ์ตัวอย่าง (เสียงพูด + เพลงที่ duck แล้ว + SFX) เป็นไฟล์ mp3 ให้ผู้ใช้กดฟังก่อนวางจริง.
    พารามิเตอร์เหมือน place_audio"""
    plan = _plan_audio(timeline, music, sfx)
    layers = plan["music"] + [l for lane in plan["sfx_lanes"] for l in lane]
    out = plan["work"] / f"mix_preview_{time.strftime('%H%M%S')}.mp3"
    audio.mix_preview(plan["voice"], layers, out, plan["total"])
    return {"preview_mp3": str(out), "speech_db": plan["speech_db"],
            "music": [(l["name"], l["at"], round(l["at"] + l["src_out"] - l["src_in"], 2), l["gain_db"])
                      for l in plan["music"]],
            "sfx": [(l["name"], l["cue"], l["gain_db"]) for lane in plan["sfx_lanes"] for l in lane],
            "sfx_tracks_needed": len(plan["sfx_lanes"])}


@mcp.tool()
def place_audio(timeline: str, music: list[dict] | None = None, sfx: list[dict] | None = None) -> dict:
    """วางเพลงประกอบลง audio track "Music" และ SFX ลง "SFX" (+ "SFX 2"... ถ้าซ้อนกัน) - เรียกซ้ำ = แทนที่ track เหล่านั้น
    (ส่ง music=None เพื่อไม่แตะ track Music). เรียกหลังผู้ใช้อนุมัติแผนแล้ว. เวลาเป็นวินาทีบน timeline.

    music: [{"music": "ชื่อเพลงจาก audio_library", "start": 0, "end": 29.4,
             "src_start": 0,                         # เริ่มจากวินาทีไหนของเพลง
             "hit_at": {"music_time": 32.0, "timeline_time": 15.6},   # (แทน src_start) ให้จุดพุ่งของเพลงตรงจุดหักมุม
             "level": -12,                           # dB เทียบกับเสียงพูด (ช่วงไม่มีเสียงพูด)
             "duck": true, "duck_db": 10,            # ลดเพลงอัตโนมัติตอนพูด
             "fade_in": 1.0, "fade_out": 2.0}]
    sfx:   [{"sfx": "vine-boom", "at": 15.62,         # วินาทีที่ "จังหวะ" ต้องตรง (คำ/จุดตัด/ภาพเข้า)
             "align": "sync" | "end" | "start",      # sync = จุดกระแทกของเสียงตรงกับ at (swell ใช้ end อัตโนมัติ)
             "level": "loud" | "normal" | "soft" | "subtle" หรือ dB เทียบเสียงพูด,
             "max_len": 1.2}]                        # ตัดหางเสียงที่ยาวเกิน (มี fade ให้)"""
    plan = _plan_audio(timeline, music, sfx)
    tracks = []
    if music is not None:
        tracks.append({"name": "Music", "items": [
            {k: l[k] for k in ("path", "at", "src_in", "src_out", "fade_in", "fade_out")} | {"volume_db": l["gain_db"]}
            for l in plan["music"]]})
    if sfx is not None:
        lanes = plan["sfx_lanes"] or [[]]
        for i, lane in enumerate(lanes):
            tracks.append({"name": "SFX" if i == 0 else f"SFX {i + 1}", "items": [
                {k: l[k] for k in ("path", "at", "src_in", "src_out", "fade_in", "fade_out")}
                | {"volume_db": l["gain_db"]} for l in lane]})
    placed = call("place_audio", timeline=timeline, tracks=tracks, bin_name="Audio")
    return {"placed": placed, "speech_db": plan["speech_db"]}


# ---------- looks (grade presets) ----------

LOOKS_DIR = Path(__file__).resolve().parent / "presets"


RESOLVE_LUT_DIR = Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/davinci-bot")


def _all_looks():
    return {x.stem: x for x in sorted(LOOKS_DIR.glob("*.drx"))} | \
        {x.stem: x for x in sorted((LOOKS_DIR / "luts").glob("*.cube"))}


def _look_path(look):
    p = Path(look).expanduser()
    if p.suffix.lower() in (".drx", ".cube") and p.exists():
        return str(p.resolve())
    looks = _all_looks()
    if look not in looks:
        raise ValueError(f"look not found: {look} (available: {list(looks)})")
    return str(looks[look])


def _apply(timeline, look, track, items):
    path = _look_path(look)
    if path.endswith(".cube"):
        installed = RESOLVE_LUT_DIR / Path(path).name  # Resolve resolves LUTs from its own LUT folders
        RESOLVE_LUT_DIR.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(Path(path).read_bytes())
        return call("apply_lut", timeline=timeline, path=str(installed), track=track, items=items)
    return call("apply_grade", timeline=timeline, path=path, track=track, items=items)


def _clip_index_at(timeline, t, track=1):
    items = call("timeline_info", timeline=timeline)["tracks"].get(f"V{track}", [])
    for i, it in enumerate(items, 1):
        if it["start"] <= t < it["end"]:
            return i
    raise ValueError(f"no clip on V{track} at {t}s")


@mcp.tool()
def list_looks() -> list:
    """preset แต่งสี/แต่งหน้าที่บันทึกไว้:
    natural-skin (.drx, Resolve) = ผิวเนียน+ใส: qualifier ผิว + วงรีรอบหัว + Mid/Detail -55 + blur เบาๆ + gamma +0.03
    natural-skin-tone (.cube LUT) = เฉพาะ "ผิวใส" (สว่างขึ้น ลดแดง โทนพีช) ไม่มีผิวเนียน - ใช้ได้ทั้ง Resolve และ CapCut"""
    return [f"{k} ({v.suffix})" for k, v in _all_looks().items()]


@mcp.tool()
def preview_look(timeline: str, look: str, at: float, track: int = 1) -> dict:
    """ใส่ look ให้คลิปเดียว ณ วินาที at แล้วคืนภาพเทียบ ก่อน | หลัง (ครอปบริเวณหน้า + ทั้งเฟรม) ให้ใช้ Read ดู.
    คลิปนั้นจะคง look ไว้ (ถ้าผู้ใช้ไม่เอา ให้ reset_look)"""
    from PIL import Image
    out = media.CACHE / "looks"
    out.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    before, after = out / f"{stamp}_before.png", out / f"{stamp}_after.png"
    idx = _clip_index_at(timeline, at, track)
    call("export_frame", timeline=timeline, at=at, path=str(before))
    res = _apply(timeline, look, track, [idx])
    call("export_frame", timeline=timeline, at=at, path=str(after))
    A, B = Image.open(before).convert("RGB"), Image.open(after).convert("RGB")
    W, H = A.size
    box = (int(W * 0.3), int(H * 0.08), int(W * 0.7), int(H * 0.72)) if W > H else (0, int(H * 0.1), W, int(H * 0.6))
    fa, fb = A.crop(box), B.crop(box)
    sheet = Image.new("RGB", (fa.width * 2 + 12, fa.height), (20, 20, 20))
    sheet.paste(fa, (0, 0))
    sheet.paste(fb, (fa.width + 12, 0))
    sheet.thumbnail((1600, 1600))
    path = out / f"{stamp}_compare.jpg"
    sheet.save(path, quality=90)
    return {"compare": str(path), "clip_index": idx, "applied": res}


@mcp.tool()
def apply_look(timeline: str, look: str, track: int = 1, clip_indexes: list[int] | None = None) -> dict:
    """ใส่ look ให้ทุกคลิปบน video track (หรือเฉพาะ clip_indexes เริ่มที่ 1).
    .drx = แทนที่เกรดเดิมของคลิป, .cube = ใส่ LUT ที่ node 1 (คงค่าอื่นใน node ไว้).
    natural-skin ทำมาสำหรับกล้องตั้งนิ่ง คนนั่งกลางเฟรม (วงรีจำกัดบริเวณหัว) - ถ้ามุมกล้องต่างมาก ให้ preview_look ก่อน"""
    return _apply(timeline, look, track, clip_indexes)


@mcp.tool()
def reset_look(timeline: str, track: int = 1) -> dict:
    """ล้างเกรดสีทั้งหมดของคลิปบน track (กลับเป็นภาพต้นฉบับ)"""
    return call("reset_grade", timeline=timeline, track=track)


# ---------- creator analysis ----------

@mcp.tool()
def creator_footage_metrics(folder: str, max_files: int = 6) -> dict:
    """วัดการนำเสนอจากฟุตเทจพูดหน้ากล้อง (เลือกไฟล์กระจายทั้ง session, ข้ามไฟล์ที่ไม่มีเสียง):
    speech_ratio (เวลาพูด/เวลาอัด), retake_ratio (สัดส่วนช่วงพูดที่เป็นการพูดซ้ำ), words_per_min_speech,
    ช่วงเงียบ, snr_db, loudness_range_db, f0_median_hz, pitch_range_semitones (ช่วงเสียงสูงต่ำ = ความมีชีวิตชีวา)
    + ภาพตัวอย่างเฟรม (frames_sheet) ให้ใช้ Read ดู framing/แสง/สายตา. ครั้งแรกใช้เวลา ~1/3 ของความยาวไฟล์"""
    files = [f for f in sorted(Path(folder).expanduser().iterdir()) if f.suffix.lower() in media.VIDEO_EXT]
    with_audio = [f for f in files if media.probe(str(f)).get("has_audio")]
    if len(with_audio) > max_files:
        step = (len(with_audio) - 1) / (max_files - 1)
        with_audio = [with_audio[round(i * step)] for i in range(max_files)]
    metrics = []
    for f in with_audio:
        try:
            metrics.append(creator.file_metrics(str(f)))
        except Exception as e:
            metrics.append({"file": f.name, "error": str(e)[:200]})
    sheet = creator.frames_sheet(with_audio, media.CACHE / "creator" / f"frames_{_safe_name(Path(folder).name)}.jpg")
    return {"files_total": len(files), "files_with_audio": len([f for f in files if media.probe(str(f)).get("has_audio")]),
            "analyzed": metrics, "frames_sheet": sheet}


@mcp.tool()
def practice_feedback(path: str, script_text: str | None = None, script_path: str | None = None) -> dict:
    """ให้ฟีดแบ็กไฟล์อัดฝึก/เทคจริง เทียบกับค่าเดิมของ creator (reports/metrics.json) และเป้าหมายในแผนฝึก:
    speech_ratio, retake_ratio, ช่วงเงียบยาวต่อ 10 นาที, ความเร็วพูด, ช่วงเสียงสูงต่ำ, SNR (pass/fail).
    ถ้าให้ script_text หรือ script_path (ตัดเฉพาะส่วนที่อัด) จะบอก % ที่พูดตรงบท และวลีที่ข้าม/เปลี่ยน"""
    text = script_text or (Path(script_path).expanduser().read_text(encoding="utf-8") if script_path else None)
    return creator.practice_feedback(_abs(path), text)


@mcp.tool()
def make_prompter(script_path: str, out_dir: str | None = None, max_chars: int = 34) -> dict:
    """สร้าง teleprompter จากสคริปต์ช่อง (.md ที่มีหัวฉาก '## [ฉาก N] ...'): ดึงเฉพาะบทพูด (ตัด [ภาพ:...],
    หมายเหตุ, Shorts, fact-sheet), ตัดบรรทัดตามจังหวะหายใจ (~max_chars ตัวอักษร), ตัวเลขตัวหนา.
    ได้ <เรื่อง>_prompter.html (เลื่อนอัตโนมัติ, Space เริ่ม/หยุด, ↑↓ ความเร็ว, +/− ขนาด, M กลับด้านสำหรับกระจก)
    และ .txt - บันทึกข้างสคริปต์ (หรือ out_dir) พร้อมนับความยาวบทพูดจริง (600 ตัวอักษร/นาที)
    และ hook_check: ประโยคแรกต้องเป็น "วันนี้เราจะมาพูดถึงเรื่อง..." และประโยคสุดท้ายของ hook ต้องส่งเข้าเรื่อง
    ("เดี๋ยวเราไปดูกันครับ", "ไปฟังกันเลยครับ" ฯลฯ)"""
    return prompter.build(_abs(script_path), out_dir and _abs(out_dir), max_chars)


@mcp.tool()
def script_digest(path: str) -> dict:
    """สรุปโครงสคริปต์/เอกสาร .md ยาวๆ: จำนวนคำ, เวลาโดยประมาณ, หัวข้อ/ฉากทั้งหมด, คำถามในบท, ตอนเปิด และตอนจบ"""
    return creator.script_digest(_abs(path))


# ---------- render ----------

@mcp.tool()
def open_page(page: str) -> bool:
    """สลับหน้าใน Resolve: media, cut, edit, fusion, color, fairlight, deliver"""
    return call("open_page", page=page)


@mcp.tool()
def list_render_presets() -> list:
    """รายชื่อ render preset ในหน้า Deliver"""
    return call("list_render_presets")


@mcp.tool()
def render(target_dir: str, filename: str, timeline: str | None = None,
           preset: str | None = None, start: bool = False) -> dict:
    """เพิ่มงาน render ของ timeline ลงคิว (start=True เพื่อเริ่ม render ทันที)"""
    return call("render", target_dir=_abs(target_dir), filename=filename, timeline=timeline,
                preset=preset, start=start)


@mcp.tool()
def render_status(job: str) -> dict:
    """เช็คสถานะงาน render"""
    return call("render_status", job=job)


if __name__ == "__main__":
    mcp.run()
